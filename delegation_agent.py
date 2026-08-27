"""
delegation_agent.py — Multi-CLI delegation framework for the voice bridge.

Supports:
  - opencode     (opencode run)
  - codex        (codex exec)
  - gemini       (gemini ...)
  - numasec      (numasec ...)
  - hermes-api   (Hermes API server HTTP)

Flow:
  1. Gemini calls local_delegate_start(goal)
  2. Framework returns at most one clarifying question when ambiguity blocks action
  3. Gemini asks user in voice only if needed
  4. Gemini calls local_delegate_suggest(goal, size, scope, complexity, platform_hint)
  5. Framework checks rate limits, suggests platform, estimates time
  6. User confirms
  7. Gemini calls local_delegate_assemble(goal, subgoals, platform)
  8. Framework builds platform-optimized system prompt
  9. Gemini calls local_delegate_execute(prompt, platform)
  10. Spawns CLI, reports session_id, watcher fires on progress
"""

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("delegation-agent")

# ── Platform definitions ─────────────────────────────────────────────────
PLATFORMS = {
    "opencode": {
        "name": "OpenCode",
        "binary": "/home/caps/.local/bin/opencode",
        "max_context": 128_000,
        "strengths": ["code generation", "refactoring", "test writing", "debugging"],
        "weaknesses": ["web search", "large file IO"],
        "min_tokens": 4000,  # min tokens for a simple prompt
        "max_tokens": 126_000,
        "rate_limit_key": "opencode_requests",
    },
    "codex": {
        "name": "Codex CLI",
        "binary": "/home/caps/.npm-global/bin/codex",
        "max_context": 200_000,
        "strengths": ["reasoning", "complex code changes", "multi-file refactors"],
        "weaknesses": ["small quick edits (overhead)", "streaming"],
        "min_tokens": 8000,
        "max_tokens": 195_000,
        "rate_limit_key": "codex_requests",
    },
    "gemini": {
        "name": "Gemini CLI",
        "binary": "/home/caps/.npm-global/bin/gemini",
        "max_context": 1_000_000,
        "strengths": ["huge context windows", "vision", "audio understanding"],
        "weaknesses": ["code execution", "tool use"],
        "min_tokens": 1000,
        "max_tokens": 900_000,
        "rate_limit_key": "gemini_tokens",
    },
    "numasec": {
        "name": "Numasec",
        "binary": "/home/caps/.npm-global/bin/numasec",
        "max_context": 128_000,
        "strengths": ["security analysis", "code review", "vulnerability scanning"],
        "weaknesses": ["general coding", "web tasks"],
        "min_tokens": 2000,
        "max_tokens": 120_000,
        "rate_limit_key": "numasec_requests",
    },
    "hermes-api": {
        "name": "Hermes API Server",
        "binary": None,  # HTTP, not CLI
        "max_context": None,  # depends on upstream model
        "strengths": ["any task the Hermes agent can do", "tool access", "multi-step planning"],
        "weaknesses": ["no direct voice", "async dispatch"],
        "min_tokens": None,
        "max_tokens": None,
        "rate_limit_key": "hermes_dispatch",
        "api_config": {
            "host": os.getenv("API_SERVER_HOST", "127.0.0.1"),
            "port": int(os.getenv("API_SERVER_PORT", "0") or "0") or 8088,
            "key": os.getenv("API_SERVER_KEY", ""),
        },
    },
}

# ── Rate-limit tracking (per rolling window) ─────────────────────────────
_RATE_LIMITS: Dict[str, List[float]] = {}
_RATE_WINDOW_SECONDS = 3600  # 1 hour
_RATE_LIMIT_CAPS = {
    "opencode_requests": 100,  # requests per hour (estimated)
    "codex_requests": 50,      # requests per hour (conservative, no auth wall)
    "gemini_tokens": 1_000_000,  # tokens per hour (Free tier, ~1500/min)
    "numasec_requests": 60,    # per hour
    "hermes_dispatch": 200,    # per hour
}


# ── Fallback chain (criterion #5: "fix broken tools via neighbors") ──────
# When a platform is marked broken (binary missing, rate-limited, auth
# failed, persistent error in tmux log), the next delegation on that
# platform auto-routes to the first healthy neighbor in this list.
_FALLBACK_CHAIN: Dict[str, List[str]] = {
    "codex":       ["opencode"],
    "opencode":    ["codex"],
    "numasec":     ["opencode", "codex"],
    "gemini":      ["opencode", "codex"],
    "hermes-api":  ["opencode", "codex"],
}

# Marked-broken platforms persist to disk so the flag survives bridge
# restarts. Format: {pid: {reason, marked_at_monotonic, ttl_seconds}}
_HEALTH_PATH = Path.home() / ".hermes" / "voice-platform-health.json"
_HEALTH_DEFAULT_TTL = 600  # 10 min — re-try after this


