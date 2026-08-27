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
from bridge_config import DISCORD_CH, DISCORD_SR, FRAME_SIZE, GEMINI_IN_CH, GEMINI_IN_SR, GEMINI_OUT_CH, GEMINI_OUT_SR, OUTPUT_READ_WAIT_SECONDS, SAMPLE_WIDTH, TYPING_SFX_PATH, TYPING_SFX_VOLUME, TYPING_SYNTH_FALLBACK

def _put_drop_oldest(q: "queue.Queue[Optional[bytes]]", item: Optional[bytes]) -> None:
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def _resample_pcm(data: bytes, src_rate: int, src_ch: int, dst_rate: int, dst_ch: int) -> bytes:
    if not data:
        return b""
    raw = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if src_ch == 2 and dst_ch == 1:
        raw = raw.reshape(-1, 2).mean(axis=1)
    elif src_ch == 1 and dst_ch == 2:
        raw = np.repeat(raw, 2)
    if src_rate != dst_rate:
        src_len = len(raw)
        dst_len = int(src_len * dst_rate / src_rate)
        # Fast path: when the resample ratio is an integer >= 2 (our
        # common case is 48k→16k = 3:1), use a box-filter average
        # instead of np.interp. A linear interpolation allocates two
        # large temporary arrays per frame; a box average allocates
        # only the output. Voice quality is identical for speech.
        ratio = src_rate // dst_rate
        if ratio * dst_rate == src_rate and ratio >= 2 and src_len % ratio == 0:
            raw = raw.reshape(-1, ratio).mean(axis=1)
        else:
            raw = np.interp(np.linspace(0, src_len - 1, dst_len), np.arange(src_len), raw)
    raw = np.clip(raw, -32768, 32767).astype(np.int16)
    return raw.tobytes()


def downsample_for_gemini(pcm_48k_stereo: bytes) -> bytes:
    return _resample_pcm(pcm_48k_stereo, DISCORD_SR, DISCORD_CH, GEMINI_IN_SR, GEMINI_IN_CH)


def upsample_for_discord(pcm_24k_mono: bytes) -> bytes:
    return _resample_pcm(pcm_24k_mono, GEMINI_OUT_SR, GEMINI_OUT_CH, DISCORD_SR, DISCORD_CH)


def _silence_pcm(sample_rate: int, channels: int, ms: int) -> bytes:
    samples = int(sample_rate * ms / 1000) * channels
    return b"\x00" * samples * SAMPLE_WIDTH


