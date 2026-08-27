"""Extracted from bridge.py — part of the gemini-live-discord-bridge split. Do not edit in isolation; see bridge.py facade."""
import ast
import asyncio
import base64
import html
import inspect
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
from typing import Any, Optional, Dict, List, Callable, Tuple, Protocol

import numpy as np
logger = logging.getLogger("voice-live")
from bridge_audio import LiveAudioSource, _fade_in_pcm_24k_mono, _has_barge_in_energy_16k, _has_speech_energy, _has_speech_energy_16k, _put_drop_oldest, _silence_pcm, downsample_for_gemini, generate_typing_pcm
from bridge_config import ALLOWED_SPEAKER_IDS, AUTO_LEAVE_MIN_UPTIME_SECONDS, AUTO_LEAVE_QUIET_SECONDS, BASE_SYSTEM_PROMPT, GEMINI_API_KEY, GEMINI_MODEL, GEMINI_MODEL_FALLBACKS, GEMINI_OUT_CH, GEMINI_OUT_SR, GEMINI_VOICE_NAME, GEMINI_WS_URL, GITHUB_VOICE_TOOLS_ENABLED, IDLE_PROMPT_GRACE_SECONDS, IDLE_PROMPT_SECONDS, IDLE_PROMPT_TEXT, INITIAL_GREETING, NOTES_DIR, OUTPUT_CLEAR_ON_INTERRUPT, OUTPUT_FADE_IN_MS, OUTPUT_PREROLL_MS, OUTPUT_TAIL_PAD_MS, SPOTIFY_VOICE_TOOLS_ENABLED, TYPING_SOUND_ENABLED, VIDEO_ENABLED, VIDEO_INITIALIZED_QUIET_THRESHOLD_S, VIDEO_MAX_BYTES, VIDEO_MAX_FPS, VIDEO_WHEN_RECENT_AUDIO_SECONDS, VOICE_LEAVE_PHRASES, WEB_VOICE_TOOLS_ENABLED
from bridge_context import _build_honcho_context
from bridge_opencode import OPENCODE_VOICE_TOOLS_ENABLED, _OPENCODE_FUNCTION_DECLARATIONS, _run_opencode_tool_with_bridge
from bridge_tools import HA_VOICE_TOOLS_ENABLED, LOCAL_VOICE_TOOLS_ENABLED, SYSINSPECT_VOICE_TOOLS_ENABLED, _GITHUB_FUNCTION_DECLARATIONS, _HOMEASSISTANT_FUNCTION_DECLARATIONS, _LOCAL_FUNCTION_DECLARATIONS, _SPOTIFY_FUNCTION_DECLARATIONS, _SYSINSPECT_FUNCTION_DECLARATIONS, _WEB_FUNCTION_DECLARATIONS, _normalize_voice_web_args, _run_github_tool, _run_local_tool, _run_spotify_tool, _run_sysinspect_tool, _run_web_tool

try:
    from discord.ext import voice_recv
except Exception:
    voice_recv = None


if voice_recv is not None:
    class GeminiPCMSink(voice_recv.AudioSink):
        """Receive decoded Discord PCM and forward 16 kHz mono chunks to Gemini."""

        def __init__(self, on_pcm_callback: Callable[[bytes], None]):
            super().__init__()
            self._on_pcm = on_pcm_callback
            self._frames = 0
            self._decoded_frames = 0
            self._skipped_unknown = 0
            self._skipped_bot = 0
            self._decode_errors = 0
            self._last_decode_error_log = 0.0

        def wants_opus(self) -> bool:
            """Return False so voice_recv delivers decoded PCM instead of opus frames."""
            return False

        def write(self, user, data) -> None:
            if user is None:
                self._skipped_unknown += 1
                return
            if getattr(user, "bot", False):
                self._skipped_bot += 1
                return
            if ALLOWED_SPEAKER_IDS is not None and getattr(user, "id", None) not in ALLOWED_SPEAKER_IDS:
                self._skipped_unknown += 1
                return
            # voice_recv gives us pre-decoded PCM (48k stereo, 20ms chunks)
            # because wants_opus() is False. Sometimes voice_recv passes raw
            # `bytes` instead of a VoiceData object — branch on type or
            # getattr returns b"" because bytes has no .pcm attribute, and
            # 100% of inbound audio is silently dropped.
            if isinstance(data, bytes):
                pcm = data
            else:
                pcm = getattr(data, "pcm", b"") or b""
            if not pcm:
                return
            self._frames += 1
            if not _has_speech_energy(pcm):
                return
            self._decoded_frames += 1
            self._on_pcm(downsample_for_gemini(bytes(pcm)))

        def cleanup(self) -> None:
            pass

        def stats(self) -> Dict[str, int]:
            return {
                "voice_sink_frames": self._frames,
                "voice_sink_decoded_frames": self._decoded_frames,
                "voice_sink_decode_errors": self._decode_errors,
                "voice_sink_skipped_unknown": self._skipped_unknown,
                "voice_sink_skipped_bot": self._skipped_bot,
            }
else:
    GeminiPCMSink = None


class AudioOutput(Protocol):
    """Transport-neutral output contract used by Discord and mobile."""

    def feed(self, pcm: bytes) -> None: ...

    def wake(self) -> bool: ...

    def clear(self) -> None: ...


class AudioFramePacer:
    """Schedule PCM chunks at their real-time duration without burst catch-up."""

    def __init__(self, sample_rate: int, sample_width: int, channels: int) -> None:
        self._bytes_per_second = sample_rate * sample_width * channels
        self._next_send_at: Optional[float] = None

    def delay_for(self, chunk: bytes, *, now: Optional[float] = None) -> float:
        current = time.monotonic() if now is None else now
        send_at = current if self._next_send_at is None else max(current, self._next_send_at)
        duration = len(chunk) / self._bytes_per_second
        self._next_send_at = send_at + duration
        return send_at - current


def drop_audio_backlog(audio_queue: "queue.Queue[Optional[bytes]]", max_frames: int) -> int:
    """Drop stale queued PCM so the live stream cannot accumulate latency."""

    dropped = 0
    while audio_queue.qsize() > max_frames:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            break
        dropped += 1
    return dropped


