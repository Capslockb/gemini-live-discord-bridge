"""Loopback-only Android transport for the existing Gemini Live bridge.

This module does not implement an assistant. It adapts authenticated WebSocket
frames to ``GeminiLiveBridge`` and projects the bridge's neutral events/audio
back to the SORA Mobile Gateway. Provider credentials are accepted only in the
initial internal session frame, retained in memory for that session, and never
logged.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from bridge_core import GeminiLiveBridge

logger = logging.getLogger("sora-mobile-realtime")
_CONTEXT_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_AUDIO_FRAME = 64 * 1024
_MAX_VIDEO_FRAME = 512 * 1024
_MAX_TEXT_FRAME = 16 * 1024


class MobileUserProfile:
    """Minimal per-session policy consumed by ``GeminiLiveBridge``."""

    def __init__(self, allowed_tools: set[str], peer_name: str = "user") -> None:
        self._allowed_tools = frozenset(allowed_tools)
        self.honcho_peer_name = peer_name
        self.system_prompt_overrides = ""
        self.onboarding_completed = True
        self.discord_id = None

    def is_tool_allowed(self, name: str) -> bool:
        return name in self._allowed_tools

    def needs_onboarding(self) -> bool:
        return False


class OutboundMux:
    """Bounded single-writer queue for JSON events and PCM output."""

    def __init__(self, max_items: int = 256) -> None:
        self._queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue(maxsize=max_items)

    def put_nowait(self, kind: str, payload: Any) -> None:
        try:
            self._queue.put_nowait((kind, payload))
            return
        except asyncio.QueueFull:
            pass
        try:
            self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            self._queue.put_nowait((kind, payload))
        except asyncio.QueueFull:
            pass

    async def get(self) -> tuple[str, Any] | None:
        return await self._queue.get()

    def clear_audio(self) -> None:
        retained: list[tuple[str, Any] | None] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None or item[0] != "audio":
                retained.append(item)
        for item in retained:
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                break

    def close(self) -> None:
        self.put_nowait("control", None)


class MobileAudioOutput:
    """Output sink compatible with the existing bridge core."""

    def __init__(self, outbound: OutboundMux) -> None:
        self._outbound = outbound

    def feed(self, pcm: bytes) -> None:
        if pcm:
            self._outbound.put_nowait("audio", bytes(pcm))

    def wake(self) -> bool:
        return False

    def clear(self) -> None:
        self._outbound.clear_audio()


class SessionProtocolError(ValueError):
    pass


def _authorization_token(websocket: WebSocket) -> str | None:
    value = websocket.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _configured_tool_allowlist() -> set[str]:
    raw = os.getenv(
        "SORA_REALTIME_ALLOWED_TOOLS",
        "web_search,web_extract,local_delegate_suggest,local_delegate_eta,local_delegate_health",
    )
    return {value.strip() for value in raw.split(",") if value.strip()}


def _validate_start(payload: Any) -> tuple[str, str, set[str], str]:
    if not isinstance(payload, dict) or payload.get("type") != "session.start":
        raise SessionProtocolError("The first frame must start a session")
    context_id = payload.get("contextId")
    provider_key = payload.get("providerKey")
    if not isinstance(context_id, str) or not _CONTEXT_RE.fullmatch(context_id):
        raise SessionProtocolError("Invalid contextId")
    if not isinstance(provider_key, str) or not 16 <= len(provider_key) <= 256:
        raise SessionProtocolError("A provisioned provider credential is required")
    requested = payload.get("allowedTools") or []
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        raise SessionProtocolError("Invalid allowedTools")
    allowed = set(requested) & _configured_tool_allowlist()
    peer_name = payload.get("peerName")
    if not isinstance(peer_name, str) or not peer_name.strip():
        peer_name = "user"
    return context_id, provider_key, allowed, peer_name[:128]


async def _send_loop(websocket: WebSocket, outbound: OutboundMux) -> None:
    while True:
        item = await outbound.get()
        if item is None or item == ("control", None):
            return
        kind, payload = item
        if kind == "audio":
            await websocket.send_bytes(payload)
        else:
            await websocket.send_json(payload)


async def _receive_loop(websocket: WebSocket, bridge: Any, output: MobileAudioOutput) -> None:
    muted = False
    while True:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            return
        binary = message.get("bytes")
        if binary is not None:
            if muted:
                continue
            if not binary or len(binary) > _MAX_AUDIO_FRAME or len(binary) % 2:
                raise SessionProtocolError("Invalid PCM frame")
            bridge.feed_audio(binary)
            continue
        text = message.get("text")
        if text is None:
            continue
        if len(text) > _MAX_TEXT_FRAME:
            raise SessionProtocolError("Control frame is too large")
        try:
            control = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SessionProtocolError("Malformed control frame") from exc
        if not isinstance(control, dict):
            raise SessionProtocolError("Malformed control frame")
        kind = control.get("type")
        if kind == "mic.mute":
            muted = True
        elif kind == "mic.unmute":
            muted = False
        elif kind == "playback.interrupt":
            output.clear()
        elif kind == "text.send":
            value = control.get("text")
            if isinstance(value, str) and value.strip():
                await bridge.send_text(value[:4000])
        elif kind == "camera.frame":
            value = control.get("data")
            if not isinstance(value, str):
                raise SessionProtocolError("Camera frame data is required")
            try:
                frame = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise SessionProtocolError("Malformed camera frame") from exc
            if not frame or len(frame) > _MAX_VIDEO_FRAME:
                raise SessionProtocolError("Camera frame is outside limits")
            bridge.feed_video_frame(frame, "image/jpeg")
        elif kind == "session.end":
            return
        else:
            raise SessionProtocolError("Unsupported control frame")


def create_mobile_realtime_app(
    *,
    internal_token: str | None = None,
    bridge_factory: Callable[..., Any] = GeminiLiveBridge,
) -> FastAPI:
    token = internal_token or os.getenv("SORA_REALTIME_INTERNAL_TOKEN", "")
    if not token:
        raise RuntimeError("SORA_REALTIME_INTERNAL_TOKEN is required")

    app = FastAPI(
        title="SORA Mobile Realtime Transport",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "transport": "gemini-live-bridge",
                "inputAudio": {"encoding": "pcm_s16le", "sampleRate": 16000, "channels": 1},
                "outputAudio": {"encoding": "pcm_s16le", "sampleRate": 24000, "channels": 1},
            }
        )

    @app.websocket("/v1/realtime")
    async def realtime(websocket: WebSocket) -> None:
        supplied = _authorization_token(websocket)
        if supplied is None or not hmac.compare_digest(supplied, token):
            await websocket.close(code=4401, reason="Authentication required")
            return
        await websocket.accept()
        outbound = OutboundMux()
        output = MobileAudioOutput(outbound)
        sender = asyncio.create_task(_send_loop(websocket, outbound))
        bridge: Any | None = None
        try:
            first = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            if len(first) > _MAX_TEXT_FRAME:
                raise SessionProtocolError("Session frame is too large")
            try:
                start = json.loads(first)
            except json.JSONDecodeError as exc:
                raise SessionProtocolError("Malformed session frame") from exc
            context_id, provider_key, allowed_tools, peer_name = _validate_start(start)

            def emit_event(event: dict[str, Any]) -> None:
                outbound.put_nowait("event", event)

            bridge = bridge_factory(
                output_source=output,
                on_event=emit_event,
                api_key=provider_key,
                context_id=context_id,
                user_profile=MobileUserProfile(allowed_tools, peer_name),
            )
            if bridge is None:
                raise RuntimeError("Realtime bridge factory returned no session")
            # Drop the only sidecar-held reference as soon as the bridge owns it.
            provider_key = ""
            await bridge.connect()
            await _receive_loop(websocket, bridge, output)
        except (SessionProtocolError, asyncio.TimeoutError) as exc:
            outbound.put_nowait(
                "event",
                {
                    "kind": "session.error",
                    "code": "invalid_session",
                    "summary": str(exc),
                    "retryable": False,
                },
            )
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning("Mobile realtime session failed (%s)", type(exc).__name__)
            outbound.put_nowait(
                "event",
                {
                    "kind": "session.error",
                    "code": "backend_unavailable",
                    "summary": "SORA Live is unavailable",
                    "retryable": True,
                },
            )
        finally:
            if bridge is not None:
                bridge._user_disconnect = True
                try:
                    await bridge.disconnect()
                except Exception:
                    logger.debug("Realtime bridge shutdown failed", exc_info=True)
            outbound.close()
            try:
                await asyncio.wait_for(sender, timeout=1.0)
            except (asyncio.TimeoutError, WebSocketDisconnect):
                sender.cancel()
            try:
                await websocket.close(code=1000)
            except Exception:
                pass

    return app


app = create_mobile_realtime_app() if os.getenv("SORA_REALTIME_INTERNAL_TOKEN") else None
