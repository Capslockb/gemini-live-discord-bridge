from __future__ import annotations

import asyncio
import logging

import pytest

from bridge_core import AudioFramePacer, drop_audio_backlog
from mobile_realtime import configure_safe_transport_logging


def test_audio_frame_pacer_spaces_burst_frames_at_pcm_duration() -> None:
    pacer = AudioFramePacer(sample_rate=16_000, sample_width=2, channels=1)
    frame = b"\x00" * 640  # 20 ms of 16 kHz mono PCM16

    assert pacer.delay_for(frame, now=10.0) == pytest.approx(0.0)
    assert pacer.delay_for(frame, now=10.0) == pytest.approx(0.02)


def test_audio_frame_pacer_resets_after_idle_instead_of_catching_up() -> None:
    pacer = AudioFramePacer(sample_rate=16_000, sample_width=2, channels=1)
    frame = b"\x00" * 640

    pacer.delay_for(frame, now=10.0)
    pacer.delay_for(frame, now=10.0)

    assert pacer.delay_for(frame, now=10.1) == pytest.approx(0.0)
    assert pacer.delay_for(frame, now=10.1) == pytest.approx(0.02)


def test_drop_audio_backlog_discards_oldest_frames() -> None:
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    for value in range(6):
        queue.put_nowait(bytes([value]))

    dropped = drop_audio_backlog(queue, max_frames=3)

    assert dropped == 3
    assert [queue.get_nowait() for _ in range(3)] == [b"\x03", b"\x04", b"\x05"]


def test_mobile_transport_disables_websocket_frame_debug_logging() -> None:
    client_logger = logging.getLogger("websockets.client")
    server_logger = logging.getLogger("websockets.server")
    original_levels = (client_logger.level, server_logger.level)
    try:
        client_logger.setLevel(logging.DEBUG)
        server_logger.setLevel(logging.DEBUG)

        configure_safe_transport_logging()

        assert client_logger.level == logging.WARNING
        assert server_logger.level == logging.WARNING
    finally:
        client_logger.setLevel(original_levels[0])
        server_logger.setLevel(original_levels[1])