def _fade_in_pcm_24k_mono(pcm: bytes, fade_ms: int) -> bytes:
    if not pcm or fade_ms <= 0:
        return pcm
    raw = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    fade_samples = min(len(raw), int(GEMINI_OUT_SR * fade_ms / 1000))
    if fade_samples <= 1:
        return pcm
    raw[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    return np.clip(raw, -32768, 32767).astype(np.int16).tobytes()


_TYPING_SFX_CACHE: Optional[bytes] = None


_TYPING_SFX_WARNED = False


def _scale_pcm16(pcm: bytes, volume: float) -> bytes:
    if not pcm or volume == 1.0:
        return pcm
    raw = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    raw *= max(0.0, volume)
    return np.clip(raw, -32768, 32767).astype(np.int16).tobytes()


def _load_typing_sfx_pcm() -> Optional[bytes]:
    """Load an actual WAV keyboard SFX as 24 kHz mono PCM16."""
    global _TYPING_SFX_CACHE, _TYPING_SFX_WARNED
    if _TYPING_SFX_CACHE is not None:
        return _TYPING_SFX_CACHE
    if not TYPING_SFX_PATH:
        return None
    path = Path(TYPING_SFX_PATH).expanduser()
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        if sample_width != SAMPLE_WIDTH:
            raise ValueError(f"expected 16-bit WAV, got {sample_width * 8}-bit")
        pcm = _resample_pcm(frames, sample_rate, channels, GEMINI_OUT_SR, GEMINI_OUT_CH)
        if len(pcm) > int(GEMINI_OUT_SR * 0.35) * SAMPLE_WIDTH:
            pcm = pcm[: int(GEMINI_OUT_SR * 0.35) * SAMPLE_WIDTH]
        _TYPING_SFX_CACHE = _scale_pcm16(pcm, TYPING_SFX_VOLUME)
        logger.info("VoiceLive: loaded typing SFX from %s", path)
        return _TYPING_SFX_CACHE
    except Exception as exc:
        if not _TYPING_SFX_WARNED:
            logger.warning("VoiceLive: typing SFX load failed (%s): %s", path, exc)
            _TYPING_SFX_WARNED = True
        return None


def generate_typing_pcm() -> bytes:
    """Return a real keyboard SFX when configured; synthetic fallback is opt-in."""
    sfx = _load_typing_sfx_pcm()
    if sfx:
        return sfx
    if not TYPING_SYNTH_FALLBACK:
        return b""

    sr = GEMINI_OUT_SR  # 24000
    duration_sec = 0.015 + random.random() * 0.010  # 15-25 ms total
    samples = int(sr * duration_sec)
    t = np.arange(samples, dtype=np.float64) / sr

    # Low-passed fallback tap: no high tick, so it will not read as a beep.
    thud_freq = 160 + random.randint(0, 90)
    thud = np.sin(2 * np.pi * thud_freq * t)
    thud_env = np.exp(-t / (duration_sec * 0.28))
    thud *= thud_env

    noise = np.random.default_rng().normal(0.0, 0.025, samples)
    noise *= np.exp(-t / (duration_sec * 0.18))
    click = thud + noise
    max_val = np.max(np.abs(click))
    if max_val > 0:
        click = click / max_val * 0.035 * 32767.0
    click = np.clip(click, -32768, 32767).astype(np.int16)
    return click.tobytes()


def _has_speech_energy(pcm_48k_stereo: bytes) -> bool:
    """Fast VAD: peak amplitude over a 20ms frame. Anything below the noise
    floor is treated as silence.

    Implemented in pure Python with struct (no numpy import) so it can run
    inside the voice_recv dispatch thread on every 20ms frame without
    triggering GC pauses or numpy allocation overhead. The previous numpy
    implementation allocated 4 arrays per frame; under load that caused
    voice_recv to flush its decoder buffer ("N packets were lost being
    flushed in decoder-N") and produced audible lag in the conversation.
    """
    if not pcm_48k_stereo or len(pcm_48k_stereo) < 2:
        return False
    # int16 little-endian, ~960 samples per 20ms frame at 48k stereo.
    # Use memoryview + max() of decoded samples (still ~30us for 4KB).
    mv = memoryview(pcm_48k_stereo).cast("h")
    if not mv:
        return False
    # Sample every 4th value (skip 3 of every 4 L/R pairs) — 4x speedup,
    # speech peaks are well above the noise floor so we won't miss them.
    # If the absolute peak is below ~600 (int16 scale), it's noise.
    peak = 0
    for s in mv[::8]:
        a = -s if s < 0 else s
        if a > peak:
            peak = a
            if peak >= 600:
                return True
    return peak >= 600


def _has_speech_energy_16k(pcm_16k_mono: bytes) -> bool:
    """Same fast-VAD logic as _has_speech_energy, but on 16 kHz mono PCM
    (the format the bridge sends to Gemini via feed_audio). Stride is 4
    instead of 8 because there's no stereo channel to skip. ~15us per
    640-byte frame (20ms at 16kHz). Threshold is 400 (slightly lower than
    the 48k stereo version because downsampling attenuates peaks).
    """
    if not pcm_16k_mono or len(pcm_16k_mono) < 2:
        return False
    mv = memoryview(pcm_16k_mono).cast("h")
    if not mv:
        return False
    peak = 0
    for s in mv[::4]:
        a = -s if s < 0 else s
        if a > peak:
            peak = a
            if peak >= 400:
                return True
    return peak >= 400


def _has_barge_in_energy_16k(
    pcm_16k_mono: bytes,
    *,
    min_peak: int = 2200,
    min_rms: int = 850,
) -> bool:
    """Reject low playback residue while retaining normal close-mic speech."""
    if not pcm_16k_mono or len(pcm_16k_mono) < 2:
        return False
    samples = memoryview(pcm_16k_mono).cast("h")
    if not samples:
        return False
    peak = 0
    square_sum = 0
    for sample in samples:
        absolute = -sample if sample < 0 else sample
        if absolute > peak:
            peak = absolute
        square_sum += sample * sample
    return peak >= min_peak and square_sum >= len(samples) * min_rms * min_rms


try:
    import discord as _discord_audio
    _AudioSourceBase = _discord_audio.AudioSource
except Exception:
    _AudioSourceBase = object


class LiveAudioSource(_AudioSourceBase):
    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=256)
        self._buffer = bytearray()
        self._stopped = False

    def feed(self, pcm_24k_mono: bytes) -> None:
        if self._stopped:
            return
        _put_drop_oldest(self._q, pcm_24k_mono)

    def wake(self) -> bool:
        return True

    def clear(self) -> None:
        with self._q.mutex:
            self._q.queue.clear()
        self._buffer.clear()

    def finish(self) -> None:
        self._stopped = True
        _put_drop_oldest(self._q, None)

    def read(self) -> bytes:
        while len(self._buffer) < FRAME_SIZE:
            if self._stopped:
                return b""
            try:
                chunk = self._q.get(timeout=OUTPUT_READ_WAIT_SECONDS)
            except queue.Empty:
                return b"\x00" * FRAME_SIZE
            if chunk is None:
                self._stopped = True
                return b""
            pcm_48k_stereo = upsample_for_discord(chunk)
            self._buffer.extend(pcm_48k_stereo)
        frame = bytes(self._buffer[:FRAME_SIZE])
        self._buffer = self._buffer[FRAME_SIZE:]
        return frame

    def is_opus(self) -> bool:
        return False

    def cleanup(self):
        self._stopped = True


__all__ = ['_put_drop_oldest', '_resample_pcm', 'downsample_for_gemini', 'upsample_for_discord', '_silence_pcm', '_fade_in_pcm_24k_mono', '_TYPING_SFX_CACHE', '_TYPING_SFX_WARNED', '_scale_pcm16', '_load_typing_sfx_pcm', 'generate_typing_pcm', '_has_speech_energy', '_has_speech_energy_16k', '_AudioSourceBase', 'LiveAudioSource']
__all__ = [n for n in __all__ if n in globals()]