def _load_health() -> Dict[str, Dict[str, Any]]:
    try:
        if _HEALTH_PATH.exists():
            return json.loads(_HEALTH_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("voice-platform-health: load failed: %s", exc)
    return {}


def _save_health(health: Dict[str, Dict[str, Any]]) -> None:
    try:
        _HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HEALTH_PATH.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("voice-platform-health: save failed: %s", exc)


def mark_platform_broken(platform: str, reason: str, ttl_seconds: int = _HEALTH_DEFAULT_TTL) -> None:
    """Flag a platform as unhealthy. Persists to disk with a TTL.

    Suggestion / execute flows will skip this platform until the TTL
    expires, and `local_delegate_execute` will auto-route the first
    subsequent call on this platform to the next healthy neighbor.
    """
    health = _load_health()
    health[platform] = {
        "reason": reason[:280],
        "marked_at": time.time(),
        "expires_at": time.time() + ttl_seconds,
        "ttl_seconds": ttl_seconds,
    }
    _save_health(health)
    logger.warning("voice-platform-health: marked %s broken — %s (ttl=%ds)", platform, reason, ttl_seconds)
    # Best-effort webhook so the agent can narrate it
    try:
        from webhook_dispatcher import emit_fallback_event  # type: ignore
        emit_fallback_event(platform, reason[:200], list(_FALLBACK_CHAIN.get(platform, [])))
    except Exception:
        pass


def clear_platform_health(platform: Optional[str] = None) -> None:
    """Clear health flags — pass a platform to clear one, or None to clear all."""
    if platform is None:
        _save_health({})
        return
    health = _load_health()
    health.pop(platform, None)
    _save_health(health)


def get_health_snapshot() -> Dict[str, Any]:
    """Read current health state, pruning expired entries. Returns {pid: {reason, expires_in}}."""
    now = time.time()
    health = _load_health()
    pruned = {}
    expired = []
    for pid, entry in health.items():
        if entry.get("expires_at", 0) <= now:
            expired.append(pid)
            continue
        pruned[pid] = {
            "reason": entry.get("reason", "?"),
            "expires_in_seconds": int(entry.get("expires_at", 0) - now),
        }
    if expired:
        for pid in expired:
            health.pop(pid, None)
        _save_health(health)
    return pruned


def is_platform_healthy(platform: str) -> bool:
    snapshot = get_health_snapshot()
    return platform not in snapshot


def choose_fallback(platform: str) -> Optional[str]:
    """Return the first healthy neighbor in FALLBACK_CHAIN, or None."""
    for neighbor in _FALLBACK_CHAIN.get(platform, []):
        if is_platform_healthy(neighbor):
            return neighbor
    return None


# Patterns in a CLI's tmux log that mean "this platform is broken right now"
# (not just a one-off task failure). Auto-fallback fires when any of these
# appear within the first ~5s of log output.
_BROKEN_LOG_PATTERNS = [
    re.compile(r"\b(?:HTTP\s*|status[: ]?)\s*401\b", re.I),
    re.compile(r"\b(?:HTTP\s*|status[: ]?)\s*403\b", re.I),
    re.compile(r"\b(?:HTTP\s*|status[: ]?)\s*429\b", re.I),
    re.compile(r"\b(?:HTTP\s*|status[: ]?)\s*5\d\d\b", re.I),
    re.compile(r"\brate[- ]limit", re.I),
    re.compile(r"\b(?:command|program)\s+not\s+found\b", re.I),
    re.compile(r"\bno\s+such\s+file\b", re.I),
    re.compile(r"\bpermission\s+denied\b", re.I),
    re.compile(r"\bauth(?:entication|orization)?\s+(?:failed|error)\b", re.I),
    re.compile(r"\b(?:api[_-]?key|token)\s+(?:invalid|expired|missing)\b", re.I),
    re.compile(r"\bconnection\s+refused\b", re.I),
    re.compile(r"\b(?:ollama|openrouter)\s+(?:error|unavailable)\b", re.I),
    re.compile(r"\b(?:free\s*tier|quota)\s+exceeded\b", re.I),
    re.compile(r"\brequires?\s+(?:a\s+)?subscription\b|\bextra\s+usage\b", re.I),
    re.compile(r"^\s*opencode\s+run\s+\[message\.\.\]", re.I | re.M),
    re.compile(r"^\s*Traceback\s+\(most recent call last\)", re.M),
]


def detect_broken_log(log_path: str, head_bytes: int = 4096) -> Optional[str]:
    """Return a short reason string if the log shows the platform is broken, else None."""
    try:
        p = Path(log_path)
        if not p.exists():
            return None
        head = p.read_text(errors="replace")[:head_bytes]
        for pat in _BROKEN_LOG_PATTERNS:
            m = pat.search(head)
            if m:
                snippet = head[max(0, m.start() - 20):m.end() + 60].strip().replace("\n", " ")
                return f"log matched {pat.pattern!r}: {snippet[:120]}"
    except Exception as exc:
        logger.debug("detect_broken_log: %s", exc)
    return None


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SENSITIVE_LOG_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization)"
    r"(\s*[:=]\s*)(\S+)"
)
_BEARER_LOG_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*")
_KEY_SHAPE_RE = re.compile(r"\b(?:AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{16,})\b")


def _sanitize_delegation_output(text: str, max_chars: int = 1600) -> str:
    cleaned = _ANSI_ESCAPE_RE.sub("", text).replace("\x00", "")
    cleaned = _SENSITIVE_LOG_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", cleaned)
    cleaned = _BEARER_LOG_RE.sub("Bearer [REDACTED]", cleaned)
    cleaned = _KEY_SHAPE_RE.sub("[REDACTED]", cleaned)
    return cleaned[-max_chars:].strip()


def _tmux_window_active(window_name: str) -> bool:
    if not window_name:
        return False
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", "delegate", "-F", "#{window_name}"],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError("could not inspect delegation tmux state") from exc
    if result.returncode != 0:
        error = str(result.stderr or "").lower()
        if any(marker in error for marker in ("no server running", "can't find session", "no such session")):
            return False
        raise RuntimeError("could not inspect delegation tmux state")
    return window_name in {line.strip() for line in result.stdout.splitlines()}


