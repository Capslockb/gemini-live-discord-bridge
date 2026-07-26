"""Extracted from bridge.py — part of the gemini-live-discord-bridge split. Do not edit in isolation; see bridge.py facade."""
import ast
import asyncio
import base64
import html
import json
import logging
import os
import queue
import random
import re
import subprocess
import sys
import time
import wave
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from typing import Any, Optional, Dict, List, Callable, Tuple

import numpy as np
logger = logging.getLogger("voice-live")
from bridge_config import VIDEO_MAX_BYTES
from bridge_core import VoiceLiveBridge
from bridge_email import _start_email_reminder_loop

HTTP_PORT = int(os.getenv("DISCORD_VOICE_LIVE_PORT", "18943"))


BRIDGE: Optional[VoiceLiveBridge] = None


async def handle_http_request(reader, writer):
    request_data = b""
    while True:
        line = await reader.readline()
        if not line or line == b"\r\n":
            break
        # Patch 9: cap the header section to prevent unbounded growth.
        if len(request_data) > 16 * 1024:
            break
        request_data += line
    request_text = request_data.decode("utf-8", errors="replace")
    lines = request_text.split("\r\n")
    if not lines:
        writer.close()
        return
    method_path = lines[0].split(" ")
    if len(method_path) < 2:
        writer.close()
        return
    method = method_path[0].upper()
    path = method_path[1]
    parsed_url = urlparse(path)
    route = parsed_url.path
    # Patch 9: parse headers so we can authenticate.
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.lower().strip()] = value.strip()
    response_body = ""
    status = 200
    # Patch 9: shared-secret auth for mutating routes. Read-only routes
    # (/health, /notes) remain anonymous so the agent's status checks
    # don't need to forward a token. The secret is generated at module
    # import in __init__.py and stored in the module attribute
    # CONTROL_API_SECRET; the bridge imports it back through the module
    # spec we shared there.
    MUTATING_ROUTES = {"/stop", "/say", "/frame", "/notify"}
    if route in MUTATING_ROUTES:
        # Look up the plugin's __init__ module via sys.modules under its real
        # import name (discord_voice_live, set when the plugin loader imports
        # this package). `from __init__ import` is fragile and fails when
        # bridge.py is loaded as a stand-alone spec.
        import sys as _sys
        _plugin_mod = _sys.modules.get("discord_voice_live")
        if _plugin_mod is None:
            # Fall back to the spec name used by _bridge_mod if the parent
            # package isn't installed under the conventional name.
            _plugin_mod = _sys.modules.get("discord_voice_live_bridge")
        if _plugin_mod is None:
            status = 500
            response_body = json.dumps({"error": "control secret unavailable"})
            response = _format_response(status, response_body, reason="INTERNAL")
            writer.write(response)
            await writer.drain()
            return
        _SECRET = getattr(_plugin_mod, "CONTROL_API_SECRET", "")
        if not _SECRET:
            status = 500
            response_body = json.dumps({"error": "control secret not initialised"})
            response = _format_response(status, response_body, reason="INTERNAL")
            writer.write(response)
            await writer.drain()
            return
        presented = headers.get("x-api-secret", "")
        # Constant-time compare to avoid timing leaks.
        if not hmac.compare_digest(presented, _SECRET):
            status = 401
            response_body = json.dumps({"error": "unauthorized"})
            response = _format_response(status, response_body, reason="UNAUTHORIZED")
            writer.write(response)
            await writer.drain()
            return
    if route == "/health":
        response_body = json.dumps(BRIDGE.health() if BRIDGE else {"status": "not_started", "running": False})
    elif route == "/stop":
        if BRIDGE and BRIDGE._running:
            await BRIDGE.stop()
            response_body = json.dumps({"status": "stopped"})
        else:
            response_body = json.dumps({"status": "not_running"})
    elif route == "/say":
        text = parse_qs(parsed_url.query).get("text", [""])[0]
        if BRIDGE and BRIDGE._running and text:
            await BRIDGE._gemini.send_text(text)
            response_body = json.dumps({"status": "sent", "text": text})
        else:
            response_body = json.dumps({"status": "error", "message": "Bridge not running or text missing"})
            status = 400
    elif route == "/frame":
        if not BRIDGE or not BRIDGE._running:
            response_body = json.dumps({"status": "error", "message": "Bridge not running"})
            status = 400
        else:
            query = parse_qs(parsed_url.query)
            force = str(query.get("force", ["false"])[0]).lower() in {"1", "true", "yes", "on"}
            source = query.get("source", [""])[0] or query.get("src", [""])[0]
            mime_type = query.get("mime", ["image/jpeg"])[0]
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower().strip()] = value.strip()
            content_length = int(headers.get("content-length", "0") or "0")
            if content_length <= 0:
                response_body = json.dumps({"status": "error", "message": "Missing frame body"})
                status = 400
            elif content_length > VIDEO_MAX_BYTES:
                response_body = json.dumps({"status": "error", "message": "Frame too large", "max_bytes": VIDEO_MAX_BYTES})
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            else:
                body = await reader.readexactly(content_length)
                if "content-type" in headers:
                    mime_type = headers["content-type"].split(";", 1)[0].strip().lower()
                result = BRIDGE._gemini.feed_video_frame(body, mime_type, force=force, source=source)
                response_body = json.dumps({"status": "ok" if result.get("accepted") else "dropped", **result})
    elif route == "/notes":
        if not BRIDGE or not BRIDGE._running:
            response_body = json.dumps({"status": "error", "message": "Bridge not running"})
            status = 400
        else:
            query = parse_qs(parsed_url.query)
            limit = max(1, min(int(query.get("limit", ["50"])[0] or "50"), 500))
            notes_file = Path(BRIDGE._gemini.metrics.get("notes_file") or "")
            events: List[Dict[str, Any]] = []
            if notes_file.exists():
                lines = notes_file.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
                for line in lines:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            transcript: List[Dict[str, str]] = []
            for event in events:
                direction = str(event.get("direction") or "")
                text = str(event.get("text") or "").strip()
                if not direction or not text:
                    continue
                if transcript and transcript[-1]["direction"] == direction:
                    sep = "" if text in {".", ",", "?", "!", ":", ";"} else " "
                    transcript[-1]["text"] = (transcript[-1]["text"] + sep + text).strip()
                    transcript[-1]["ts"] = str(event.get("ts") or transcript[-1]["ts"])
                else:
                    transcript.append({
                        "ts": str(event.get("ts") or ""),
                        "direction": direction,
                        "text": text,
                    })
            response_body = json.dumps({
                "status": "ok",
                "notes_file": str(notes_file),
                "events": events,
                "transcript": transcript,
            })
    elif route == "/notify":
        # Proactive notification breakout (criterion #6). Accepts JSON body:
        #   {mode, text, title, source, channel_id, user_id, event_class, sub_event, fields}
        # mode ∈ {auto, voice, dm, channel, webhook, all}
        try:
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower().strip()] = value.strip()
            content_length = int(headers.get("content-length", "0") or "0")
            raw_body = b""
            if content_length > 0:
                raw_body = await reader.readexactly(min(content_length, 64 * 1024))
            try:
                payload = json.loads(raw_body.decode("utf-8", errors="replace") or "{}")
                if not isinstance(payload, dict):
                    payload = {"text": str(payload)}
            except json.JSONDecodeError:
                # Allow GET-style params as a fallback (curl-friendly)
                from urllib.parse import parse_qs as _pqs
                qs = _pqs(parsed_url.query)
                payload = {
                    "mode": (qs.get("mode", ["auto"])[0] or "auto"),
                    "text": (qs.get("text", [""])[0] or ""),
                    "title": qs.get("title", [None])[0],
                    "source": (qs.get("source", ["agent"])[0] or "agent"),
                    "channel_id": qs.get("channel_id", [None])[0],
                    "user_id": qs.get("user_id", [None])[0],
                    "event_class": (qs.get("event_class", ["agent.notify"])[0] or "agent.notify"),
                    "sub_event": (qs.get("sub_event", ["agent_notification"])[0] or "agent_notification"),
                }
            from notification import deliver as _notify_deliver
            result = _notify_deliver(
                text=payload.get("text", ""),
                mode=payload.get("mode", "auto"),
                bridge=BRIDGE,
                adapter=getattr(BRIDGE, "_adapter", None) if BRIDGE else None,
                user_id=payload.get("user_id") or (BRIDGE._target_user_id if BRIDGE else None),
                channel_id=payload.get("channel_id"),
                event_class=payload.get("event_class", "agent.notify"),
                sub_event=payload.get("sub_event", "agent_notification"),
                title=payload.get("title"),
                source=payload.get("source", "agent"),
            )
            response_body = json.dumps(result)
            status = 200 if result.get("status") in ("ok", "partial", "no_subscribers", "scheduled") else 400
        except Exception as exc:
            logger.exception("/notify handler crashed")
            response_body = json.dumps({"status": "error", "message": f"{type(exc).__name__}: {exc}"})
            status = 500
    else:
        response_body = json.dumps({"status": "error", "message": "Not found"})
        status = 404
    _HTTP_REASON = {
        200: "OK",
        400: "BAD REQUEST",
        401: "UNAUTHORIZED",
        404: "NOT FOUND",
        413: "PAYLOAD TOO LARGE",
        500: "INTERNAL SERVER ERROR",
    }
    reason = _HTTP_REASON.get(status, "ERROR")
    response = _format_response(status, response_body, reason=reason)
    # _format_response already returns bytes (it ends with .encode()),
    # so write directly. The previous code called .encode() on the bytes
    # result, raising AttributeError on every successful response and
    # silently dropping the body — which is why /health and /notes came
    # back empty.
    writer.write(response)
    await writer.drain()
    writer.close()


