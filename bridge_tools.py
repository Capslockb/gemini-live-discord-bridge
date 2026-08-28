"""Extracted from bridge.py — part of the gemini-live-discord-bridge split. Do not edit in isolation; see bridge.py facade."""
import ast
import asyncio
import base64
import html
import http.client as http_client
import ipaddress
import json
import logging
import os
import queue
import random
import re
import secrets
import socket
import ssl
import subprocess
import sys
import time
import wave
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from typing import Any, Optional, Dict, List, Callable, Tuple

import numpy as np
logger = logging.getLogger("voice-live")
from bridge_config import GITHUB_VOICE_TOOLS_ENABLED, GOOGLE_API_BIN, NOTES_DIR, _GH_BIN, _NOTES_PATH
from bridge_email import _autocorrect_email_address
from bridge_opencode import _bridge_user_id, _opencode_get_bridge
import delegation_agent as _local_delegation_agent
def _get_bridge():
    """Lazy accessor for the global BRIDGE singleton (lives in bridge_http).

    Deferred import avoids a circular import: bridge_http -> bridge_core -> bridge_tools.
    """
    import bridge_http
    return bridge_http.BRIDGE


def _run_github_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """GitHub repo tracker tools (criterion #22). Wraps the `gh` CLI.

    Read-only by default; only `local_github_note` (local file append)
    and `local_github_issue_create` (network call) are write operations.
    """
    import subprocess
    if not GITHUB_VOICE_TOOLS_ENABLED:
        return {"error": "GitHub voice tools disabled (DISCORD_VOICE_LIVE_GITHUB_TOOLS=false)"}
    if not Path(_GH_BIN).exists():
        return {"error": f"gh CLI not found at {_GH_BIN}"}
    if name == "local_github_repo_list":
        try:
            limit = min(max(int(args.get("limit", 20)), 1), 50)
        except (TypeError, ValueError):
            limit = 20
        try:
            out = subprocess.run(
                [_GH_BIN, "repo", "list", "--json",
                 "name,owner,description,visibility,isPrivate,updatedAt",
                 "--limit", str(limit)],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode != 0:
                return {"error": f"gh repo list failed: {out.stderr[:200]}"}
            try:
                repos = json.loads(out.stdout)
            except json.JSONDecodeError as exc:
                return {"error": f"gh repo list parse failed: {exc}"}
            return {"result": {
                "count": len(repos),
                "repos": [
                    {
                        "name": r.get("name"),
                        "full_name": f"{r.get('owner', {}).get('login', '?')}/{r.get('name', '?')}",
                        "description": (r.get("description") or "")[:200],
                        "private": r.get("isPrivate", False),
                        "updated_at": r.get("updatedAt", ""),
                    } for r in repos
                ],
            }}
        except subprocess.TimeoutExpired:
            return {"error": "gh repo list timed out"}
        except Exception as exc:
            return {"error": f"gh repo list crashed: {exc}"}

    if name == "local_github_issues":
        repo = args.get("repo", "").strip()
        if not repo:
            return {"error": "repo is required (e.g. 'Capslockb/gemini-live-discord-bridge')"}
        state = args.get("state", "open").strip() or "open"
        try:
            limit = min(max(int(args.get("limit", 15)), 1), 50)
        except (TypeError, ValueError):
            limit = 15
        try:
            out = subprocess.run(
                [_GH_BIN, "issue", "list", "--repo", repo,
                 "--state", state, "--json",
                 "number,title,state,author,createdAt,url,labels",
                 "--limit", str(limit)],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode != 0:
                return {"error": f"gh issue list failed: {out.stderr[:200]}"}
            try:
                items = json.loads(out.stdout)
            except json.JSONDecodeError as exc:
                return {"error": f"gh issue list parse failed: {exc}"}
            return {"result": {
                "repo": repo, "state": state, "count": len(items),
                "issues": [
                    {
                        "number": i.get("number"),
                        "title": i.get("title"),
                        "state": i.get("state"),
                        "author": (i.get("author") or {}).get("login", "?"),
                        "url": i.get("url"),
                        "labels": [l.get("name") for l in (i.get("labels") or [])],
                        "created_at": i.get("createdAt", ""),
                    } for i in items
                ],
            }}
        except subprocess.TimeoutExpired:
            return {"error": "gh issue list timed out"}
        except Exception as exc:
            return {"error": f"gh issue list crashed: {exc}"}

    if name == "local_github_prs":
        repo = args.get("repo", "").strip()
        if not repo:
            return {"error": "repo is required"}
        state = args.get("state", "open").strip() or "open"
        try:
            out = subprocess.run(
                [_GH_BIN, "pr", "list", "--repo", repo,
                 "--state", state, "--json",
                 "number,title,state,author,createdAt,url,headRefName",
                 "--limit", "15"],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode != 0:
                return {"error": f"gh pr list failed: {out.stderr[:200]}"}
            try:
                items = json.loads(out.stdout)
            except json.JSONDecodeError as exc:
                return {"error": f"gh pr list parse failed: {exc}"}
            return {"result": {
                "repo": repo, "state": state, "count": len(items),
                "prs": [
                    {
                        "number": i.get("number"),
                        "title": i.get("title"),
                        "state": i.get("state"),
                        "author": (i.get("author") or {}).get("login", "?"),
                        "url": i.get("url"),
                        "branch": i.get("headRefName"),
                    } for i in items
                ],
            }}
        except Exception as exc:
            return {"error": f"gh pr list crashed: {exc}"}

    if name == "local_github_issue_create":
        repo = args.get("repo", "").strip()
        title = args.get("title", "").strip()
        body = args.get("body", "").strip()
        if not repo or not title:
            return {"error": "repo and title are required"}
        labels = args.get("labels", "")
        if isinstance(labels, list):
            labels = ",".join(labels)
        cmd = [_GH_BIN, "issue", "create", "--repo", repo, "--title", title,
               "--body", body or "(no description provided)"]
        if labels:
            cmd += ["--label", labels]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                return {"error": f"gh issue create failed: {out.stderr[:300]}"}
            # gh issue create prints the issue URL to stdout
            url = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
            return {"result": {
                "status": "created", "repo": repo, "title": title, "url": url,
            }}
        except Exception as exc:
            return {"error": f"gh issue create crashed: {exc}"}

    if name == "local_github_note":
        # Persist a free-form note to ~/.hermes/voice-users/voice-session-notes.jsonl
        # so the next Hermes turn (or a future voice session) can pick it up.
        text = args.get("text", "").strip()
        category = args.get("category", "general").strip() or "general"
        if not text:
            return {"error": "text is required"}
        try:
            _NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "category": category,
                "text": text[:4000],
            }
            with open(_NOTES_PATH, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return {"result": {"status": "noted", "category": category, "path": str(_NOTES_PATH)}}
        except Exception as exc:
            return {"error": f"note append failed: {exc}"}

    if name == "local_github_notes_read":
        # Read all persisted notes (most recent first), optionally filtered
        try:
            limit = min(max(int(args.get("limit", 20)), 1), 100)
        except (TypeError, ValueError):
            limit = 20
        category = (args.get("category") or "").strip()
        if not _NOTES_PATH.exists():
            return {"result": {"count": 0, "notes": []}}
        try:
            with open(_NOTES_PATH) as f:
                lines = [json.loads(l) for l in f if l.strip()]
        except Exception as exc:
            return {"error": f"note read failed: {exc}"}
        if category:
            lines = [n for n in lines if n.get("category") == category]
        lines.reverse()  # most recent first
        return {"result": {
            "count": len(lines),
            "notes": lines[:limit],
            "path": str(_NOTES_PATH),
        }}


    if name == "local_github_suggest_repos":
        """Search GitHub for repos matching the user's interests and return curated suggestions."""
        interests_raw = args.get("interests", [])
        limit = min(max(int(args.get("limit_per_topic", 3)), 1), 5)
        if not interests_raw:
            return {"error": "interests list is required"}
        if isinstance(interests_raw, str):
            topics = [t.strip() for t in interests_raw.split(",") if t.strip()]
        else:
            topics = [str(t).strip() for t in interests_raw if str(t).strip()]
        if not topics:
            return {"error": "at least one interest keyword is required"}
        import subprocess as _sp, json as _json
        recommendations = {}
        total = 0
        for topic in topics[:5]:  # max 5 topics per call
            try:
                out = _sp.run(
                    ["gh", "search", "repos", topic, "--limit", str(limit),
                     "--json", "fullName,description,url"],
                    capture_output=True, text=True, timeout=15,
                )
                if out.returncode != 0:
                    continue
                try:
                    results = _json.loads(out.stdout)
                except _json.JSONDecodeError:
                    continue
                curated = []
                for r in results:
                    if not isinstance(r, dict):
                        continue
                    full_name = r.get("fullName", "")
                    desc = r.get("description", "")
                    repo_url = r.get("url", "")
                    curated.append({
                        "full_name": full_name,
                        "description": (desc or "")[:200],
                        "url": repo_url,
                    })
                if curated:
                    recommendations[topic] = curated
                    total += len(curated)
            except Exception as exc:
                continue
        if not recommendations:
            return {"error": "No results found for any of the provided interests"}
        return {"result": {
            "recommendations": recommendations,
            "total_count": total,
            "note": "These are top matches by relevance. Browse and see what looks interesting!"
        }}
    
    return {"error": f"Unknown GitHub tool: {name}"}


_SYSINSPECT_ALLOWED_PREFIXES = (
    str(Path.home() / ".hermes"),
    "/etc/systemd",
    "/home/caps/hermes-workspace",
    "/home/caps/honcho",
    str(Path.home() / "hermes-extensions"),
    str(Path.home() / "projects"),
    "/var/log",
)


def _sysinspect_path_allowed(path: str) -> bool:
    try:
        resolved = str(Path(path).expanduser().resolve())
    except Exception:
        return False
    return any(resolved.startswith(prefix) for prefix in _SYSINSPECT_ALLOWED_PREFIXES)


def _run_sysinspect_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only file/grep tools. All paths must be in the allowlist."""
    if name == "local_inspect_read":
        path = args.get("path", "")
        limit = min(max(int(args.get("limit", 200)), 1), 1000)
        if not _sysinspect_path_allowed(path):
            return {"error": f"path not in allowlist: {path}"}
        try:
            with open(Path(path).expanduser(), "r", errors="replace") as f:
                content = "".join(f.readlines()[:limit])
            return {"result": {"path": path, "lines": content.count("\n"), "content": content}}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    if name == "local_inspect_grep":
        path = args.get("path", "")
        pattern = args.get("pattern", "")
        limit = min(max(int(args.get("limit", 50)), 1), 200)
        if not pattern:
            return {"error": "pattern is required"}
        if not _sysinspect_path_allowed(path):
            return {"error": f"path not in allowlist: {path}"}
        try:
            import subprocess
            proc = subprocess.run(
                ["rg", "--no-heading", "-n", "--max-count", str(limit), pattern, str(Path(path).expanduser())],
                capture_output=True,
                text=True,
                timeout=15,
            )
            matches = proc.stdout.splitlines()[:limit]
            return {"result": {"path": path, "pattern": pattern, "matches": matches, "match_count": len(matches)}}
        except FileNotFoundError:
            # rg not installed — fall back to grep
            try:
                proc = subprocess.run(
                    ["grep", "-rn", "--max-count", str(limit), pattern, str(Path(path).expanduser())],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                matches = proc.stdout.splitlines()[:limit]
                return {"result": {"path": path, "pattern": pattern, "matches": matches, "match_count": len(matches), "fallback": "grep"}}
            except Exception as exc:
                return {"error": f"grep fallback failed: {exc}"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    return {"error": f"Unknown sysinspect tool: {name}"}


def _ensure_hermes_agent_path() -> None:
    hermes_agent = Path.home() / ".hermes" / "hermes-agent"
    if str(hermes_agent) not in sys.path:
        sys.path.insert(0, str(hermes_agent))


def _run_spotify_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a Spotify tool call and return a dict for Gemini toolResponse."""
    try:
        import plugins.spotify.tools as spotify_tools
    except Exception:
        _ensure_hermes_agent_path()
        try:
            import plugins.spotify.tools as spotify_tools  # type: ignore[no-redef]
        except Exception as exc:
            logger.warning("Spotify tools import failed: %s", exc)
            return {"error": f"Spotify tools not available: {exc}"}

    try:
        if name == "spotify_play":
            result = spotify_tools._handle_spotify_playback({
                "action": "play",
                "uris": args.get("uris"),
                "context_uri": args.get("context_uri"),
                "device_id": args.get("device_id"),
            })
        elif name == "spotify_pause":
            result = spotify_tools._handle_spotify_playback({"action": "pause"})
        elif name == "spotify_next":
            result = spotify_tools._handle_spotify_playback({"action": "next"})
        elif name == "spotify_previous":
            result = spotify_tools._handle_spotify_playback({"action": "previous"})
        elif name == "spotify_get_state":
            result = spotify_tools._handle_spotify_playback({"action": "get_state"})
        elif name == "spotify_set_volume":
            result = spotify_tools._handle_spotify_playback({
                "action": "set_volume",
                "volume_percent": args.get("volume_percent"),
            })
        elif name == "spotify_search":
            result = spotify_tools._handle_spotify_search({
                "query": args.get("query"),
                "types": args.get("types", ["track"]),
            })
        elif name == "spotify_add_to_queue":
            result = spotify_tools._handle_spotify_queue({
                "action": "add",
                "uri": args.get("uri"),
                "device_id": args.get("device_id"),
            })
        elif name == "spotify_playlists":
            result = spotify_tools._handle_spotify_playlists(args)
        else:
            return {"error": f"Unknown Spotify tool: {name}"}

        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("error"):
                return {"error": parsed["error"]}
            return {"result": parsed}
        except Exception:
            return {"result": result}
    except Exception as exc:
        logger.exception("Spotify tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


_VOICE_DOMAIN_ALIASES = {
    "cortesera.eu": "corticera.eu",
    "cortesera.com": "corticera.eu",
    "cortisera.eu": "corticera.eu",
    "cortisera.com": "corticera.eu",
    "cordisera.eu": "corticera.eu",
    "cordisera.com": "corticera.eu",
    "torticera.eu": "corticera.eu",
    "torticera.com": "corticera.eu",
}


def _normalize_voice_web_text(value: str) -> str:
    """Fix common voice-ASR variants for domains before web tool dispatch."""
    text = str(value or "")
    for alias, target in _VOICE_DOMAIN_ALIASES.items():
        text = re.sub(rf"(?i)\b{re.escape(alias)}\b", target, text)
    text = re.sub(
        r"(?ix)\b[ct]\s*o\s*r\s*t\s*i\s*c\s*e\s*r\s*a\s*\.?\s*e\s*u\b",
        "corticera.eu",
        text,
    )
    text = re.sub(r"(?i)\bcorticera\s+eu\b", "corticera.eu", text)
    return text


def _normalize_voice_web_args(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(args or {})
    if name == "web_search":
        normalized["query"] = _normalize_voice_web_text(str(normalized.get("query", "")))
    elif name == "web_extract":
        urls = normalized.get("urls", [])
        if isinstance(urls, str):
            urls = [urls]
        if isinstance(urls, list):
            normalized["urls"] = [_normalize_voice_web_text(str(url)) for url in urls]
    return normalized


class _PinnedHTTPSConnection(http_client.HTTPSConnection):
    """TLS connection pinned to a validated IP while verifying the URL host."""

    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._origin_host = host
        self._validated_address = address
        self._verification_context = ssl.create_default_context()

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
        )
        self.sock = self._verification_context.wrap_socket(raw_socket, server_hostname=self._origin_host)


def _validate_public_http_url(url: str) -> tuple[Any, list[str]]:
    if not url or len(url) > 2048 or any(char in url for char in "\r\n\x00"):
        raise ValueError("Invalid HTTP URL")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("Invalid HTTP URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Invalid URL port") from exc
    if port not in {80, 443}:
        raise ValueError("Only standard HTTP/HTTPS ports are allowed")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
        answers = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (OSError, UnicodeError) as exc:
        raise ValueError("URL hostname could not be resolved") from exc
    addresses = sorted({str(answer[4][0]) for answer in answers if answer and len(answer) >= 5})
    if not addresses:
        raise ValueError("URL hostname could not be resolved")
    for address in addresses:
        try:
            public = ipaddress.ip_address(address).is_global
        except ValueError as exc:
            raise ValueError("URL hostname returned an invalid address") from exc
        if not public:
            raise ValueError("URL hostname must resolve only to public addresses")
    return parsed, addresses


def _request_public_http_once(parsed: Any, address: str) -> tuple[int, Dict[str, str], bytes]:
    hostname = parsed.hostname.encode("idna").decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        connection: Any = _PinnedHTTPSConnection(hostname, address, port, timeout=12)
    else:
        connection = http_client.HTTPConnection(address, port=port, timeout=12)
    path = parsed.path or "/"
    if parsed.params:
        path += ";" + parsed.params
    if parsed.query:
        path += "?" + parsed.query
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    headers = {
        "Host": host_header,
        "User-Agent": "HermesVoiceLive/1.0 (+https://github.com/NousResearch/hermes-agent)",
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.2",
        "Connection": "close",
    }
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read(500_000)
        response_headers = {str(key).lower(): str(value) for key, value in response.getheaders()}
        return int(response.status), response_headers, body
    finally:
        connection.close()


def _fetch_public_http(url: str, max_redirects: int = 5) -> tuple[bytes, str, str]:
    current_url = url
    for redirect_count in range(max_redirects + 1):
        parsed, addresses = _validate_public_http_url(current_url)
        last_error: Optional[BaseException] = None
        response: Optional[tuple[int, Dict[str, str], bytes]] = None
        for address in addresses:
            try:
                response = _request_public_http_once(parsed, address)
                break
            except (OSError, http_client.HTTPException) as exc:
                last_error = exc
        if response is None:
            raise OSError("Public URL request failed") from last_error
        status, headers, raw = response
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location", "").strip()
            if not location:
                raise ValueError("Redirect response did not include a location")
            if redirect_count >= max_redirects:
                raise ValueError("Too many HTTP redirects")
            current_url = urljoin(current_url, location)
            continue
        if status < 200 or status >= 400:
            raise OSError(f"HTTP request failed with status {status}")
        return raw, headers.get("content-type", ""), current_url
    raise ValueError("Too many HTTP redirects")


def _basic_extract_url(url: str) -> Dict[str, Any]:
    try:
        raw, content_type, _final_url = _fetch_public_http(url)
    except ValueError as exc:
        return {"url": url, "title": "", "content": "", "error": str(exc)}
    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    encoding = charset_match.group(1) if charset_match else "utf-8"
    text = raw.decode(encoding, errors="replace")
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else ""
    text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    content = text.strip()[:20_000]
    return {"url": url, "title": title, "content": content}


def _basic_web_extract(urls: Any) -> Dict[str, Any]:
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list):
        return {"error": "web_extract fallback expected a list of URLs"}
    results = []
    for url in urls[:5]:
        try:
            results.append(_basic_extract_url(str(url)))
        except Exception as exc:
            results.append({"url": str(url), "title": "", "content": "", "error": f"{type(exc).__name__}: {exc}"})
    return {"result": {"success": True, "data": {"pages": results}, "results": results}}


def _run_web_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a web search/extract tool call and return a dict for Gemini toolResponse."""
    _ensure_hermes_agent_path()
    try:
        import tools.web_tools as web_tools
    except Exception as exc:
        logger.warning("Web tools import failed: %s", exc)
        return {"error": f"Web tools not available: {exc}"}
    try:
        if name == "web_search":
            result = web_tools.web_search_tool(query=args.get("query", ""), limit=args.get("limit", 5))
        elif name == "web_extract":
            result = asyncio.run(web_tools.web_extract_tool(urls=args.get("urls", [])))
        else:
            return {"error": f"Unknown web tool: {name}"}
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("success") is False:
                error_text = str(parsed.get("error", ""))
                recoverable_extract_error = any(
                    marker in error_text.lower()
                    for marker in (
                        "no web extract provider configured",
                        "plugin is disabled",
                        "plugin ('web/",
                        "extract backend unavailable",
                    )
                )
                if name == "web_extract" and recoverable_extract_error:
                    logger.warning("Web extract provider unavailable; using basic HTTP fallback")
                    return _basic_web_extract(args.get("urls", []))
                return {"error": parsed.get("error", "web tool failed")}
            return {"result": parsed}
        except Exception:
            return {"result": result}
    except Exception as exc:
        logger.exception("Web tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


class _CalcVisitor:
    """Restricted AST evaluator for local_calc: only safe math ops."""
    ALLOWED_NAMES = {
        "sqrt": __import__("math").sqrt,
        "abs": abs,
        "sin": __import__("math").sin,
        "cos": __import__("math").cos,
        "log": __import__("math").log,
        "round": round,
        "min": min,
        "max": max,
    }

    def visit(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp):
            left = self.visit(node.left)
            right = self.visit(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div):
                if right == 0: raise ValueError("Division by zero")
                return left / right
            if isinstance(node.op, ast.Pow): return left ** right
            raise ValueError("Unsupported binary operator")
        if isinstance(node, ast.UnaryOp):
            operand = self.visit(node.operand)
            if operand is None:
                raise ValueError("Invalid operand")
            if isinstance(node.op, ast.UAdd): return +operand
            if isinstance(node.op, ast.USub): return -operand
            raise ValueError("Unsupported unary operator")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("Only named function calls allowed")
            fn = self.ALLOWED_NAMES.get(node.func.id)
            if fn is None:
                raise ValueError(f"Function '{node.func.id}' not allowed")
            args = [self.visit(a) for a in node.args]
            return fn(*args)
        if isinstance(node, ast.Name):
            if node.id in ("pi", "e", "tau"):
                import math
                return getattr(math, node.id)
            raise ValueError(f"Name '{node.id}' not allowed")
        if isinstance(node, ast.Expr):
            return self.visit(node.value)
        raise ValueError("Unsupported expression")


def _run_local_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a safe local helper tool and return a dict for Gemini toolResponse.

    All tools are read-only or append-only. No destructive operations.
    """
    # Criterion #8 — play tool_init sfx on the first tool call of a session.
    # Subsequent calls are silent (the sfx library is meant to be subtle,
    # not a per-call notification).
    if not getattr(_run_local_tool, "_tool_init_played", False):
        try:
            from sfx import play_sfx
            play_sfx("tool_init")
        except Exception:
            pass
        _run_local_tool._tool_init_played = True  # type: ignore[attr-defined]
    try:
        if name == "local_weather":
            location = args.get("location", "Amsterdam")
            try:
                import requests
            except Exception:
                return {"error": "requests not installed"}
            geo_url = "https://geocoding-api.open-meteo.com/v1/search"
            params = {"name": location, "count": 1, "format": "json"}
            try:
                r = requests.get(geo_url, params=params, timeout=10)
                r.raise_for_status()
                results = r.json().get("results", [])
                if not results:
                    return {"error": f"Location '{location}' not found"}
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]
                city = results[0].get("name", location)
                weather_url = "https://api.open-meteo.com/v1/forecast"
                wp = {
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": "true",
                    "timezone": "auto",
                }
                wr = requests.get(weather_url, params=wp, timeout=10)
                wr.raise_for_status()
                cw = wr.json().get("current_weather", {})
                temp = cw.get("temperature")
                wind = cw.get("windspeed")
                code = cw.get("weathercode")
                conditions = {
                    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                    45: "Fog", 48: "Depositing rime fog",
                    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
                    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
                    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
                    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
                    95: "Thunderstorm",
                }.get(code, "Unknown conditions")
                return {"result": {
                    "location": city,
                    "temperature_c": temp,
                    "wind_kph": wind,
                    "conditions": conditions,
                }}
            except Exception as exc:
                logger.exception("Weather fetch failed")
                return {"error": f"Weather fetch failed: {exc}"}

        elif name == "local_translate":
            text = args.get("text", "")
            target = args.get("target_language", "en").lower()
            source = args.get("source_language", "")
            try:
                from deep_translator import GoogleTranslator
                # Map language names to codes if needed
                lang_map = {"dutch": "nl", "romanian": "ro", "english": "en", "spanish": "es", "german": "de", "french": "fr", "italian": "it"}
                target = lang_map.get(target, target)
                source = lang_map.get(source, source) if source else "auto"
                kwargs = {"target": target}
                if source and source != "auto":
                    kwargs["source"] = source
                result = GoogleTranslator(**kwargs).translate(text)
                return {"result": {"translation": result, "source_detected": source or "auto", "target": target}}
            except Exception as exc:
                logger.warning("translate tool failed: %s", exc)
                return {"error": f"translate unavailable (deep_translator needed): {exc}"}

        elif name == "local_time":
            tz = args.get("timezone", "Europe/Amsterdam")
            try:
                from zoneinfo import ZoneInfo
                from datetime import datetime
                dt = datetime.now(ZoneInfo(tz))
                return {"result": {"time": dt.strftime("%H:%M"), "date": dt.strftime("%Y-%m-%d"), "day": dt.strftime("%A"), "timezone": tz}}
            except Exception:
                try:
                    import pytz
                    from datetime import datetime
                    dt = datetime.now(pytz.timezone(tz))
                    return {"result": {"time": dt.strftime("%H:%M"), "date": dt.strftime("%Y-%m-%d"), "day": dt.strftime("%A"), "timezone": tz}}
                except Exception as exc:
                    return {"error": f"Timezone lookup failed: {exc}"}

        elif name == "local_remind":
            action = args.get("action", "list")
            reminders_path = Path.home() / ".hermes" / "voice-reminders.jsonl"
            if action == "add":
                reminder = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "text": args.get("text", ""),
                    "minutes": args.get("minutes"),
                }
                try:
                    reminders_path.parent.mkdir(parents=True, exist_ok=True)
                    with reminders_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(reminder, ensure_ascii=False) + "\n")
                    return {"result": {"status": "saved", "text": reminder["text"]}}
                except Exception as exc:
                    return {"error": f"Reminder save failed: {exc}"}
            else:
                try:
                    if not reminders_path.exists():
                        return {"result": {"count": 0, "reminders": []}}
                    lines = reminders_path.read_text(encoding="utf-8").strip().splitlines()
                    recents = [json.loads(line) for line in lines[-20:]]
                    return {"result": {"count": len(recents), "reminders": recents}}
                except Exception as exc:
                    return {"error": f"Reminder list failed: {exc}"}

        elif name == "local_email":
            limit = args.get("limit", 5)
            try:
                cmd = [
                    "himalaya",
                    "--quiet",
                    "-o",
                    "json",
                    "envelope",
                    "list",
                    "--page-size",
                    str(limit),
                ]
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if out.returncode != 0:
                    return {"error": f"himalaya error: {out.stderr[:200]}"}
                data = json.loads(out.stdout)
                emails = []
                messages = data if isinstance(data, list) else data.get("response", data.get("results", []))
                for msg in messages:
                    sender = msg.get("from", {})
                    if isinstance(sender, list):
                        sender = sender[0] if sender else {}
                    emails.append({
                        "id": msg.get("id"),
                        "from": sender.get("addr") or sender.get("address") or sender.get("name") or "unknown",
                        "subject": msg.get("subject", "(no subject)"),
                        "date": msg.get("date"),
                    })
                return {"result": {"emails": emails}}
            except Exception as exc:
                logger.exception("Email list failed")
                return {"error": f"Email tool failed: {exc}"}

        elif name == "local_email_read":
            message_id = args.get("message_id", "")
            if not message_id:
                return {"error": "message_id is required"}
            try:
                if Path(GOOGLE_API_BIN).exists():
                    out = subprocess.run(
                        [sys.executable, GOOGLE_API_BIN, "gmail", "get", message_id],
                        capture_output=True, text=True, timeout=30,
                    )
                    if out.returncode == 0:
                        try:
                            data = json.loads(out.stdout)
                            payload = data.get("payload", {})
                            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
                            body = data.get("snippet", "")
                            return {"result": {
                                "from": headers.get("from", ""),
                                "to": headers.get("to", ""),
                                "subject": headers.get("subject", ""),
                                "date": headers.get("date", ""),
                                "body": body,
                                "id": message_id,
                            }}
                        except (json.JSONDecodeError, AttributeError, KeyError) as parse_exc:
                            return {"result": {"raw": out.stdout[:5000], "error": str(parse_exc)}}
                    return {"error": f"google_api.py error: {out.stderr[:300]}"}
                return {"error": "google_api.py not found, cannot read email"}
            except Exception as exc:
                logger.exception("Email read failed")
                return {"error": f"Email read failed: {exc}"}

        elif name == "local_email_send":
            to_raw = args.get("to", "")
            subject = args.get("subject", "")
            body = args.get("body", "")
            if not to_raw or not subject or not body:
                return {"error": "to, subject, and body are all required"}
            # Auto-correct voice-transcribed email addresses (criterion #18).
            # Common STT errors: " at " → "@", " dot " → ".", " underscore " → "_",
            # doubled spaces, missing TLDs, and accidental spaces inside
            # local-part or domain. Best-effort — returns the corrected
            # address and a note if anything was changed.
            to, to_corrections = _autocorrect_email_address(to_raw)
            if to_corrections:
                logger.info(
                    "Email 'to' address was auto-corrected: %r -> %r (%s)",
                    to_raw, to, "; ".join(to_corrections),
                )
            try:
                if Path(GOOGLE_API_BIN).exists():
                    out = subprocess.run(
                        [sys.executable, GOOGLE_API_BIN, "gmail", "send",
                         "--to", to, "--subject", subject, "--body", body],
                        capture_output=True, text=True, timeout=30,
                    )
                    if out.returncode == 0:
                        try:
                            data = json.loads(out.stdout)
                            # Webhook: email_sent
                            try:
                                from webhook_dispatcher import emit_email_sent
                                emit_email_sent(to, subject)
                            except Exception:
                                pass
                            return {"result": {
                                "status": "sent",
                                "id": data.get("id", ""),
                                "threadId": data.get("threadId", ""),
                                "to_corrections": to_corrections or None,
                            }}
                        except json.JSONDecodeError:
                            try:
                                from webhook_dispatcher import emit_email_sent
                                emit_email_sent(to, subject)
                            except Exception:
                                pass
                            return {"result": {"status": "sent", "raw": out.stdout[:2000]}}
                    return {"error": f"Send failed: {out.stderr[:300]}"}
                return {"error": "google_api.py not found"}
            except Exception as exc:
                logger.exception("Email send failed")
                return {"error": f"Email send failed: {exc}"}

        elif name == "local_email_reply":
            message_id = args.get("message_id", "")
            body = args.get("body", "")
            if not message_id or not body:
                return {"error": "message_id and body are required"}
            try:
                if Path(GOOGLE_API_BIN).exists():
                    out = subprocess.run(
                        [sys.executable, GOOGLE_API_BIN, "gmail", "reply", message_id, "--body", body],
                        capture_output=True, text=True, timeout=30,
                    )
                    if out.returncode == 0:
                        try:
                            data = json.loads(out.stdout)
                            return {"result": {"status": "replied", "id": data.get("id", ""), "threadId": data.get("threadId", "")}}
                        except json.JSONDecodeError:
                            return {"result": {"status": "replied", "raw": out.stdout[:2000]}}
                    return {"error": f"Reply failed: {out.stderr[:300]}"}
                return {"error": "google_api.py not found"}
            except Exception as exc:
                logger.exception("Email reply failed")
                return {"error": f"Email reply failed: {exc}"}

        elif name == "local_email_brief":
            # Proactive inbox digest (criterion #7). Returns a spoken brief
            # to the model AND fires local_notify(mode="auto") so AFK users
            # still get pinged. force=true skips the de-dup check.
            try:
                from email_brief import build_brief, build_and_notify
            except Exception as exc:
                return {"error": f"email_brief module import failed: {exc}"}
            limit = int(args.get("limit", 8))
            force = bool(args.get("force", False))
            notify = bool(args.get("notify", True))
            backend = args.get("backend", "google")
            try:
                if not notify:
                    # Pure read — return brief to model, no DM/webhook
                    payload = build_brief(limit=limit, backend=backend)
                    return {"result": {**payload, "notified": False, "delivery": None}}
                # Resolve the live bridge for the notification path.
                _bridge = _get_bridge()
                _uid = _bridge_user_id(_bridge)
                try:
                    _bridge = _opencode_get_bridge(session_name="__notify__", user_id=_uid) or _bridge
                except Exception:
                    pass
                if _bridge is None:
                    _bridge = _get_bridge()
                _adapter = getattr(_bridge, "_adapter", None) if _bridge is not None else None
                _uid = (
                    _bridge_user_id(_bridge)
                    or os.getenv("DISCORD_VOICE_LIVE_USER_ID", "1474100257762578597")
                )
                payload = build_and_notify(
                    limit=limit,
                    backend=backend,
                    force=force,
                    bridge=_bridge,
                    adapter=_adapter,
                    user_id=_uid,
                    source="email_brief_tool",
                )
                return {"result": payload}
            except Exception as exc:
                logger.exception("Email brief failed")
                return {"error": f"Email brief failed: {exc}"}

        elif name == "local_systemd":
            svc = args.get("service")
            try:
                if svc:
                    cmd = ["systemctl", "--user", "status", svc, "--no-pager", "-o", "cat"]
                else:
                    cmd = ["systemctl", "--user", "list-units", "--type=service", "--state=running", "--no-pager", "--plain"]
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                return {"result": {"output": out.stdout[-2000:] or out.stderr[:500]}}
            except Exception as exc:
                return {"error": f"systemd check failed: {exc}"}

        elif name == "local_docker":
            try:
                out = subprocess.run(
                    ["docker", "ps", "--format", "json"],
                    capture_output=True, text=True, timeout=10,
                )
                if out.returncode != 0:
                    return {"error": f"docker error: {out.stderr[:200]}"}
                containers = []
                for line in out.stdout.strip().splitlines():
                    c = json.loads(line)
                    containers.append({
                        "name": c.get("Names", "").split(",")[0],
                        "image": c.get("Image"),
                        "status": c.get("Status"),
                        "ports": c.get("Ports"),
                    })
                return {"result": {"containers": containers}}
            except Exception as exc:
                return {"error": f"Docker check failed: {exc}"}

        elif name == "local_tailscale":
            try:
                out = subprocess.run(
                    ["tailscale", "status", "--json"],
                    capture_output=True, text=True, timeout=10,
                )
                if out.returncode != 0:
                    return {"error": f"tailscale error: {out.stderr[:200]}"}
                data = json.loads(out.stdout)
                peers = []
                for name, node in data.get("Peer", {}).items():
                    peers.append({
                        "name": name,
                        "online": node.get("Online", False),
                        "ip": node.get("TailscaleIPs", []),
                        "os": node.get("OS"),
                    })
                return {"result": {"self": data.get("Self", {}).get("HostName"), "peers": peers}}
            except Exception as exc:
                return {"error": f"Tailscale check failed: {exc}"}

        elif name == "local_notes":
            query = args.get("query", "").lower()
            limit = args.get("limit", 5)
            try:
                matches = []
                for f in NOTES_DIR.glob("*.jsonl"):
                    if not f.is_file():
                        continue
                    for line in f.read_text(encoding="utf-8").strip().splitlines():
                        if not line:
                            continue
                        obj = json.loads(line)
                        text = json.dumps(obj, ensure_ascii=False).lower()
                        if query in text:
                            matches.append({"file": f.name, "event": obj})
                return {"result": {"matches": matches[:limit]}}
            except Exception as exc:
                return {"error": f"Notes search failed: {exc}"}

        elif name == "local_disk":
            try:
                import shutil
                usage = shutil.disk_usage("/")
                gb_total = usage.total / (1024**3)
                gb_used = usage.used / (1024**3)
                gb_free = usage.free / (1024**3)
                pct = round(usage.used / usage.total * 100, 1)
                return {"result": {"total_gb": round(gb_total, 1), "used_gb": round(gb_used, 1), "free_gb": round(gb_free, 1), "percent_used": pct}}
            except Exception as exc:
                return {"error": f"Disk check failed: {exc}"}

        elif name == "local_calc":
            expr = args.get("expression", "")
            if not expr:
                return {"error": "Empty expression"}
            try:
                tree = ast.parse(expr, mode="eval")
                result = _CalcVisitor().visit(tree.body)
                return {"result": {"expression": expr, "value": result}}
            except Exception as exc:
                return {"error": f"Calculation error: {exc}"}

        elif name == "local_uptime":
            try:
                with open("/proc/uptime", "r") as fh:
                    up_sec = float(fh.read().split()[0])
                up_h = int(up_sec // 3600)
                up_m = int((up_sec % 3600) // 60)
                with open("/proc/loadavg", "r") as fh:
                    load = fh.read().split()[:3]
                mem_info = {}
                with open("/proc/meminfo", "r") as fh:
                    for line in fh:
                        if line.startswith("MemTotal:"):
                            mem_info["total_mb"] = int(line.split()[1]) // 1024
                        elif line.startswith("MemAvailable:"):
                            mem_info["available_mb"] = int(line.split()[1]) // 1024
                return {"result": {"uptime": f"{up_h}h {up_m}m", "load": load, "memory_mb": mem_info}}
            except Exception as exc:
                return {"error": f"Uptime read failed: {exc}"}

        elif name == "local_news":
            topic = args.get("topic", "tech")
            limit = args.get("limit", 5)
            _ensure_hermes_agent_path()
            try:
                from tools.web_tools import web_search_tool
                result = web_search_tool(
                    query=f"latest {topic} news {time.strftime('%Y')}",
                    limit=max(limit, 10),
                )
                # Terse voice-friendly results
                lines = []
                if isinstance(result, dict):
                    for item in result.get("data", {}).get("web", result.get("results", []))[:limit]:
                        lines.append(f"{item.get('title', 'untitled')} — {item.get('source', item.get('url', 'link'))}")
                return {"result": {"headlines": lines, "topic": topic}}
            except Exception as exc:
                return {"error": f"News lookup failed: {exc}"}

        elif name == "local_youtube":
            query = args.get("query", "")
            limit = args.get("limit", 5)
            _ensure_hermes_agent_path()
            try:
                from tools.web_tools import web_search_tool
                result = web_search_tool(
                    query=f"site:youtube.com {query}",
                    limit=max(limit, 10),
                )
                lines = []
                seen = set()
                for item in result.get("data", {}).get("web", result.get("results", []))[:limit + 5]:
                    url = item.get("url", "")
                    if "youtube.com/watch" in url and url not in seen:
                        seen.add(url)
                        lines.append(f"{item.get('title', 'untitled')} — {url}")
                    if len(lines) >= limit:
                        break
                return {"result": {"videos": lines}}
            except Exception as exc:
                return {"error": f"YouTube search failed: {exc}"}

        elif name == "local_honcho":
            query = str(args.get("query") or "").strip()[:1000]
            if not query:
                query = "current user context, preferences, and relevant unfinished work"
            try:
                limit = max(1, min(int(args.get("limit", 3)), 5))
            except (TypeError, ValueError):
                limit = 3
            try:
                import requests
                honcho_json = Path.home() / ".hermes" / "honcho.json"
                if not honcho_json.exists():
                    return {"error": "~/.hermes/honcho.json not found"}
                hc = json.loads(honcho_json.read_text())
                host = hc.get("hosts", {}).get("hermes", {})
                base_url = host.get("baseUrl") or hc.get("baseUrl") or "http://127.0.0.1:8000"
                workspace = host.get("workspace") or hc.get("workspace") or "hermes"
                api_key = host.get("apiKey") or hc.get("apiKey") or ""
                peer_name = host.get("peerName") or hc.get("peerName") or "user"
                r = requests.post(
                    f"{base_url}/v3/workspaces/{workspace}/peers/{peer_name}/search",
                    json={"query": query, "limit": limit},
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=5,
                )
                r.raise_for_status()
                data = r.json()
                items = data if isinstance(data, list) else data.get("items", data.get("results", []))
                excerpts = []
                seen = set()
                total_chars = 0
                for item in items or []:
                    if not isinstance(item, dict):
                        continue
                    content = " ".join(str(item.get("content") or "").split())
                    if not content:
                        continue
                    content = content[:600]
                    if content in seen:
                        continue
                    remaining = 3000 - total_chars
                    if remaining <= 0:
                        break
                    content = content[:remaining]
                    excerpts.append(content)
                    seen.add(content)
                    total_chars += len(content)
                    if len(excerpts) >= limit:
                        break
                return {"result": {"excerpts": excerpts, "query": query}}
            except Exception as exc:
                return {"error": f"Honcho search failed: {exc}"}

        elif name in ("local_user_onboarding_get_questions", "local_user_onboarding_answer"):
            # #32: New-user onboarding Q&A. Imports user_profiles on
            # demand so the local-tool dispatch doesn't hard-fail at
            # module import if the profile module has issues.
            try:
                from user_profiles import (
                    ONBOARDING_QUESTIONS,
                    get_or_create_profile,
                    mark_onboarding_complete,
                )
            except Exception as exc:
                return {"error": f"onboarding module import failed: {exc}"}

            if name == "local_user_onboarding_get_questions":
                return {"result": {
                    "questions": [
                        {"id": q["id"], "question": q["question"]}
                        for q in ONBOARDING_QUESTIONS
                    ],
                    "instructions": (
                        "Ask these one at a time in voice, in order. "
                        "After each answer, call local_user_onboarding_answer. "
                        "Don't rush; mirror the user's energy."
                    ),
                }}

            # local_user_onboarding_answer
            qid = args.get("question_id", "").strip()
            answer = (args.get("answer") or "").strip()
            if not qid or not answer:
                return {"error": "question_id and answer are both required"}
            valid_ids = {q["id"] for q in ONBOARDING_QUESTIONS}
            if qid not in valid_ids:
                return {"error": f"unknown question_id: {qid!r}. Valid: {sorted(valid_ids)}"}
            # Get the active user. We rely on the bridge's _user_profile.
            bridge = globals().get("BRIDGE")
            user_id = None
            if bridge is not None:
                prof = getattr(bridge, "_user_profile", None)
                if prof is not None:
                    user_id = getattr(prof, "discord_id", None)
            if not user_id:
                return {"error": "no active user (bridge/_user_profile missing)"}
            existing = get_or_create_profile(user_id)
            merged = dict(existing.onboarding_answers)
            merged[qid] = answer
            updated = mark_onboarding_complete(existing, merged)
            return {"result": {
                "stored": qid,
                "answer_length": len(answer),
                "answers_so_far": list(updated.onboarding_answers.keys()),
                "onboarding_completed": updated.onboarding_completed,
            }}

        # ── Multi-CLI delegation tools (criterion #23-#25) ─────────────────
        elif name in ("local_delegate_quick", "local_delegate_status", "local_delegate_suggest",
                       "local_delegate_assemble", "local_delegate_execute", "local_delegate_eta",
                       "local_delegate_health"):
            suggest_platform = _local_delegation_agent.suggest_platform
            assemble_prompt = _local_delegation_agent.assemble_prompt
            execute_delegation = _local_delegation_agent.execute_delegation
            execute_with_fallback = _local_delegation_agent.execute_with_fallback
            observe_delegation = _local_delegation_agent.observe_delegation
            estimate_eta = _local_delegation_agent.estimate_eta
            get_health_snapshot = _local_delegation_agent.get_health_snapshot
            clear_platform_health = _local_delegation_agent.clear_platform_health
            mark_platform_broken = _local_delegation_agent.mark_platform_broken
            is_valid_session_id = _local_delegation_agent.is_valid_session_id
            lookup_delegation = _local_delegation_agent.lookup_delegation
            _FALLBACK_CHAIN = _local_delegation_agent._FALLBACK_CHAIN
            _USER_ETA_CORRECTION = _local_delegation_agent._USER_ETA_CORRECTION

            if name == "local_delegate_status":
                session_id = str(args.get("sessionId") or "").strip()
                platform = str(args.get("platform") or "").strip().lower()
                if not is_valid_session_id(session_id):
                    return {"error": "invalid delegation session_id"}
                if platform not in {"opencode", "codex"}:
                    return {"error": "unsupported sandboxed delegation platform"}
                recorded = lookup_delegation(session_id, platform)
                if not recorded:
                    return {"error": "delegation session was not found"}
                result = observe_delegation(recorded, wait_seconds=0)
                return {"result": result}

            if name == "local_delegate_quick":
                import time as _t

                goal = str(args.get("goal") or "").strip()
                if not goal:
                    return {"error": "goal is required"}
                goal = goal[:8000]
                platform = str(args.get("platform") or "auto").strip().lower()
                if platform not in {"auto", "opencode", "codex"}:
                    return {"error": "unsupported sandboxed delegation platform"}
                if platform == "auto":
                    suggestion = suggest_platform(
                        goal=goal,
                        project_size="small",
                        scope="code",
                        complexity="medium",
                        user_id=None,
                    )
                    platform = str(suggestion.get("suggestion") or "opencode")
                if platform not in {"opencode", "codex"}:
                    platform = "opencode"
                session_id = "live-{}-{}".format(int(_t.time() * 1000), secrets.token_hex(4))
                prompt = assemble_prompt(
                    goal=goal,
                    subgoals=[],
                    platform=platform,
                    project_root=args.get("workdir"),
                )
                result = execute_with_fallback(
                    prompt=prompt,
                    platform=platform,
                    session_id=session_id,
                    workdir=args.get("workdir"),
                )
                result = observe_delegation(result, wait_seconds=3.0)
                result.setdefault("status", "failed" if result.get("error") else "started")
                return {"result": result}

            if name == "local_delegate_suggest":
                result = suggest_platform(
                    goal=args.get("goal", ""),
                    project_size=args.get("project_size", "medium"),
                    scope=args.get("scope", "code"),
                    complexity=args.get("complexity", "medium"),
                    user_id=None,  # per-user tracking TBD
                )
                # Filter out platforms currently marked broken (criterion #5)
                try:
                    health = get_health_snapshot()
                    if isinstance(result, dict) and "available_platforms" in result:
                        available = list(result["available_platforms"])
                        safe_platforms = {"opencode", "codex"}
                        unsafe = [p for p in available if p not in safe_platforms]
                        healthy = [p for p in available if p in safe_platforms and p not in health]
                        removed = [p for p in available if p in safe_platforms and p in health]
                        result["available_platforms"] = healthy
                        result["unsafe_platforms"] = unsafe
                        result["unhealthy_platforms"] = removed
                        result["unhealthy_reasons"] = {p: health[p].get("reason", "?") for p in removed}
                        original = result.get("suggestion")
                        if original not in healthy and healthy:
                            result["suggestion"] = healthy[0]
                            result["reason"] = (
                                f"Original pick `{original}` was unavailable for sandboxed Live execution; "
                                f"re-routed to `{healthy[0]}`."
                            )
                            result["was_fallback"] = True
                except Exception:
                    pass
                # Webhook
                try:
                    from webhook_dispatcher import emit_bridge_status
                    emit_bridge_status(
                        "info",
                        f"Delegation suggested: {result.get('suggestion')} "
                        f"for '{args.get('goal', '')[:60]}' "
                        f"(ETA: {result.get('estimated_eta_display')})",
                    )
                except Exception:
                    pass
                return {"result": result}

            if name == "local_delegate_assemble":
                prompt = assemble_prompt(
                    goal=args.get("goal", ""),
                    subgoals=args.get("subgoals", []),
                    platform=args.get("platform", "opencode"),
                    project_root=args.get("project_root"),
                )
                return {"result": {
                    "prompt": prompt,
                    "platform": args.get("platform"),
                    "length": len(prompt),
                    "tokens_est": len(prompt.split()) * 10,
                }}

            if name == "local_delegate_execute":
                import time as _t
                platform = args.get("platform", "opencode")
                if platform not in {"opencode", "codex"}:
                    return {"error": "unsupported sandboxed delegation platform"}
                session_id = args.get("session_id", f"del-{int(_t.time())}")
                # Use execute_with_fallback so broken platforms auto-route (criterion #5)
                result = execute_with_fallback(
                    prompt=args.get("prompt", ""),
                    platform=platform,
                    session_id=session_id,
                    workdir=args.get("workdir"),
                )
                # Webhook
                try:
                    from webhook_dispatcher import emit_opencode_status
                    sid = result.get("session_id", session_id)
                    active = result.get("active_platform", platform)
                    if active != platform:
                        # Fallback fired — narrate it
                        try:
                            from webhook_dispatcher import emit_bridge_status
                            emit_bridge_status(
                                "warning",
                                f"Delegation fallback: `{platform}` → `{active}` "
                                f"({result.get('fallback_reason', 'broken')[:160]})",
                            )
                        except Exception:
                            pass
                    emit_opencode_status(
                        "opencode_started", sid,
                        f"Delegated to {active}"
                        + (f" (fallback from {platform})" if active != platform else ""),
                        fields=[
                            {"name": "Session", "value": sid, "inline": True},
                            {"name": "Platform", "value": f"`{active}`", "inline": True},
                        ],
                    )
                except Exception:
                    pass
                return {"result": result}

            if name == "local_delegate_health":
                action = args.get("action", "list")
                safe_platforms = {"opencode", "codex"}
                if action == "list":
                    snapshot = {k: v for k, v in get_health_snapshot().items() if k in safe_platforms}
                    return {"result": {
                        "unhealthy": snapshot,
                        "fallback_chain": {k: _FALLBACK_CHAIN.get(k, []) for k in sorted(safe_platforms)},
                        "note": "These platforms are skipped by suggest and auto-routed by execute until TTL expires.",
                    }}
                if action == "clear":
                    target = args.get("platform")
                    if target and target not in safe_platforms:
                        return {"error": "unsupported sandboxed delegation platform"}
                    targets = [target] if target else sorted(safe_platforms)
                    for platform_name in targets:
                        clear_platform_health(platform_name)
                    return {"result": {
                        "cleared": target or sorted(safe_platforms),
                        "unhealthy": {
                            k: v for k, v in get_health_snapshot().items() if k in safe_platforms
                        },
                    }}
                if action == "mark":
                    target = args.get("platform", "")
                    if not target:
                        return {"error": "platform is required for action=mark"}
                    if target not in safe_platforms:
                        return {"error": "unsupported sandboxed delegation platform"}
                    reason = str(args.get("reason", "manual mark via tool"))[:500]
                    ttl = max(1, min(int(args.get("ttl_seconds", 600)), 3600))
                    mark_platform_broken(target, reason, ttl)
                    return {"result": {
                        "marked": target,
                        "reason": reason,
                        "ttl_seconds": ttl,
                        "fallback_chain": _FALLBACK_CHAIN.get(target, []),
                    }}
                return {"error": f"unknown action: {action} (use list|clear|mark)"}

            if name == "local_delegate_eta":
                actual = args.get("actual_seconds", 0)
                estimated = args.get("estimated_seconds", 0)
                if not actual or not estimated:
                    return {"error": "actual_seconds and estimated_seconds are required"}
                correction = actual / max(estimated, 1)
                # Store per-current-user (user_id=None for now)
                _USER_ETA_CORRECTION[None] = correction
                return {"result": {
                    "correction_factor": correction,
                    "applied": True,
                    "note": "Future ETA estimates will be adjusted by {:.2f}x".format(correction),
                }}

        # ── Proactive notification breakout (criterion #6) ─────────────────
        elif name in ("local_notify", "local_notify_schedule"):
            try:
                from notification import (
                    deliver as _notify_deliver,
                    schedule_notification as _notify_schedule,
                    list_scheduled as _notify_list,
                    cancel_scheduled as _notify_cancel,
                )
            except Exception as exc:
                return {"error": f"notification module import failed: {exc}"}

            if name == "local_notify":
                # The bridge is held in the tool runner's closure via _user_profile
                # / per-user weak-ref. Pull the live bridge so the dispatcher can
                # route to voice + DM + webhook. _run_local_tool is a module-level
                # function in bridge.py, so BRIDGE/_opencode_get_bridge live in
                # this module's globals — no import needed.
                _bridge = _get_bridge()
                _user_id = _bridge_user_id(_bridge)
                try:
                    _bridge = _opencode_get_bridge(session_name="__notify__", user_id=_user_id) or _bridge
                except Exception:
                    pass
                if _bridge is None:
                    _bridge = _get_bridge()
                _adapter = getattr(_bridge, "_adapter", None) if _bridge is not None else None
                _user_id = (
                    _bridge_user_id(_bridge)
                    or os.getenv("DISCORD_VOICE_LIVE_USER_ID", "1474100257762578597")
                )
                result = _notify_deliver(
                    text=args.get("text", ""),
                    mode=args.get("mode", "auto"),
                    bridge=_bridge,
                    adapter=_adapter,
                    user_id=_user_id,
                    channel_id=args.get("channel_id"),
                    event_class=args.get("event_class", "agent.notify"),
                    sub_event=args.get("sub_event", "agent_notification"),
                    title=args.get("title"),
                    source=args.get("source", "agent"),
                )
                # Webhook record
                try:
                    from webhook_dispatcher import emit_agent_notify
                    if result.get("status") in ("ok", "partial", "no_subscribers"):
                        emit_agent_notify(
                            text=args.get("text", "")[:1900],
                            source=args.get("source", "agent"),
                            title=args.get("title"),
                        )
                except Exception:
                    pass
                # Criterion #8 — play notification sfx for the user
                try:
                    from sfx import play_sfx
                    play_sfx("notification")
                except Exception:
                    pass
                return {"result": result}

            if name == "local_notify_schedule":
                if args.get("list"):
                    return {"result": {"scheduled": _notify_list()}}
                if args.get("cancel_id"):
                    removed = _notify_cancel(args.get("cancel_id"))
                    return {"result": {"cancelled": removed, "cancel_id": args.get("cancel_id")}}
                # Schedule a new one
                if not args.get("text"):
                    return {"error": "text is required (or pass list=true / cancel_id=...)"}
                import time as _t
                if args.get("fire_at_epoch") is not None:
                    fire_at = float(args.get("fire_at_epoch"))
                elif args.get("delay_seconds") is not None:
                    fire_at = _t.time() + float(args.get("delay_seconds"))
                else:
                    return {"error": "either delay_seconds or fire_at_epoch is required"}
                # Pull the live bridge so the scheduled deliver() can use it later
                _bridge = _get_bridge()
                _adapter = getattr(_bridge, "_adapter", None) if _bridge is not None else None
                _user_id = (
                    (_bridge._target_user_id if _bridge is not None else None)
                    or os.getenv("DISCORD_VOICE_LIVE_USER_ID", "1474100257762578597")
                )
                result = _notify_schedule(
                    fire_at=fire_at,
                    text=args.get("text", ""),
                    mode=args.get("mode", "auto"),
                    title=args.get("title"),
                    source=args.get("source", "scheduled"),
                    bridge=_bridge,
                    adapter=_adapter,
                    user_id=_user_id,
                    channel_id=args.get("channel_id"),
                )
                return {"result": result}

        elif name == "local_sfx_test":
            # Play a UI sfx slot in the active voice session (criterion #8).
            try:
                from sfx import play_sfx, list_slots
            except Exception as exc:
                return {"error": f"sfx module import failed: {exc}"}
            action = args.get("action", "play")
            slot = args.get("slot", "")
            if action == "list":
                return {"result": {"slots": list_slots()}}
            if not slot:
                return {"error": "slot is required (e.g. 'tool_init', 'error', 'notification', 'transition')"}
            res = play_sfx(slot)
            return {"result": res}

        elif name.startswith("local_homeassistant_"):
            hass_url = os.getenv("HASS_URL", "http://homeassistant.local:8123").rstrip("/")
            hass_token = os.getenv("HASS_TOKEN", "")
            if not hass_token:
                return {"error": "Home Assistant not configured: no HASS_TOKEN set"}
            try:
                import requests as _req
                headers = {
                    "Authorization": f"Bearer {hass_token}",
                    "Content-Type": "application/json",
                }
                if name == "local_homeassistant_entity_list":
                    r = _req.get(f"{hass_url}/api/states", headers=headers, timeout=10)
                    r.raise_for_status()
                    entities = r.json()
                    summary = []
                    for ent in entities:
                        fid = ent.get("attributes", {}).get("friendly_name", "")
                        summary.append({
                            "entity_id": ent["entity_id"],
                            "state": ent["state"],
                            "friendly_name": fid,
                            "domain": ent["entity_id"].split(".")[0],
                        })
                    return {"result": {"count": len(summary), "entities": summary[:50]}}
                elif name == "local_homeassistant_get_state":
                    entity_id = args.get("entity_id", "")
                    if not entity_id:
                        return {"error": "entity_id is required"}
                    r = _req.get(f"{hass_url}/api/states/{entity_id}", headers=headers, timeout=10)
                    if r.status_code == 404:
                        return {"error": f"Entity '{entity_id}' not found"}
                    r.raise_for_status()
                    ent = r.json()
                    return {"result": {
                        "entity_id": ent["entity_id"],
                        "state": ent["state"],
                        "friendly_name": ent.get("attributes", {}).get("friendly_name", ""),
                        "last_changed": ent.get("last_changed", ""),
                    }}
                elif name == "local_homeassistant_call_service":
                    domain = args.get("domain", "")
                    service = args.get("service", "")
                    entity_id = args.get("entity_id", "")
                    data = args.get("data", {})
                    if not domain or not service or not entity_id:
                        return {"error": "domain, service, and entity_id are required"}
                    payload = {"entity_id": entity_id}
                    if isinstance(data, dict):
                        payload.update(data)
                    r = _req.post(
                        f"{hass_url}/api/services/{domain}/{service}",
                        headers=headers, json=payload, timeout=10,
                    )
                    r.raise_for_status()
                    return {"result": {"status": "called", "service": f"{domain}.{service}", "entity_id": entity_id}}
                elif name == "local_homeassistant_get_services":
                    r = _req.get(f"{hass_url}/api/services", headers=headers, timeout=10)
                    r.raise_for_status()
                    services = r.json()
                    domains = {}
                    for svc in services:
                        domain = svc.get("domain", "")
                        svc_list = list(svc.get("services", {}).keys())
                        domains[domain] = svc_list
                    return {"result": {"domains": domains}}
                else:
                    return {"error": f"Unknown HA tool: {name}"}
            except Exception as exc:
                logger.exception("HA tool %s failed", name)
                return {"error": f"HA tool failed: {exc}"}

        else:
            return {"error": f"Unknown local tool: {name}"}

    except Exception as exc:
        logger.exception("Local tool %s failed", name)
        # Criterion #8 — play the error sfx for any uncaught tool failure
        try:
            from sfx import play_sfx
            play_sfx("error")
        except Exception:
            pass
        return {"error": f"{type(exc).__name__}: {exc}"}


from bridge_decls import (
    _SPOTIFY_FUNCTION_DECLARATIONS,
    _WEB_FUNCTION_DECLARATIONS,
    _GITHUB_FUNCTION_DECLARATIONS,
    _HOMEASSISTANT_FUNCTION_DECLARATIONS,
    _LOCAL_FUNCTION_DECLARATIONS,
    _SYSINSPECT_FUNCTION_DECLARATIONS,
    HA_VOICE_TOOLS_ENABLED,
    LOCAL_VOICE_TOOLS_ENABLED,
    SYSINSPECT_VOICE_TOOLS_ENABLED,
)


__all__ = ['_run_github_tool', '_SYSINSPECT_ALLOWED_PREFIXES', '_sysinspect_path_allowed', '_run_sysinspect_tool', '_ensure_hermes_agent_path', '_run_spotify_tool', '_VOICE_DOMAIN_ALIASES', '_normalize_voice_web_text', '_normalize_voice_web_args', '_basic_extract_url', '_basic_web_extract', '_run_web_tool', '_CalcVisitor', '_run_local_tool', 'HA_VOICE_TOOLS_ENABLED', 'LOCAL_VOICE_TOOLS_ENABLED', 'SYSINSPECT_VOICE_TOOLS_ENABLED', '_GITHUB_FUNCTION_DECLARATIONS', '_HOMEASSISTANT_FUNCTION_DECLARATIONS', '_LOCAL_FUNCTION_DECLARATIONS', '_SPOTIFY_FUNCTION_DECLARATIONS', '_SYSINSPECT_FUNCTION_DECLARATIONS', '_WEB_FUNCTION_DECLARATIONS']
__all__ = [n for n in __all__ if n in globals()]