def _stop_delegation_window(result: Dict[str, Any]) -> bool:
    """Stop an original worker and prove it is inactive before fallback."""
    window = str(result.get("tmux_window") or "")
    if not window:
        return True
    subprocess.run(
        ["tmux", "kill-window", "-t", f"delegate:{window}"],
        capture_output=True,
        text=True,
    )
    try:
        stopped = not _tmux_window_active(window)
        if stopped:
            _cleanup_ephemeral_state(result)
        return stopped
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def _cleanup_ephemeral_state(result: Dict[str, Any]) -> None:
    value = str(result.get("ephemeral_state_path") or "")
    if not value:
        return
    root = Path(os.getenv("SORA_DELEGATION_STATE_ROOT", "/tmp/sora-live-delegation-state")).resolve()
    path = Path(value).resolve()
    if path != root and root in path.parents:
        shutil.rmtree(path, ignore_errors=True)


def _prepare_opencode_state(session_id: str) -> tuple[Path, Path]:
    source = Path(
        os.getenv("SORA_OPENCODE_DATA_DIR", str(Path.home() / ".local" / "share" / "opencode")),
    ).expanduser().resolve()
    if not source.is_dir():
        raise OSError("OpenCode data directory is unavailable")
    root = Path(os.getenv("SORA_DELEGATION_STATE_ROOT", "/tmp/sora-live-delegation-state")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    cutoff = time.time() - 3600
    for stale in root.iterdir():
        try:
            if stale.is_dir() and stale.stat().st_mtime < cutoff:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            continue
    safe_session = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id).strip("-.")[:64] or "session"
    state = Path(tempfile.mkdtemp(prefix=f"{safe_session}-", dir=root))
    state.chmod(0o700)
    for name in ("auth.json", "account.json"):
        credential = source / name
        if credential.is_file():
            destination = state / name
            shutil.copyfile(credential, destination)
            destination.chmod(0o600)
    return source, state