def _format_response(status: int, body: str, reason: Optional[str] = None) -> bytes:
    """Patch 10: central HTTP response builder so the reason phrase is
    guaranteed to match the status (the previous code emitted 200 for
    status=413 because the reason map defined 'PAYLOAD TOO LARGE' but
    the success path rendered the body without re-checking the status).
    """
    if reason is None:
        reason = {
            200: "OK",
            400: "BAD REQUEST",
            401: "UNAUTHORIZED",
            404: "NOT FOUND",
            413: "PAYLOAD TOO LARGE",
            500: "INTERNAL SERVER ERROR",
        }.get(status, "ERROR")
    body_bytes = body.encode("utf-8")
    headers = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii")
    return headers + body_bytes


def _hmac_compare(a: bytes, b: bytes) -> bool:
    """Patch 9: constant-time string comparison so the auth check doesn't
    leak the secret length / prefix via response timing. Falls back to a
    naive compare if hmac.compare_digest is unavailable (it always is on
    CPython 3.3+ but the fallback is cheap insurance)."""
    try:
        import hmac
        return hmac.compare_digest(a, b)
    except Exception:
        return a == b


async def run_sidecar(vc, adapter, ready_future: Optional[asyncio.Future] = None, user_profile: Optional[Any] = None,
                     target_user_id: Optional[str] = None):
    global BRIDGE
    BRIDGE = VoiceLiveBridge(vc, adapter, user_profile=user_profile, target_user_id=target_user_id)
    server = None
    try:
        server = await asyncio.start_server(handle_http_request, "127.0.0.1", HTTP_PORT)
        logger.info("Control API listening on 127.0.0.1:%d", HTTP_PORT)
        ok = await BRIDGE.start()
        if not ok:
            logger.error("Bridge failed to start")
            if ready_future and not ready_future.done():
                ready_future.set_result({"ok": False, "message": "Bridge failed to start"})
            return
        if ready_future and not ready_future.done():
            ready_future.set_result({"ok": True, "health": BRIDGE.health(), "vc": BRIDGE._vc})
        # Webhook: bridge started
        try:
            from webhook_dispatcher import emit_bridge_status
            vc_guild = getattr(getattr(BRIDGE._vc, "guild", None), "id", "?") if BRIDGE._vc else "?"
            vc_chan = getattr(getattr(BRIDGE._vc, "channel", None), "name", "?") if BRIDGE._vc else "?"
            emit_bridge_status("bridge_started", f"Guild: {vc_guild} | Channel: {vc_chan}")
        except Exception:
            pass
        # Start the email-reminder poller (criterion #19)
        try:
            _start_email_reminder_loop(BRIDGE._gemini)
        except Exception as exc:
            logger.debug("email reminder loop start failed: %s", exc)

        # Start the notification scheduler (criterion #6 — deferred notifications)
        try:
            from notification import start_scheduler
            start_scheduler()
        except Exception as exc:
            logger.debug("notification scheduler start failed: %s", exc)

        # Start the email-brief scheduler (criterion #7 — proactive inbox digest)
        try:
            from email_brief import start_brief_scheduler
            start_brief_scheduler(
                get_bridge_fn=lambda: BRIDGE,
                interval=float(os.getenv(
                    "DISCORD_VOICE_LIVE_EMAIL_BRIEF_INTERVAL_SECONDS", "1800"
                )),
            )
        except Exception as exc:
            logger.debug("email brief scheduler start failed: %s", exc)

        # Watch for stop() to close server so run_sidecar task completes
        async def _shutdown_watcher():
            while BRIDGE and BRIDGE._running:
                await asyncio.sleep(1.0)
            logger.info("VoiceLive: shutting down control server")
            if server:
                server.close()
        shutdown_task = asyncio.create_task(_shutdown_watcher())

        async with server:
            await server.serve_forever()
        # server stopped — either by watcher or cancel
        shutdown_task.cancel()
    except asyncio.CancelledError:
        if ready_future and not ready_future.done():
            ready_future.cancel()
    except Exception as exc:
        if ready_future and not ready_future.done():
            ready_future.set_result({"ok": False, "message": str(exc)})
        raise
    finally:
        if server:
            server.close()
            await server.wait_closed()
        if BRIDGE:
            await BRIDGE.stop()


__all__ = ['HTTP_PORT', 'BRIDGE', 'handle_http_request', '_format_response', '_hmac_compare', 'run_sidecar']
__all__ = [n for n in __all__ if n in globals()]
