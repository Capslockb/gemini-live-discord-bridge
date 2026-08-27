from __future__ import annotations

import asyncio
import json

from bridge_core import GeminiLiveBridge


class RecordingOutput:
    def feed(self, pcm: bytes) -> None:
        del pcm

    def wake(self) -> bool:
        return True

    def clear(self) -> None:
        pass


class RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def test_idle_microphone_gap_does_not_finalize_continuous_stream() -> None:
    bridge = GeminiLiveBridge(output_source=RecordingOutput())
    socket = RecordingWebSocket()
    bridge._ws = socket
    bridge._audio_stream_open = True
    bridge._last_audio_sent_at = 0.0

    asyncio.run(bridge._maybe_end_idle_audio_stream())

    assert socket.sent == []
    assert bridge._audio_stream_open is True