def _prepare_delegation_run_files(platform: str, session_id: str) -> Dict[str, str]:
    """Create unpredictable, owner-only runtime artifacts for one delegation."""
    root = Path(
        os.getenv("SORA_DELEGATION_RUN_ROOT", "/tmp/sora-live-delegation-runs"),
    ).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    cutoff = time.time() - 86_400
    for stale in root.iterdir():
        try:
            if stale.is_dir() and stale.stat().st_mtime < cutoff:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            continue
    safe_platform = re.sub(r"[^A-Za-z0-9_-]+", "-", platform).strip("-")[:24] or "agent"
    safe_session = re.sub(r"[^A-Za-z0-9_-]+", "-", session_id).strip("-")[:40] or "session"
    run_dir = Path(tempfile.mkdtemp(prefix=f"{safe_platform}-{safe_session}-", dir=root))
    run_dir.chmod(0o700)
    log_path = run_dir / "output.log"
    status_path = run_dir / "output.log.status"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for artifact in (log_path, status_path):
        fd = os.open(artifact, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    return {
        "run_dir": str(run_dir),
        "log_path": str(log_path),
        "status_path": str(status_path),
    }


def _record_delegation(result: Dict[str, Any]) -> None:
    session_id = str(result.get("session_id") or "")
    if not is_valid_session_id(session_id):
        return
    snapshot = dict(result)
    _ACTIVE_DELEGATIONS[session_id] = snapshot
    run_dir_raw = str(snapshot.get("run_dir") or "")
    if not run_dir_raw:
        return
    run_dir = Path(run_dir_raw).resolve()
    metadata = run_dir / "metadata.json"
    safe = {
        key: snapshot[key]
        for key in (
            "session_id",
            "active_platform",
            "tmux_window",
            "log_path",
            "status_path",
            "run_dir",
        )
        if key in snapshot
    }
    try:
        fd = os.open(
            metadata,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(safe, handle, separators=(",", ":"))
            handle.write("\n")
    except OSError:
        return


def lookup_delegation(session_id: str, platform: str) -> Optional[Dict[str, Any]]:
    """Find a recorded delegation without reconstructing attacker-guessable paths."""
    if not is_valid_session_id(session_id) or platform not in {"opencode", "codex"}:
        return None
    active = _ACTIVE_DELEGATIONS.get(session_id)
    if active and str(active.get("active_platform") or platform) == platform:
        return dict(active)
    root = Path(
        os.getenv("SORA_DELEGATION_RUN_ROOT", "/tmp/sora-live-delegation-runs"),
    ).expanduser().resolve()
    if not root.is_dir():
        return None
    try:
        candidates = sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)[:256]
    except OSError:
        return None
    for run_dir in candidates:
        metadata = run_dir / "metadata.json"
        try:
            if not run_dir.is_dir() or run_dir.parent != root or metadata.is_symlink():
                continue
            value = json.loads(metadata.read_text(encoding="utf-8"))
            if value.get("session_id") != session_id or value.get("active_platform") != platform:
                continue
            for key in ("log_path", "status_path"):
                target = Path(str(value.get(key) or "")).resolve()
                if target.parent != run_dir.resolve():
                    raise ValueError("delegation metadata escaped its private run directory")
            return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def observe_delegation(
    result: Dict[str, Any],
    *,
    wait_seconds: float = 3.0,
) -> Dict[str, Any]:
    """Read back a spawned delegation before reporting success to Live."""
    observed = dict(result)
    log_path = str(observed.get("log_path") or "")
    status_path = str(observed.get("status_path") or (f"{log_path}.status" if log_path else ""))
    window = str(observed.get("tmux_window") or "")
    if not log_path:
        return observed

    deadline = time.monotonic() + max(0.0, min(wait_seconds, 15.0))
    try:
        while _tmux_window_active(window) and time.monotonic() < deadline:
            time.sleep(0.2)
    except RuntimeError:
        observed["status"] = "failed"
        observed["error"] = "delegation state could not be inspected"
        return observed

    path = Path(log_path)
    text = path.read_text(errors="replace") if path.exists() else ""
    output_tail = _sanitize_delegation_output(text)
    try:
        active = _tmux_window_active(window)
    except RuntimeError:
        observed["status"] = "failed"
        observed["error"] = "delegation state could not be inspected"
        if output_tail:
            observed["output_tail"] = output_tail
        return observed
    exit_code: Optional[int] = None
    if status_path:
        try:
            raw_status = Path(status_path).read_text(encoding="utf-8").strip()
            exit_code = int(raw_status)
        except (FileNotFoundError, OSError, ValueError):
            exit_code = None
    if output_tail:
        observed["output_tail"] = output_tail
    if active:
        observed["status"] = "running"
    elif exit_code == 0:
        observed["status"] = "completed"
        observed["exit_code"] = 0
    elif exit_code is not None:
        observed["status"] = "failed"
        observed["exit_code"] = exit_code
        observed["error"] = f"delegation exited with code {exit_code}"
    else:
        observed["status"] = "failed"
        observed["error"] = "delegation exited without a completion marker"
    return observed


def preflight_platform(platform: str) -> Optional[str]:
    """Return a safe reason for failures provable without launching a task."""
    info = PLATFORMS.get(platform) or {}
    binary = info.get("binary")
    if binary and not Path(str(binary)).exists():
        return f"{platform} binary is unavailable"
    if platform != "codex":
        return None

    auth_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        auth = json.loads((auth_home / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "Codex authentication is missing"
    token = str((auth.get("tokens") or {}).get("access_token") or "")
    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        expires_at = float(payload.get("exp") or 0)
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if expires_at and expires_at <= time.time() + 30:
        return "Codex access token is expired; interactive login is required"
    return None


def execute_with_fallback(
    prompt: str,
    platform: str,
    session_id: str,
    workdir: Optional[str] = None,
    health_check_delay: float = 5.0,
) -> Dict[str, Any]:
    """Spawn `platform`; if it appears broken within `health_check_delay`
    seconds, auto-respawn on the first healthy neighbor from FALLBACK_CHAIN.

    Returns a composite dict that always includes the original platform,
    the platform that actually ran, and (if a fallback fired) the reason.
    """
    if not is_platform_healthy(platform):
        neighbor = choose_fallback(platform)
        if neighbor:
            inner = execute_with_fallback(prompt, neighbor, session_id, workdir, health_check_delay)
            inner["requested_platform"] = platform
            inner["active_platform"] = inner.get("active_platform", neighbor)
            # If a deeper layer already set fallback_from, keep that chain
            if "fallback_from" not in inner:
                inner["fallback_from"] = platform
            if "fallback_reason" not in inner:
                inner["fallback_reason"] = f"platform '{platform}' was marked broken (pre-check)"
            return inner
        return {
            "error": f"platform '{platform}' is marked broken and no healthy neighbor found",
            "requested_platform": platform,
            "active_platform": platform,
            "health": get_health_snapshot(),
        }

    preflight_error = preflight_platform(platform)
    if preflight_error:
        mark_platform_broken(platform, preflight_error)
        neighbor = choose_fallback(platform)
        if neighbor:
            inner = execute_with_fallback(
                prompt,
                neighbor,
                session_id + "-fb",
                workdir,
                health_check_delay,
            )
            inner["requested_platform"] = platform
            inner["active_platform"] = inner.get("active_platform", neighbor)
            inner["fallback_from"] = platform
            inner["fallback_reason"] = preflight_error
            return inner
        return {
            "error": preflight_error,
            "requested_platform": platform,
            "active_platform": platform,
        }

    result = execute_delegation(prompt, platform, session_id, workdir)
    if "error" in result:
        # Hard error before tmux spawn — treat as broken, try neighbor
        mark_platform_broken(platform, f"execute_delegation error: {str(result['error'])[:160]}")
        neighbor = choose_fallback(platform)
        if neighbor:
            inner = execute_with_fallback(prompt, neighbor, session_id, workdir, health_check_delay)
            inner["requested_platform"] = platform
            inner["active_platform"] = inner.get("active_platform", neighbor)
            if "fallback_from" not in inner:
                inner["fallback_from"] = platform
            inner["fallback_reason"] = str(result["error"])[:200]
            return inner
        result["requested_platform"] = platform
        result["active_platform"] = platform
        return result

    result.setdefault("requested_platform", platform)
    result.setdefault("active_platform", platform)

    log_path = result.get("log_path")
    if log_path and health_check_delay > 0:
        # Never infer process failure from free-form output while a worker is
        # running or after it wrote a successful completion marker. Reviewed
        # text may legitimately quote the same error phrases we detect.
        time.sleep(health_check_delay)
        observed = observe_delegation(result, wait_seconds=0)
        observed.setdefault("requested_platform", platform)
        observed.setdefault("active_platform", platform)
        if observed.get("status") in {"running", "completed"}:
            return observed
        if observed.get("exit_code") is None:
            return observed
        reason = detect_broken_log(str(log_path))
        if reason:
            mark_platform_broken(platform, reason)
            neighbor = choose_fallback(platform)
            if neighbor:
                if not _stop_delegation_window(result):
                    observed["status"] = "failed"
                    observed["error"] = "original delegation could not be stopped; fallback was not launched"
                    observed["health_warning"] = reason
                    return observed
                inner = execute_with_fallback(prompt, neighbor, session_id + "-fb", workdir, health_check_delay)
                inner["fallback_from"] = platform
                inner["fallback_reason"] = reason
                inner["original_log_path"] = log_path
                return inner
            observed["health_warning"] = reason
        return observed

    result.setdefault("requested_platform", platform)
    result.setdefault("active_platform", platform)
    return result


def _check_rate_limit(rate_limit_key: str) -> Tuple[bool, int, int]:
    """Returns (allowed, used_this_hour, cap)."""
    now = time.monotonic()
    window = _RATE_LIMITS.get(rate_limit_key, [])
    # Prune entries outside the 1h window
    window = [t for t in window if (now - t) < _RATE_WINDOW_SECONDS]
    _RATE_LIMITS[rate_limit_key] = window
    cap = _RATE_LIMIT_CAPS.get(rate_limit_key, 9999)
    used = len(window)
    allowed = used < cap
    return allowed, used, cap


def _record_rate_limit(rate_limit_key: str) -> None:
    now = time.monotonic()
    window = _RATE_LIMITS.get(rate_limit_key, [])
    window.append(now)
    _RATE_LIMITS[rate_limit_key] = [t for t in window if (now - t) < _RATE_WINDOW_SECONDS]


def get_all_rate_limits() -> Dict[str, Dict[str, Any]]:
    """Return rate-limit status for all platforms."""
    out = {}
    for pid, info in PLATFORMS.items():
        rlk = info.get("rate_limit_key")
        if not rlk:
            out[pid] = {"available": True, "used": 0, "cap": 9999}
            continue
        allowed, used, cap = _check_rate_limit(rlk)
        out[pid] = {"available": allowed, "used": used, "cap": cap}
    return out


# ── ETA estimation (based on project complexity) ─────────────────────────
# rough multipliers derived from prior builds (calibratable via
# local_delegate_learn_eta)
_ETA_BY_SIZE = {
    "tiny": 60,       # 1 min — single file edit
    "small": 300,     # 5 min — small feature
    "medium": 900,    # 15 min — multi-file refactor
    "large": 3600,    # 1 hr — new feature across many files
    "xlarge": 7200,   # 2 hr — significant project work
}
_USER_ETA_CORRECTION = {}  # {user_id: multiplier}


def estimate_eta(project_size: str, complexity: str, user_id: Optional[str] = None) -> int:
    """Return estimated seconds for a project."""
    base_sec = _ETA_BY_SIZE.get(project_size, 300)
    if complexity == "low":
        base_sec = int(base_sec * 0.6)
    elif complexity == "high":
        base_sec = int(base_sec * 1.8)
    elif complexity == "extreme":
        base_sec = int(base_sec * 3.0)
    if user_id and user_id in _USER_ETA_CORRECTION:
        base_sec = int(base_sec * _USER_ETA_CORRECTION[user_id])
    return min(base_sec, 14400)  # cap at 4 hours


# ── Prompt assembly ──────────────────────────────────────────────────────
def assemble_prompt(
    goal: str,
    subgoals: List[str],
    platform: str,
    project_root: Optional[str] = None,
) -> str:
    """Build a platform-optimized system prompt for the target CLI."""
    platform_info = PLATFORMS.get(platform, {})
    prompt_parts = [
        "# Goal",
        goal.strip(),
    ]
    if subgoals:
        prompt_parts.append("")
        prompt_parts.append("## Sub-goals (in order)")
        for i, sg in enumerate(subgoals, 1):
            prompt_parts.append(f"  {i}. {sg.strip()}")

    prompt_parts.append("")
    prompt_parts.append("## Constraints")
    prompt_parts.append("- Do NOT hallucinate files or dependencies.")
    prompt_parts.append("- Ask before destructive operations (rm, drop table, etc.).")
    prompt_parts.append("- If stuck, explain what you tried and suggest next steps.")
    prompt_parts.append("- Keep commits atomic and messages clear.")

    if project_root:
        prompt_parts.append("")
        prompt_parts.append(f"```\ncd {project_root}\n```")
        prompt_parts.append(f"Project root: {project_root}")

    # Platform-specific optimizations
    if platform == "codex":
        prompt_parts.append("")
        prompt_parts.append("## Codex-specific")
        prompt_parts.append("- Stay inside the configured workspace-write sandbox.")
        prompt_parts.append("- After each change, verify the file compiles.")
    elif platform == "opencode":
        prompt_parts.append("")
        prompt_parts.append("## OpenCode-specific")
        prompt_parts.append("- Use the 'run' mode for one-shot task execution.")
        prompt_parts.append("- Model: auto-resolved by the CLI.")
    elif platform == "gemini":
        prompt_parts.append("")
        prompt_parts.append("## Gemini CLI-specific")
        prompt_parts.append("- Use the full context window for analysis.")
        prompt_parts.append("- Prefer structured output (JSON).")
    elif platform == "numasec":
        prompt_parts.append("")
        prompt_parts.append("## Numasec-specific")
        prompt_parts.append("- Focus on security analysis, vulnerability scanning.")
        prompt_parts.append("- Output severity-graded findings.")
    elif platform == "hermes-api":
        prompt_parts.append("")
        prompt_parts.append("## Hermes API-specific")
        prompt_parts.append("- The agent has full Hermes tool access.")
        prompt_parts.append("- Use the shortest path to the result.")

    return "\n".join(prompt_parts)


# ── CLI execution ────────────────────────────────────────────────────────
_ACTIVE_DELEGATIONS: Dict[str, Dict[str, Any]] = {}
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


def is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.fullmatch(session_id)) and session_id not in {".", ".."}


def _delegation_roots() -> tuple[Path, tuple[Path, ...]]:
    scratch = Path(os.getenv("SORA_DELEGATION_SCRATCH_ROOT", "/tmp/sora-live-delegations")).expanduser().resolve()
    configured = os.getenv("SORA_DELEGATION_ALLOWED_ROOTS", "")
    roots = [scratch]
    for raw in configured.split(os.pathsep):
        value = raw.strip()
        if not value:
            continue
        candidate = Path(value).expanduser().resolve()
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return scratch, tuple(roots)


def _inside_allowed_root(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def execute_delegation(
    prompt: str,
    platform: str,
    session_id: str,
    workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a prompt to the selected CLI and return a session handle.

    For CLI platforms, spawns a subprocess (background) or tmux window.
    For hermes-api, sends an HTTP POST to the Hermes API server.
    """
    if not is_valid_session_id(session_id):
        return {"error": "invalid delegation session_id"}
    platform_info = PLATFORMS.get(platform)
    if not platform_info:
        return {"error": f"Unknown platform: {platform}"}

    scratch_root, allowed_roots = _delegation_roots()
    if workdir:
        resolved_workdir = Path(workdir).expanduser().resolve()
        if not resolved_workdir.is_dir():
            return {"error": f"workdir does not exist or is not a directory: {workdir}"}
        if not _inside_allowed_root(resolved_workdir, allowed_roots):
            return {"error": "workdir is outside the configured allowed delegation roots"}
    else:
        safe_session = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id).strip("-.")[:96] or "session"
        resolved_workdir = scratch_root / safe_session
        try:
            resolved_workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not (resolved_workdir / ".git").exists():
                initialized = subprocess.run(
                    ["git", "init", "-q", str(resolved_workdir)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if initialized.returncode != 0:
                    return {"error": "could not initialize isolated delegation workdir"}
        except (OSError, subprocess.SubprocessError):
            return {"error": "could not prepare isolated delegation workdir"}
    workdir = str(resolved_workdir)

    if platform == "opencode":
        result = _run_opencode(prompt, session_id, workdir, platform_info)
    elif platform == "codex":
        result = _run_codex(prompt, session_id, workdir, platform_info)
    elif platform == "gemini":
        result = _run_gemini_cli(prompt, session_id, workdir, platform_info)
    elif platform == "numasec":
        result = _run_numasec(prompt, session_id, workdir, platform_info)
    elif platform == "hermes-api":
        result = _run_hermes_api(prompt, session_id, platform_info)
    else:
        return {"error": f"No executor for platform: {platform}"}
    result.setdefault("session_id", session_id)
    result.setdefault("active_platform", platform)
    if "error" not in result:
        _record_delegation(result)
    return result


def _tmux_exec(session_name: str, window_name: str, cmd: str, log_path: str) -> Dict[str, Any]:
    """Run a command in a tmux window and return session info."""
    window = f"del-{window_name}"
    status_path = f"{log_path}.status"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        for artifact in (log_path, status_path):
            fd = os.open(artifact, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
    except OSError:
        return {"error": "could not prepare delegation status files"}
    # Kill prior window with same name
    subprocess.run(["tmux", "kill-window", "-t", f"delegate:{window}"], capture_output=True)
    # Create tmux session if needed
    if subprocess.run(["tmux", "has-session", "-t", "delegate"], capture_output=True).returncode != 0:
        created = subprocess.run(
            ["tmux", "new-session", "-d", "-s", "delegate", "-n", "_init"],
            capture_output=True,
        )
        if created.returncode != 0:
            return {"error": "could not create delegation tmux session"}
    # Spawn in new window
    import shlex
    full_cmd = (
        "set -o pipefail; "
        f"cd {shlex.quote(session_name)} 2>/dev/null || "
        f"{{ printf '125\\n' > {shlex.quote(status_path)}; exit 125; }}; "
        f"{cmd} 2>&1 | tee {shlex.quote(log_path)}; "
        "rc=${PIPESTATUS[0]}; "
        f"printf '%s\\n' \"$rc\" > {shlex.quote(status_path)}; "
        "exit \"$rc\""
    )
    launched = subprocess.run(
        ["tmux", "new-window", "-d", "-t", "delegate", "-n", window, "bash", "-c", full_cmd],
        capture_output=True,
    )
    if launched.returncode != 0:
        return {"error": "could not launch delegation tmux window"}
    return {
        "session_id": session_name,
        "tmux_window": window,
        "log_path": log_path,
        "status_path": status_path,
    }


def _opencode_permission_policy() -> Dict[str, Any]:
    # The OpenCode process needs provider network access, but delegated tools
    # must not get a shell or arbitrary egress beside the mounted auth state.
    # external_directory blocks read/edit/glob/grep against the auth overlay.
    return {
        "*": "deny",
        "read": "allow",
        "edit": "allow",
        "glob": "allow",
        "grep": "allow",
        "bash": "deny",
        "external_directory": "deny",
        "question": "deny",
        "task": "deny",
        "webfetch": "deny",
        "websearch": "deny",
    }


def _run_opencode(prompt: str, session_id: str, workdir: str, info: Dict[str, Any]) -> Dict[str, Any]:
    binary = info["binary"]
    if not Path(binary).exists():
        return {"error": f"opencode not found at {binary}"}
    import shlex
    prepared = _prepare_delegation_run_files("opencode", session_id)
    log_path = prepared["log_path"]
    model = os.getenv("SORA_OPENCODE_MODEL", "opencode/mimo-v2.5-free")
    bwrap = os.getenv("SORA_DELEGATION_BWRAP", "/usr/bin/bwrap")
    if not Path(bwrap).is_file():
        return {"error": "bubblewrap is required for sandboxed OpenCode delegation"}
    try:
        opencode_data, ephemeral_state = _prepare_opencode_state(session_id)
    except OSError as exc:
        return {"error": str(exc)}
    del opencode_data
    real_binary = str(Path(binary).resolve())
    permission = json.dumps(_opencode_permission_policy(), separators=(",", ":"))
    etc_binds = []
    for source in (
        "/etc/resolv.conf",
        "/etc/hosts",
        "/etc/nsswitch.conf",
        "/etc/gai.conf",
        "/etc/localtime",
        "/etc/ssl/certs",
    ):
        if Path(source).exists():
            etc_binds.extend(("--ro-bind", source, source))
    args = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--share-net",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--dir",
        "/etc",
        "--dir",
        "/etc/ssl",
        *etc_binds,
        "--dir",
        "/opt",
        "--dir",
        "/opt/opencode",
        "--ro-bind",
        real_binary,
        "/opt/opencode/opencode",
        "--dir",
        "/home",
        "--dir",
        "/home/sora",
        "--dir",
        "/home/sora/.config",
        "--dir",
        "/home/sora/.local",
        "--dir",
        "/home/sora/.local/share",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/workspace",
        "--bind",
        workdir,
        "/workspace",
        "--bind",
        str(ephemeral_state),
        "/home/sora/.local/share/opencode",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--setenv",
        "HOME",
        "/home/sora",
        "--setenv",
        "USER",
        "sora",
        "--setenv",
        "LOGNAME",
        "sora",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "XDG_CONFIG_HOME",
        "/home/sora/.config",
        "--setenv",
        "XDG_DATA_HOME",
        "/home/sora/.local/share",
        "--setenv",
        "XDG_CACHE_HOME",
        "/tmp/cache",
        "--setenv",
        "OPENCODE_PERMISSION",
        permission,
        "--setenv",
        "OPENCODE_DISABLE_AUTOUPDATE",
        "true",
        "--setenv",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS",
        "true",
        "--chdir",
        "/workspace",
        "--",
        "/opt/opencode/opencode",
        "run",
        "--pure",
        "--model",
        model,
        "--",
        prompt,
    ]
    sandbox_cmd = " ".join(shlex.quote(str(arg)) for arg in args)
    cleanup_script = "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)"
    cleanup_command = (
        f"/usr/bin/python3 -c {shlex.quote(cleanup_script)} "
        f"{shlex.quote(str(ephemeral_state))}"
    )
    compound = f"cleanup() {{ {cleanup_command}; }}; trap cleanup EXIT; {sandbox_cmd}"
    cmd = f"/bin/bash -c {shlex.quote(compound)}"
    result = _tmux_exec(workdir, f"oc-{session_id}", cmd, log_path)
    result.update(prepared)
    result["ephemeral_state_path"] = str(ephemeral_state)
    if "error" in result:
        _cleanup_ephemeral_state(result)
    result["session_id"] = session_id
    result["model"] = model
    return result


def _run_codex(prompt: str, session_id: str, workdir: str, info: Dict[str, Any]) -> Dict[str, Any]:
    binary = info["binary"]
    if not Path(binary).exists():
        return {"error": f"codex not found at {binary}"}
    import shlex
    prepared = _prepare_delegation_run_files("codex", session_id)
    log_path = prepared["log_path"]
    cmd = (
        f"{shlex.quote(binary)} exec --sandbox workspace-write --ephemeral "
        f"-C {shlex.quote(workdir)} -- {shlex.quote(prompt)}"
    )
    result = _tmux_exec(workdir, f"cd-{session_id}", cmd, log_path)
    result.update(prepared)
    result["session_id"] = session_id
    return result


def _run_gemini_cli(prompt: str, session_id: str, workdir: str, info: Dict[str, Any]) -> Dict[str, Any]:
    binary = info["binary"]
    if not Path(binary).exists():
        return {"error": f"gemini CLI not found at {binary}"}
    import shlex
    log_path = f"/tmp/delegate-gemini-{session_id}.log"
    # Gemini CLI takes a prompt as argument
    cmd = f"{binary} chat --text {shlex.quote(prompt)} 2>&1"
    return _tmux_exec(workdir, f"gm-{session_id}", cmd, log_path)


def _run_numasec(prompt: str, session_id: str, workdir: str, info: Dict[str, Any]) -> Dict[str, Any]:
    binary = info["binary"]
    if not Path(binary).exists():
        return {"error": f"numasec not found at {binary}"}
    import shlex
    log_path = f"/tmp/delegate-numasec-{session_id}.log"
    cmd = f"{binary} run {shlex.quote(prompt)} 2>&1"
    return _tmux_exec(workdir, f"ns-{session_id}", cmd, log_path)


def _run_hermes_api(prompt: str, session_id: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """Send a task to the Hermes API server as a fire-and-forget chat request."""
    api_cfg = info.get("api_config", {})
    host = api_cfg.get("host", "127.0.0.1")
    port = api_cfg.get("port", 8088)
    key = api_cfg.get("key", "")
    log_path = f"/tmp/delegate-hermes-{session_id}.log"
    try:
        import requests
        resp = requests.post(
            f"http://{host}:{port}/api/chat",
            json={"message": prompt, "key": key, "source": "voice-delegation"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("response", "") or data.get("message", "")
        with open(log_path, "w") as f:
            f.write(result)
        return {"session_id": session_id, "response": result[:500], "log_path": log_path}
    except Exception as exc:
        with open(log_path, "w") as f:
            f.write(f"[Hermes API dispatch failed: {exc}]")
        return {"session_id": session_id, "error": str(exc), "log_path": log_path}


# ── Tool interface (called from bridge.py) ───────────────────────────────

def suggest_platform(
    goal: str,
    project_size: str,
    scope: str,
    complexity: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze the task and suggest the best platform + ETA.

    Platforms currently marked broken in the health registry are filtered
    out before scoring. (criterion #5)
    """
    rates = get_all_rate_limits()
    broken = set(get_health_snapshot().keys())
    available = [
        pid for pid, r in rates.items()
        if r.get("available", False) and pid not in broken
    ]
    if not available:
        return {
            "error": "All healthy platforms are either rate-limited or marked broken. Please wait or run local_delegate_health action='clear'.",
            "rates": rates,
            "unhealthy": list(broken),
        }

    # Score each platform
    scores = {}
    for pid in available:
        info = PLATFORMS[pid]
        score = 50
        # Size-based scoring
        if project_size in ("tiny", "small"):
            if pid == "opencode":
                score += 30
            elif pid == "gemini":
                score += 20
        elif project_size in ("large", "xlarge"):
            if pid == "codex":
                score += 30
            elif pid == "gemini":
                score += 25
        # Complexity scoring
        if complexity == "high" and pid == "codex":
            score += 20
        if complexity == "extreme" and pid == "gemini":
            score += 30
        # Scope scoring
        if scope in ("code", "refactor") and pid in ("opencode", "codex"):
            score += 20
        if scope in ("security", "audit") and pid == "numasec":
            score += 35
        if scope in ("research", "analysis") and pid == "gemini":
            score += 25
        scores[pid] = score

    best = max(scores, key=scores.get)
    eta_sec = estimate_eta(project_size, complexity, user_id)
    eta_str = f"{eta_sec // 60}m {eta_sec % 60}s" if eta_sec >= 60 else f"{eta_sec}s"

    return {
        "suggestion": best,
        "reason": _explain_suggestion(best, project_size, complexity, scope, eta_sec),
        "estimated_eta_seconds": eta_sec,
        "estimated_eta_display": eta_str,
        "all_scores": scores,
        "rate_limits": rates,
        "available_platforms": available,
    }


def _explain_suggestion(platform: str, size: str, complexity: str, scope: str, eta_sec: int = 300) -> str:
    info = PLATFORMS.get(platform, {})
    name = info.get("name", platform)
    context = info.get("max_context", "?")
    ctx_str = f"{context // 1000}k tokens" if context else "flexible"
    eta_str = f"{eta_sec // 60}m{eta_sec % 60}s" if eta_sec >= 60 else f"{eta_sec}s"

    reasons = []
    if platform == "opencode":
        reasons.append(f"fastest for {size} tasks like this")
    elif platform == "codex":
        reasons.append(f"handles {complexity} complexity well")
    elif platform == "gemini":
        reasons.append(f"large context window ({ctx_str}) for scope={scope}")
    elif platform == "numasec":
        reasons.append(f"specialized for {scope} analysis")

    return f"I suggest **{name}** ({', '.join(reasons)}). ETA: **{eta_str}**"


def check_context_fit(goal: str, platform: str, project_root: Optional[str] = None) -> Dict[str, Any]:
    """Estimate whether the goal fits the platform's context window."""
    info = PLATFORMS.get(platform, {})
    max_ctx = info.get("max_context")
    if max_ctx is None:
        return {"fit": True, "reason": "platform has no fixed context limit"}
    # Rough heuristic: prompt is ~10 tokens per word
    word_count = len(goal.split())
    prompt_tokens_est = word_count * 10
    # If project_root, add directory listing size
    project_context_est = 0
    if project_root:
        try:
            out = subprocess.run(
                ["find", project_root, "-type", "f", "-name", "*.py", "-size", "-100k"],
                capture_output=True, timeout=5,
            )
            project_context_est = len(out.stdout.decode(errors="replace").splitlines()) * 200  # ~200 tokens per file
        except Exception:
            pass
    total_est = prompt_tokens_est + project_context_est
    fit = total_est <= max_ctx
    return {
        "fit": fit,
        "estimated_total_tokens": total_est,
        "prompt_tokens_est": prompt_tokens_est,
        "project_context_tokens_est": project_context_est,
        "platform_max_context": max_ctx,
        "warnings": [] if fit else [
            f"Estimated {total_est} tokens exceeds {platform}'s ~{max_ctx // 1000}k limit by "
            f"about {(total_est - max_ctx) // 1000}k tokens. Consider breaking the task into "
            f"smaller sub-goals or choosing a platform with a larger context window (Gemini CLI "
            f"supports up to ~1M tokens)."
        ],
    }