class GeminiLiveBridge:
    AUDIO_STREAM_IDLE_END_SECONDS = float(os.getenv("GEMINI_AUDIO_STREAM_IDLE_END_SECONDS", "0.25"))

    def __init__(
        self,
        output_source: AudioOutput,
        on_wake: Callable[[], None] = None,
        on_leave_request: Callable[[str], None] = None,
        on_reconnect: Callable[[], None] = None,
        user_profile: Optional[Any] = None,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
        api_key: Optional[str] = None,
        context_id: Optional[str] = None,
        output_echo_guard: bool = False,
    ):
        self._ws = None
        self._output_source = output_source
        # Register the output source in the sfx module so cross-bridge
        # sfx triggers (notification, error, tool_init) can find it
        # (criterion #8 — multi-slot UI sfx library).
        try:
            from sfx import register_active_source
            sid = (
                getattr(user_profile, "discord_id", None)
                or os.getenv("DISCORD_VOICE_LIVE_USER_ID", "default")
                or "default"
            )
            register_active_source(str(sid), output_source)
        except Exception:
            pass
        self._on_wake = on_wake
        self._on_leave_request = on_leave_request
        self._on_reconnect = on_reconnect
        self._on_event = on_event
        self._api_key = api_key or GEMINI_API_KEY
        self._context_id = context_id
        self._output_echo_guard = output_echo_guard
        self._output_echo_guard_confirm_frames = 4
        self._output_echo_guard_pending: List[bytes] = []
        self._running = False
        self._session_handle: Optional[str] = None
        self._reconnecting = False
        self._user_disconnect = False
        self._reconnect_count = 0
        # Per-user profile (Honcho peer, tool allowlist, prompt overrides).
        # When None, fall back to module-level defaults (legacy single-user mode).
        self._user_profile = user_profile
        self._voice_name = (
            getattr(user_profile, "voice_name", None)
            or os.getenv("DISCORD_VOICE_LIVE_VOICE", GEMINI_VOICE_NAME)
            or GEMINI_VOICE_NAME
        )
        self._send_q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=256)
        self._audio_pacer = AudioFramePacer(sample_rate=16_000, sample_width=2, channels=1)
        self._audio_max_backlog_frames = max(
            1, int(os.getenv("SORA_AUDIO_MAX_BACKLOG_FRAMES", "10"))
        )
        self._video_q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=2)
        self._tasks: List[asyncio.Task] = []
        self._audio_stream_open = False
        self._last_audio_sent_at: Optional[float] = None
        self._last_video_sent_at: Optional[float] = None
        self._output_turn_open = False
        self._seen_server_content_shapes: set = set()
        self._notes_file = self._create_notes_file()
        self.metrics: Dict[str, Any] = {
            "audio_in_chunks": 0,
            "audio_in_dropped_chunks": 0,
            "audio_echo_guard_dropped_chunks": 0,
            "audio_echo_guard_held_chunks": 0,
            "audio_echo_guard_confirmed_events": 0,
            "audio_out_chunks": 0,
            "audio_out_bytes": 0,
            "audio_stream_end_events": 0,
            "audio_preroll_events": 0,
            "input_transcript_events": 0,
            "output_transcript_events": 0,
            "video_in_frames": 0,
            "video_sent_frames": 0,
            "video_dropped_frames": 0,
            "video_last_reason": None,
            "notes_file": str(self._notes_file),
            "notes_events": 0,
            "last_input_transcript": None,
            "last_output_transcript": None,
            "last_input_to_output_ms": None,
            "last_input_monotonic": None,
            "last_output_monotonic": None,
            "model": None,
        }

    def _emit_event(self, kind: str, **payload: Any) -> None:
        """Emit a transport-neutral session event without affecting Discord."""
        if self._on_event is None:
            return
        event: Dict[str, Any] = {
            "kind": kind,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "contextId": self._context_id,
            **payload,
        }
        try:
            result = self._on_event(event)
            if inspect.isawaitable(result):
                asyncio.get_running_loop().create_task(result)
        except Exception:
            logger.debug("VoiceLive transport event callback failed", exc_info=True)

    def _create_notes_file(self) -> Path:
        try:
            NOTES_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.warning("VoiceLive: could not create notes dir %s", NOTES_DIR, exc_info=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return NOTES_DIR / f"voice-live-{stamp}.jsonl"

    def feed_audio(self, pcm_16k_mono: bytes) -> None:
        self.metrics["audio_in_chunks"] += 1
        self.metrics["last_input_monotonic"] = time.monotonic()
        confirmed_frames: List[bytes] = []
        if self._output_echo_guard:
            if self._output_turn_open:
                if not _has_barge_in_energy_16k(pcm_16k_mono):
                    self.metrics["audio_echo_guard_dropped_chunks"] += (
                        len(self._output_echo_guard_pending) + 1
                    )
                    self._output_echo_guard_pending.clear()
                    return
                self._output_echo_guard_pending.append(bytes(pcm_16k_mono))
                self.metrics["audio_echo_guard_held_chunks"] += 1
                if (
                    len(self._output_echo_guard_pending)
                    < self._output_echo_guard_confirm_frames
                ):
                    return
                confirmed_frames = self._output_echo_guard_pending[:]
                self._output_echo_guard_pending.clear()
                self.metrics["audio_echo_guard_confirmed_events"] += 1
            else:
                self._output_echo_guard_pending.clear()
        # Local hard-clear: if user audio has speech energy AND the model is
        # currently producing output, force-clear the output buffer locally
        # instead of waiting for Gemini's WSS round-trip of the
        # `interrupted=true` event. The theoretical minimum latency is one
        # PCM frame (~20ms) — empirically ~30-50ms because _has_speech_energy
        # adds a peak-amplitude scan over the downsampled frame. This is the
        # load-bearing fix for "interrupts are working but not snappy" — the
        # Gemini VAD is server-side and adds 4-9s of WSS round-trip in
        # practice, this bypasses it on the stop-audio side.
        try:
            if (
                self._output_turn_open
                and _has_speech_energy_16k(pcm_16k_mono)
                and self._output_source is not None
            ):
                if OUTPUT_CLEAR_ON_INTERRUPT:
                    self._output_source.clear()
                self._output_turn_open = False
                self.metrics["local_interrupt_events"] = (
                    self.metrics.get("local_interrupt_events", 0) + 1
                )
                self._emit_event("audio.interrupted", source="local_vad")
        except Exception:
            logger.debug("local VAD clear failed in feed_audio", exc_info=True)
        if confirmed_frames:
            for frame in confirmed_frames:
                _put_drop_oldest(self._send_q, frame)
        else:
            _put_drop_oldest(self._send_q, pcm_16k_mono)

    def feed_video_frame(self, data: bytes, mime_type: str, force: bool = False,
                         source: str = "") -> Dict[str, Any]:
        self.metrics["video_in_frames"] += 1
        if not VIDEO_ENABLED:
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "disabled"
            return {"accepted": False, "reason": "disabled"}
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "unsupported_mime"
            return {"accepted": False, "reason": "unsupported_mime"}
        if not data or len(data) > VIDEO_MAX_BYTES:
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "size_limit"
            return {"accepted": False, "reason": "size_limit", "max_bytes": VIDEO_MAX_BYTES}

        now = time.monotonic()
        min_interval = 1.0 / max(VIDEO_MAX_FPS, 0.1)
        if self._last_video_sent_at is not None and now - self._last_video_sent_at < min_interval:
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "fps_limit"
            return {"accepted": False, "reason": "fps_limit", "max_fps": VIDEO_MAX_FPS}

        last_audio = self.metrics.get("last_input_monotonic")
        if not force and (last_audio is None or now - float(last_audio) > VIDEO_WHEN_RECENT_AUDIO_SECONDS):
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "no_recent_voice"
            return {"accepted": False, "reason": "no_recent_voice"}

        # Track how long the bridge has been quiet before this first frame.
        # If the feeder kicks in cold (or comes back after a long pause), we
        # want to know — that's when the "white page" loop is most likely to
        # start and we want to announce to the user that video is actually
        # flowing now.
        last_accept = self.metrics.get("video_last_accept_monotonic")
        quiet_s = (now - float(last_accept)) if last_accept is not None else 0.0
        self.metrics["video_last_accept_monotonic"] = now
        self.metrics["video_last_quiet_s"] = quiet_s
        self.metrics["video_last_source"] = source or ""

        frame = {
            "data": base64.b64encode(data).decode(),
            "mimeType": mime_type,
        }
        _put_drop_oldest(self._video_q, frame)
        self._last_video_sent_at = now
        self.metrics["video_sent_frames"] += 1
        self.metrics["video_last_reason"] = "accepted"
        result = {"accepted": True, "max_fps": VIDEO_MAX_FPS, "bytes": len(data)}

        # Webhook: announce the first real video frame after a long quiet
        # period. The 30s threshold avoids spam during a normal 1fps feeder
        # loop while still catching cold-start and post-pause reinit.
        if quiet_s >= VIDEO_INITIALIZED_QUIET_THRESHOLD_S:
            try:
                from webhook_dispatcher import emit_video_initialized
                emit_video_initialized(source=source, frame_bytes=len(data), accepted_after_silence_s=quiet_s)
            except Exception as _exc:
                logger.debug("emit_video_initialized failed: %s", _exc)

        return result

    async def connect(self):
        import websockets
        if not self._api_key:
            raise RuntimeError("Gemini Live key is not configured")
        ws_url = GEMINI_WS_URL
        candidates = [GEMINI_MODEL]
        for model in GEMINI_MODEL_FALLBACKS:
            if model not in candidates:
                candidates.append(model)
        last_error: Optional[BaseException] = None
        for model in candidates:
            try:
                await self._connect_model(websockets, ws_url, model, handle=self._session_handle)
                self.metrics["model"] = model
                break
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini Live model %s failed: %s", model, exc)
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
        else:
            raise RuntimeError(f"No Gemini Live model could start: {last_error}") from last_error
        self._running = True
        self._tasks = [
            asyncio.create_task(self._send_loop()),
            asyncio.create_task(self._receive_loop()),
        ]
        self._emit_event(
            "session.ready",
            model=self.metrics.get("model"),
            inputAudio={"encoding": "pcm_s16le", "sampleRate": 16000, "channels": 1},
            outputAudio={"encoding": "pcm_s16le", "sampleRate": GEMINI_OUT_SR, "channels": GEMINI_OUT_CH},
        )
        if INITIAL_GREETING and not self._reconnecting:
            await self.send_text(INITIAL_GREETING)

    async def _connect_model(self, websockets, ws_url: str, model: str, handle=None):
        self._ws = await self._open_websocket(websockets, ws_url)

        # Per-user Honcho peer: when this bridge was created with a profile, use
        # that profile's honcho_peer_name so memory is fully isolated per user.
        # Falls back to module-level HONCHO_PEER_NAME if no profile was provided.
        peer_override: Optional[str] = None
        base_prompt = BASE_SYSTEM_PROMPT
        if self._user_profile is not None:
            try:
                peer_override = getattr(self._user_profile, "honcho_peer_name", None)
                overrides = getattr(self._user_profile, "system_prompt_overrides", "") or ""
                if overrides.strip():
                    base_prompt = base_prompt + "\n\n--- PER-USER OVERRIDES ---\n" + overrides.strip() + "\n--- END PER-USER OVERRIDES ---"
            except Exception:
                peer_override = None
        honcho_ctx = await _build_honcho_context(peer_name_override=peer_override)
        system_text = base_prompt + honcho_ctx
        # #32: If this is a new user who hasn't been onboarded, append
        # a one-time system reminder to start the Q&A flow. The agent
        # sees this on the very first turn, calls
        # local_user_onboarding_get_questions, then walks the user
        # through the 6 questions via voice.
        try:
            if self._user_profile is not None and self._user_profile.needs_onboarding():
                from user_profiles import ONBOARDING_QUESTIONS
                q_list = ", ".join(q["id"] for q in ONBOARDING_QUESTIONS)
                system_text = system_text + (
                    "\n\n--- ONBOARDING REQUIRED (criterion #32) ---\n"
                    "This user has never been onboarded. On your first turn, call\n"
                    "local_user_onboarding_get_questions to retrieve the list, then\n"
                    "walk them through the questions in order (one at a time, in voice).\n"
                    f"After each answer, call local_user_onboarding_answer with the\n"
                    f"question_id and the user's spoken answer. Questions: {q_list}.\n"
                    "Do NOT start any other task until onboarding is complete.\n"
                    "--- END ONBOARDING REQUIRED ---"
                )
        except Exception:
            pass

        # #28: Mirror user's speech/communication preferences. Inject the
        # user's declared communication_style and pet_peeves (captured
        # during #32 onboarding) into the system prompt so the agent
        # adapts its tone to the user's natural speech patterns.
        try:
            if self._user_profile is not None and self._user_profile.onboarding_completed:
                style = (getattr(self._user_profile, 'communication_style', '') or '').strip()
                peeves = (getattr(self._user_profile, 'pet_peeves', '') or '').strip()
                parts = []
                if style:
                    parts.append(
                        "--- COMMUNICATION PREFERENCE ---\n"
                        f"The user has said they prefer: {style}\n"
                        "Adapt your tone, sentence length, and level of formality "
                        "to match this. If they're short and direct, be short and direct. "
                        "If they're conversational, respond conversationally. "
                        "Mirror their vocabulary and rhythm — if they use technical jargon, "
                        "use technical jargon. If they use casual language, keep it casual."
                    )
                if peeves:
                    parts.append(
                        "--- PET PEEVES ---\n"
                        "The user has explicitly asked me to NEVER do these:\n"
                        f"{peeves}\n"
                        "Take these as hard constraints."
                    )
                if parts:
                    system_text = system_text + "\n\n" + "\n\n---\n\n".join(parts)
        except Exception:
            pass
        setup_payload: Dict[str, Any] = {
            "model": f"models/{model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self._voice_name}
                    }
                },
                # NOTE: mediaResolution is intentionally OMITTED from the setup
                # payload. The Gemini Live API rejects it with
                # "Unknown name 'mediaResolution' at 'setup': Cannot find field."
                # for the current model lineup (3.1-flash-live-preview and
                # 2.5-flash-native-audio-preview-*). The field exists in the docs
                # for "native audio" models but is NOT accepted on these
                # specific model names. The Live API works fine without it —
                # omitting it avoids the 1007 setup error. Frame-size cost is
                # already controlled at the bridge level (1 fps cap + 512 KB
                # max + audio-gating).
            },
            "realtimeInputConfig": {
                "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
                "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",
                "automaticActivityDetection": {
                    "disabled": False,
                    "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
                    "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
                    "prefixPaddingMs": 0,
                    "silenceDurationMs": 40,
                }
            },
            # NOTE: top-level `voice_activity_detection` is intentionally
            # OMITTED. The current Gemini Live API schema
            # (https://ai.google.dev/api/live, v1beta) only exposes
            # `realtimeInputConfig.automaticActivityDetection` for VAD
            # tuning. The top-level `voice_activity_detection` key is not
            # in the schema and was being silently ignored (or, on stricter
            # servers, returning 1007). VAD tuning lives in the inner block
            # above. Same omission-rationale pattern as mediaResolution
            # at line 4322-4334.
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "systemInstruction": {
                "parts": [{
                    "text": system_text
                }]
            },
        }
        # Helper: filter a function-declaration list by per-user allowlist.
        def _filter_for_user(decls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if self._user_profile is None:
                return decls
            try:
                return [d for d in decls if self._user_profile.is_tool_allowed(d.get("name", ""))]
            except Exception:
                return decls

        if SPOTIFY_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _spotify = _filter_for_user(_SPOTIFY_FUNCTION_DECLARATIONS)
            if _spotify:
                setup_payload["tools"].append({"functionDeclarations": _spotify})
                logger.info("Spotify voice tools registered (count=%d)", len(_spotify))
        if WEB_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _web = _filter_for_user(_WEB_FUNCTION_DECLARATIONS)
            if _web:
                setup_payload["tools"].append({"functionDeclarations": _web})
                logger.info("Web voice tools registered (count=%d)", len(_web))
        if LOCAL_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _local = _filter_for_user(_LOCAL_FUNCTION_DECLARATIONS)
            if _local:
                setup_payload["tools"].append({"functionDeclarations": _local})
                logger.info("Local voice tools registered (count=%d)", len(_local))
        if HA_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _ha = _filter_for_user(_HOMEASSISTANT_FUNCTION_DECLARATIONS)
            if _ha:
                setup_payload["tools"].append({"functionDeclarations": _ha})
                logger.info("HA voice tools registered (count=%d)", len(_ha))
        if OPENCODE_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _oc = _filter_for_user(_OPENCODE_FUNCTION_DECLARATIONS)
            if _oc:
                setup_payload["tools"].append({"functionDeclarations": _oc})
                logger.info("OpenCode voice tools registered (count=%d)", len(_oc))
        if SYSINSPECT_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _si = _filter_for_user(_SYSINSPECT_FUNCTION_DECLARATIONS)
            if _si:
                setup_payload["tools"].append({"functionDeclarations": _si})
                logger.info("SysInspect voice tools registered (count=%d)", len(_si))
        if GITHUB_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _gh = _filter_for_user(_GITHUB_FUNCTION_DECLARATIONS)
            if _gh:
                setup_payload["tools"].append({"functionDeclarations": _gh})
                logger.info("GitHub voice tools registered (count=%d)", len(_gh))
        if handle is not None:
            setup_payload["sessionResumption"] = {"handle": handle}
            logger.info("Session resumption: handle=%s", handle)
        setup = {"setup": setup_payload}
        await self._ws.send(json.dumps(setup))
        async for msg in self._ws:
            resp = json.loads(msg)
            if "setupComplete" in resp:
                logger.info("Setup complete for model %s", model)
                return
        raise RuntimeError(f"Gemini setup ended before setupComplete for {model}")

    async def _open_websocket(self, websockets, ws_url: str):
        # Google APIs accept the key in x-goog-api-key. Keeping it out of the
        # URL prevents exception strings and proxy logs from recording it.
        return await websockets.connect(
            ws_url,
            additional_headers=self._provider_headers(),
            ping_interval=20,
            ping_timeout=10,
        )

    def _provider_headers(self) -> Dict[str, str]:
        """Build in-memory provider authentication without URL credentials."""
        return {"x-goog-api-key": self._api_key}

    async def send_text(self, text: str) -> None:
        if not self._ws or not text.strip():
            return
        msg = {"realtimeInput": {"text": text.strip()}}
        await self._ws.send(json.dumps(msg))

    async def disconnect(self):
        self._running = False
        _put_drop_oldest(self._send_q, None)
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        if self._ws:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        if self._user_disconnect:
            self._emit_event("session.ended", reason="client_closed")

    async def _restart(self):
        if self._reconnecting or self._user_disconnect:
            return
        self._reconnecting = True
        self._reconnect_count += 1
        logger.info("Gemini Live: starting reconnect #%d...", self._reconnect_count)
        self._emit_event("session.reconnecting", reconnectCount=self._reconnect_count)
        await self.disconnect()
        try:
            await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        if self._user_disconnect:
            logger.info("Gemini Live: abort reconnect: user disconnected")
            self._reconnecting = False
            return
        # Backoff to avoid hammering the API
        backoff = min(2 ** (self._reconnect_count - 1), 30)
        logger.info("Gemini Live: reconnect backoff %ds", backoff)
        await asyncio.sleep(backoff)
        if self._user_disconnect:
            self._reconnecting = False
            return
        try:
            await self.connect()
            self._send_q = queue.Queue(maxsize=256)
            self._video_q = queue.Queue(maxsize=2)
            self._output_turn_open = False
            self._seen_server_content_shapes.clear()
            if self._on_reconnect:
                try:
                    self._on_reconnect()
                except Exception:
                    pass
            self._emit_event("session.reconnected", reconnectCount=self._reconnect_count)
            logger.info("Gemini Live: reconnected successfully #%d (handle=%s)", self._reconnect_count, self._session_handle)
        except Exception as e:
            logger.error("Gemini Live: reconnect failed #%d: %s", self._reconnect_count, e)
            self._emit_event(
                "session.error",
                code="reconnect_failed",
                summary="SORA Live could not reconnect",
                retryable=False,
            )
            if self._on_leave_request and not self._user_disconnect:
                try:
                    self._on_leave_request("Gemini reconnect failed: %s" % e)
                except Exception:
                    pass
        finally:
            self._reconnecting = False

    async def _send_loop(self):
        while self._running:
            dropped = drop_audio_backlog(self._send_q, self._audio_max_backlog_frames)
            if dropped:
                self.metrics["audio_in_dropped_chunks"] += dropped
            try:
                chunk = self._send_q.get_nowait()
            except queue.Empty:
                # No audio waiting — send a pending video frame and idle-end check
                await self._send_pending_video_frame()
                await self._maybe_end_idle_audio_stream()
                await asyncio.sleep(0.02)
                continue
            if chunk is None:
                break
            delay = self._audio_pacer.delay_for(chunk)
            if delay > 0:
                await asyncio.sleep(delay)
            # Send one video frame between audio chunks so video doesn't starve
            await self._send_pending_video_frame()
            b64_data = base64.b64encode(chunk).decode()
            msg = {"realtimeInput": {"audio": {"data": b64_data, "mimeType": "audio/pcm;rate=16000"}}}
            try:
                await self._ws.send(json.dumps(msg))
                self._audio_stream_open = True
                self._last_audio_sent_at = time.monotonic()
            except Exception as e:
                logger.error("Send error: %s", e)
                break

    async def _send_pending_video_frame(self) -> None:
        if not self._ws:
            return
        try:
            frame = self._video_q.get_nowait()
        except queue.Empty:
            return
        if not frame:
            return
        msg = {"realtimeInput": {"video": frame}}
        try:
            await self._ws.send(json.dumps(msg))
        except Exception as e:
            logger.error("Send video frame error: %s", e)

    async def _maybe_end_idle_audio_stream(self) -> None:
        if not self._audio_stream_open or self._last_audio_sent_at is None or not self._ws:
            return
        if time.monotonic() - self._last_audio_sent_at < self.AUDIO_STREAM_IDLE_END_SECONDS:
            return
        try:
            await self._ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
            self.metrics["audio_stream_end_events"] += 1
            self._audio_stream_open = False
            self._last_audio_sent_at = None
            logger.info("Gemini Live: sent audioStreamEnd after idle input")
        except Exception as e:
            logger.error("Send audioStreamEnd error: %s", e)

    async def _receive_loop(self):
        while self._running:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                close_code = getattr(e, "close_code", None)
                close_reason = getattr(e, "close_reason", None)
                is_1008 = close_code == 1008
                if close_code is not None:
                    logger.warning("Gemini Live: WebSocket closed (code=%s, reason=%s)", close_code, close_reason)
                else:
                    logger.error("Receive error: %s", e)
                if is_1008:
                    # 1008 = session duration exceeded — decoder state may be bad, drop it
                    logger.warning("Gemini Live: detected 1008 GoAway-style close")
                    self._output_turn_open = False
                    self._seen_server_content_shapes.clear()
                if not self._reconnecting:
                    asyncio.get_running_loop().create_task(self._restart())
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Session resumption handle update
            sru = msg.get("sessionResumptionUpdate")
            if sru:
                if sru.get("resumable") and sru.get("newHandle"):
                    self._session_handle = sru["newHandle"]
                self.metrics["gemini_resumption_updates"] = self.metrics.get("gemini_resumption_updates", 0) + 1
            # GoAway detection
            go_away = msg.get("goAway")
            if go_away is not None:
                time_left = go_away.get("timeLeft", "unknown")
                logger.warning("Gemini Live: GoAway received (%s remaining)", time_left)
                if not self._reconnecting:
                    asyncio.get_running_loop().create_task(self._restart())
                break
            sc = msg.get("serverContent", {})
            if sc:
                self._log_server_content_shape(sc)
                self._record_transcript("input", sc.get("inputTranscription", {}))
                self._record_transcript("output", sc.get("outputTranscription", {}))
                mt = sc.get("modelTurn", {})
                parts = mt.get("parts", [])
                for part in parts:
                    idata = part.get("inlineData", {})
                    if idata.get("mimeType", "").startswith("audio/pcm"):
                        pcm_bytes = base64.b64decode(idata["data"])
                        if pcm_bytes:
                            self._record_output_chunk(len(pcm_bytes))
                            if not self._output_turn_open:
                                self._output_source.feed(_silence_pcm(GEMINI_OUT_SR, GEMINI_OUT_CH, OUTPUT_PREROLL_MS))
                                pcm_bytes = _fade_in_pcm_24k_mono(pcm_bytes, OUTPUT_FADE_IN_MS)
                                self._output_turn_open = True
                                self.metrics["audio_preroll_events"] += 1
                                self._emit_event("audio.started")
                            if self._output_source.wake():
                                self._output_source.feed(pcm_bytes)
                                if self._on_wake:
                                    try:
                                        self._on_wake()
                                    except Exception:
                                        pass
                            else:
                                self._output_source.feed(pcm_bytes)
                if sc.get("interrupted"):
                    if OUTPUT_CLEAR_ON_INTERRUPT:
                        self._output_source.clear()
                    self._output_turn_open = False
                    self._emit_event("audio.interrupted", source="server")
                if sc.get("turnComplete") or sc.get("generationComplete"):
                    if self._output_turn_open and OUTPUT_TAIL_PAD_MS > 0:
                        self._output_source.feed(_silence_pcm(GEMINI_OUT_SR, GEMINI_OUT_CH, OUTPUT_TAIL_PAD_MS))
                    self._output_turn_open = False
                    self._emit_event("turn.completed")
            # ── Handle tool calls from Gemini ──────────────────────────────────────
            tool_call = msg.get("toolCall")
            if tool_call:
                try:
                    await self._handle_tool_call(tool_call)
                except Exception as tc_exc:
                    logger.exception("Gemini Live: tool call handler crashed (recv loop continues): %s", tc_exc)
            tool_call_cancel = msg.get("toolCallCancellation")
            if tool_call_cancel:
                logger.info("Gemini toolCallCancellation received")
                self._emit_event("tool.cancelled")

    def _log_server_content_shape(self, server_content: Dict[str, Any]) -> None:
        keys = tuple(sorted(server_content.keys()))
        if keys in self._seen_server_content_shapes:
            return
        self._seen_server_content_shapes.add(keys)
        logger.info("Gemini serverContent keys: %s", ",".join(keys))

    def _record_transcript(self, direction: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        self._emit_event(
            "transcript.user" if direction == "input" else "transcript.sora",
            text=text,
            final=bool(payload.get("finished") or payload.get("final")),
        )
        metric_prefix = f"{direction}_transcript"
        self.metrics[f"{metric_prefix}_events"] += 1
        self.metrics[f"last_{metric_prefix}"] = text[-500:]
        logger.info("Gemini %s transcript: %s", direction, text)
        self._append_note_event(direction, text)
        # Webhook: push transcript line to voice.transcript webhooks
        try:
            from webhook_dispatcher import emit_voice_input, emit_voice_output
            if direction == "output":
                emit_voice_output(text)
            else:
                emit_voice_input(text)
        except Exception:
            pass
        if direction == "input":
            self._maybe_handle_voice_leave_request(text)

    def _append_note_event(self, direction: str, text: str) -> None:
        event = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "direction": direction,
            "text": text,
        }
        try:
            with self._notes_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.metrics["notes_events"] += 1
        except Exception:
            logger.warning("VoiceLive: could not append note event", exc_info=True)

    async def _handle_tool_call(self, tool_call: Any) -> None:
        """Execute Spotify, web, or other local tools requested by Gemini Live.

        Tool runners are synchronous I/O; they run in a thread so the async
        receive loop stays responsive and can't be accidentally DoS'd by one
        slow search.
        """
        function_calls = tool_call.get("functionCalls", []) if isinstance(tool_call, dict) else []
        if not function_calls:
            logger.warning("toolCall message without functionCalls: %s", tool_call)
            return
        # ── Typing feedback: begin audio indicator ──────────────────────────
        typing_active = False
        typing_task: Optional[asyncio.Task] = None
        if TYPING_SOUND_ENABLED and self._output_source is not None and not self._output_turn_open:
            typing_active = True

            async def _typing_loop():
                while typing_active:
                    try:
                        pcm = generate_typing_pcm()
                        self._output_source.feed(pcm)
                    except Exception:
                        break
                    await asyncio.sleep(0.05 + random.random() * 0.15)

            typing_task = asyncio.get_running_loop().create_task(_typing_loop())

        responses: List[Dict[str, Any]] = []
        try:
            loop = asyncio.get_running_loop()
            for fc in function_calls:
                call_id = fc.get("id", "")
                name = fc.get("name", "")
                args = fc.get("args", {})
                self._emit_event("tool.started", callId=call_id, name=name)
                if name.startswith("web_") and isinstance(args, dict):
                    args = _normalize_voice_web_args(name, args)
                logger.info("Gemini tool call: %s id=%s", name, call_id)
                # Webhook: tool.called event (throttled)
                try:
                    from webhook_dispatcher import emit_tool_called
                    args_summary = ", ".join(f"{k}={str(v)[:80]}" for k, v in (args or {}).items())[:6]
                    emit_tool_called(name, args_summary)
                except Exception:
                    pass
                # Defense-in-depth per-user allowlist check. Even if a tool
                # declaration snuck through, refuse to execute it for a user
                # who isn't allowed to invoke it.
                if self._user_profile is not None:
                    try:
                        if not self._user_profile.is_tool_allowed(name):
                            result = {"error": f"Tool '{name}' is not enabled for this user"}
                            responses.append({"id": call_id, "name": name, "response": result})
                            self._emit_event("tool.failed", callId=call_id, name=name)
                            continue
                    except Exception:
                        pass
                try:
                    if name.startswith("spotify_"):
                        result = await loop.run_in_executor(None, _run_spotify_tool, name, args)
                    elif name.startswith("web_"):
                        result = await loop.run_in_executor(None, _run_web_tool, name, args)
                    elif name.startswith("local_"):
                        if name.startswith("local_inspect_"):
                            result = await loop.run_in_executor(None, _run_sysinspect_tool, name, args)
                        elif name.startswith("local_github_"):
                            result = await loop.run_in_executor(None, _run_github_tool, name, args)
                        else:
                            result = await loop.run_in_executor(None, _run_local_tool, name, args)
                    elif name.startswith("opencode_"):
                        # Bind the per-user opencode context in the worker thread.
                        _user_id = self._user_profile.discord_id if self._user_profile is not None else None
                        result = await loop.run_in_executor(
                            None,
                            _run_opencode_tool_with_bridge,
                            name, args, _user_id, self,
                        )
                    else:
                        result = {"error": f"No handler for tool: {name}"}
                except Exception as exc:
                    logger.exception("Gemini Live: tool %s crashed", name)
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                self._emit_event(
                    "tool.failed" if isinstance(result, dict) and result.get("error") else "tool.completed",
                    callId=call_id,
                    name=name,
                )
                responses.append({
                    "id": call_id,
                    "name": name,
                    "response": result,
                })
        finally:
            # ── Typing feedback: end audio indicator ───────────────────────
            if typing_active:
                typing_active = False
            if typing_task:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
        if responses and self._ws:
            payload = {"toolResponse": {"functionResponses": responses}}
            try:
                await self._ws.send(json.dumps(payload))
                logger.info("Sent toolResponse for %d tool call(s)", len(responses))
            except Exception as exc:
                logger.error("Failed to send toolResponse: %s", exc)

    def _maybe_handle_voice_leave_request(self, text: str) -> None:
        normalized = " ".join(text.lower().replace(".", " ").replace(",", " ").split())
        if not any(phrase in normalized for phrase in VOICE_LEAVE_PHRASES):
            return
        self.metrics["voice_leave_requested"] = True
        logger.info("VoiceLive: leave requested by voice transcript: %s", text)
        if self._on_leave_request:
            try:
                self._on_leave_request(text)
            except Exception:
                logger.exception("VoiceLive: failed to schedule voice leave request")

    def _record_output_chunk(self, byte_count: int) -> None:
        now = time.monotonic()
        self.metrics["audio_out_chunks"] += 1
        self.metrics["audio_out_bytes"] += byte_count
        self.metrics["last_output_monotonic"] = now
        last_input = self.metrics.get("last_input_monotonic")
        if last_input is not None:
            self.metrics["last_input_to_output_ms"] = round((now - last_input) * 1000, 1)


class VoiceLiveBridge:
    def __init__(self, voice_channel, discord_adapter, user_profile: Optional[Any] = None,
                 target_user_id: Optional[str] = None):
        self._channel = voice_channel
        self._vc = None
        self._adapter = discord_adapter
        self._guild_id = voice_channel.guild.id
        self._target_user_id = target_user_id or os.getenv("DISCORD_VOICE_LIVE_USER_ID", "1474100257762578597")
        self._user_profile = user_profile
        self._audio_source = LiveAudioSource()
        self._listener = None
        self._leave_requested = False
        self._gemini = GeminiLiveBridge(
            self._audio_source,
            on_wake=self._wake_playback,
            on_leave_request=self._request_leave,
            on_reconnect=self._recreate_pcm_sink,
            user_profile=user_profile,
        )
        self._running = False
        self._started_at = None
        self._watcher_task: Optional[asyncio.Task] = None
        self._receive_restart_task: Optional[asyncio.Task] = None
        self._receive_restarting = False
        self._last_activity_at = time.monotonic()
        self._idle_prompted_at: Optional[float] = None

    def _on_playback_end(self, error=None) -> None:
        if error:
            logger.error("Playback error: %s", error)

    def _wake_playback(self) -> None:
        if not self._running:
            return
        if not self._vc or not self._vc.is_connected():
            return
        try:
            if not self._vc.is_playing():
                self._vc.play(self._audio_source, after=self._on_playback_end)
        except Exception as exc:
            logger.error("VoiceLive: _wake_playback failed: %s", exc)
            if self._vc and self._vc.is_connected():
                try:
                    loop = self._vc.loop
                    loop.create_task(self._restart_receive())
                except Exception:
                    pass

    def _record_activity(self) -> None:
        self._last_activity_at = time.monotonic()
        self._idle_prompted_at = None

    def _recreate_pcm_sink(self) -> None:
        """Called after a Gemini reconnect to force a fresh Opus decoder."""
        logger.info("VoiceLive: recreating PCM sink after Gemini reconnect")
        if self._vc and self._vc.is_connected():
            try:
                if hasattr(self._vc, "is_listening") and self._vc.is_listening():
                    self._vc.stop_listening()
            except Exception:
                pass
            try:
                self._listener = GeminiPCMSink(self._feed_audio)
                self._vc.listen(self._listener, after=self._on_listen_end)
                logger.info("VoiceLive: PCM sink recreated")
            except Exception as e:
                logger.error("VoiceLive: PCM sink recreation failed: %s", e)

    def _request_leave(self, reason: str) -> None:
        if self._leave_requested:
            return
        self._leave_requested = True
        try:
            loop = self._vc.loop if self._vc else asyncio.get_running_loop()
            loop.create_task(self._stop_from_request(reason))
        except Exception:
            logger.exception("VoiceLive: could not schedule requested stop")

    async def _stop_from_request(self, reason: str) -> None:
        logger.info("VoiceLive: stopping from user request: %s", reason)
        await self.stop()

    async def start(self) -> bool:
        logger.info("VoiceLive: connecting to %s in guild %d", self._channel, self._guild_id)
        if voice_recv is None or GeminiPCMSink is None:
            logger.error("discord-ext-voice-recv is not installed; cannot receive Discord voice")
            return False

        existing_vc = getattr(self._channel.guild, "voice_client", None)
        if existing_vc and existing_vc.is_connected():
            try:
                logger.info("VoiceLive: disconnecting existing guild voice client before reconnect")
                await asyncio.wait_for(existing_vc.disconnect(force=True), timeout=10.0)
            except Exception as e:
                logger.warning("VoiceLive: existing voice disconnect failed: %s", e)

        receiver = self._adapter._voice_receivers.get(self._guild_id)
        if receiver:
            receiver.pause()

        try:
            self._vc = await self._channel.connect(
                cls=voice_recv.VoiceRecvClient,
                timeout=60.0,
                reconnect=True,
                self_deaf=False,
            )
        except Exception as e:
            logger.error("Discord voice connect failed: %s", e)
            if receiver:
                receiver.resume()
            return False

        self._listener = GeminiPCMSink(self._feed_audio)
        try:
            self._vc.listen(self._listener, after=self._on_listen_end)
            self._vc.play(self._audio_source, after=self._on_playback_end)
        except Exception as e:
            logger.error("Failed to start Discord voice I/O: %s", e)
            await self.stop()
            return False
        # Criterion #8 — session transition sfx (sounds when audio starts)
        try:
            from sfx import play_sfx
            play_sfx("transition", source=self._audio_source)
        except Exception:
            pass

        try:
            await self._gemini.connect()
        except Exception as e:
            logger.error("Gemini connect failed: %s", e)
            await self.stop()
            return False

        # ── Mute first-turn: immediately signal "audio stream ended" ───────
        # Gemini Live starts its first autonomous turn right after
        # setupComplete. By sending audioStreamEnd immediately, the model
        # sees "user started and ended an empty audio stream" and should
        # NOT generate its opener ("I see you're sharing your screen"
        # hallucination). First-token output would be wasted tokens.
        try:
            await self._gemini._ws.send(
                json.dumps({"realtimeInput": {"audioStreamEnd": True}})
            )
            self._gemini.metrics["audio_stream_end_events"] = \
                self._gemini.metrics.get("audio_stream_end_events", 0) + 1
            self._gemini._audio_stream_open = False
            self._gemini._last_audio_sent_at = None
            logger.info("VoiceLive: sent initial mute audioStreamEnd to suppress first turn")
        except Exception:
            pass

        self._running = True
        self._started_at = time.monotonic()
        self._watcher_task = asyncio.create_task(self._connection_watchdog())
        logger.info("VoiceLive: bridge active for guild %d", self._guild_id)
        return True

    def _feed_audio(self, pcm_16k_mono: bytes) -> None:
        self._record_activity()
        self._gemini.feed_audio(pcm_16k_mono)

    def _on_listen_end(self, error=None) -> None:
        if error:
            logger.error("Voice receive error: %s", error)
        if self._running and self._vc and self._vc.is_connected():
            if self._receive_restarting:
                return
            try:
                loop = self._vc.loop
                self._receive_restart_task = loop.create_task(self._restart_receive())
            except Exception:
                logger.exception("Could not schedule voice receive restart")

    async def _restart_receive(self) -> None:
        self._receive_restarting = True
        try:
            await asyncio.sleep(2.0)
            if not self._running or not self._vc or not self._vc.is_connected():
                return
            try:
                if hasattr(self._vc, "is_listening") and self._vc.is_listening():
                    return
                self._listener = GeminiPCMSink(self._feed_audio)
                self._vc.listen(self._listener, after=self._on_listen_end)
                logger.info("VoiceLive: voice receive restarted")
            except Exception as e:
                logger.error("VoiceLive: receive restart failed: %s", e)
        finally:
            self._receive_restarting = False

    async def _connection_watchdog(self) -> None:
        while self._running:
            await asyncio.sleep(1.0)
            if not self._vc or not self._vc.is_connected():
                if not self._running:
                    return
                logger.warning("VoiceLive: Discord disconnected. Stopping bridge.")
                await self.stop()
                return

            # ── User-presence check: stop if B leaves the voice channel ─────
            try:
                guild = self._vc.guild
                member = guild.get_member(int(self._target_user_id)) if self._target_user_id else None
                if member:
                    member_vc = getattr(getattr(member, "voice", None), "channel", None)
                    if not member_vc or member_vc.id != self._channel.id:
                        logger.info(
                            "VoiceLive: target user %s left the voice channel. Stopping bridge.",
                            self._target_user_id,
                        )
                        await self.stop()
                        return
            except Exception as exc:
                logger.debug("VoiceLive: presence check failed: %s", exc)

            now = time.monotonic()
            idle = now - self._last_activity_at

            # Phase 1: prompt if idle too long and not already prompted
            if (
                IDLE_PROMPT_SECONDS > 0
                and self._idle_prompted_at is None
                and idle >= IDLE_PROMPT_SECONDS
                and self._started_at
                and now - self._started_at >= AUTO_LEAVE_MIN_UPTIME_SECONDS
            ):
                logger.info("VoiceLive: idle for %.0fs — prompting user", idle)
                self._idle_prompted_at = now
                await self._gemini.send_text(IDLE_PROMPT_TEXT)
                continue

            # Phase 2: hang up if no response after grace period
            if self._idle_prompted_at is not None:
                grace = now - self._idle_prompted_at
                if grace >= IDLE_PROMPT_GRACE_SECONDS:
                    logger.info(
                        "VoiceLive: no response after %.0fs grace — hanging up", grace
                    )
                    await self.stop()
                    return
                # Still within grace; don't fall through to plain auto-leave
                continue

            # Fallback: plain auto-leave if prompt system is disabled
            if self._should_auto_leave_quiet():
                logger.info("VoiceLive: auto-leaving after %.0fs of quiet", idle)
                await self.stop()
                return

    def _should_auto_leave_quiet(self) -> bool:
        if AUTO_LEAVE_QUIET_SECONDS <= 0 or self._started_at is None:
            return False
        now = time.monotonic()
        if now - self._started_at < AUTO_LEAVE_MIN_UPTIME_SECONDS:
            return False
        if self._vc and self._vc.is_playing():
            return False
        return now - self._last_activity_at >= AUTO_LEAVE_QUIET_SECONDS

    async def stop(self):
        self._running = False
        if self._receive_restart_task:
            self._receive_restart_task.cancel()
        self._audio_source.finish()
        if self._gemini:
            self._gemini._user_disconnect = True
            await self._gemini.disconnect()
        if self._vc and self._vc.is_connected():
            try:
                if hasattr(self._vc, "is_listening") and self._vc.is_listening():
                    self._vc.stop_listening()
            except Exception:
                pass
            try:
                self._vc.stop_playing() if hasattr(self._vc, "stop_playing") else self._vc.stop()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._vc.disconnect(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass
        receiver = self._adapter._voice_receivers.get(self._guild_id)
        if receiver:
            receiver.resume()
        logger.info("VoiceLive bridge stopped")

    def health(self) -> Dict[str, Any]:
        metrics = dict(getattr(self._gemini, "metrics", {}) or {})
        sink_stats = self._listener.stats() if self._listener and hasattr(self._listener, "stats") else {}
        return {
            "status": "ok" if self._running else "stopped",
            "running": self._running,
            "guild_id": self._guild_id,
            "voice_connected": bool(self._vc and self._vc.is_connected()),
            "receiving_active": bool(
                self._vc and hasattr(self._vc, "is_listening") and self._vc.is_listening()
            ),
            "playback_active": bool(self._vc and self._vc.is_playing()),
            "uptime_seconds": round(time.monotonic() - self._started_at, 3) if self._started_at else 0,
            "quiet_seconds": round(time.monotonic() - self._last_activity_at, 3),
            "auto_leave_quiet_seconds": AUTO_LEAVE_QUIET_SECONDS,
            "idle_prompt_seconds": IDLE_PROMPT_SECONDS,
            "idle_prompt_grace_seconds": IDLE_PROMPT_GRACE_SECONDS,
            "idle_prompted_seconds": round(time.monotonic() - self._idle_prompted_at, 3) if self._idle_prompted_at else None,
            "configured_model": GEMINI_MODEL,
            **sink_stats,
            **metrics,
        }


__all__ = ['voice_recv', 'GeminiPCMSink', 'pcm', 'GeminiLiveBridge', 'VoiceLiveBridge']
__all__ = [n for n in __all__ if n in globals()]
