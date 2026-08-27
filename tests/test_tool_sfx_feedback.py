from __future__ import annotations

import asyncio
import time

import bridge_core
from bridge_core import GeminiLiveBridge


class RecordingOutput:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def feed(self, pcm: bytes) -> None:
        if pcm:
            self.frames.append(pcm)

    def wake(self) -> bool:
        return True

    def clear(self) -> None:
        pass


class RecordingSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def test_slow_tool_uses_one_bounded_progress_cue(monkeypatch) -> None:
    output = RecordingOutput()
    bridge = GeminiLiveBridge(output_source=output)
    bridge._ws = RecordingSocket()

    monkeypatch.setattr(bridge_core, "TYPING_SOUND_ENABLED", True)
    monkeypatch.setattr(bridge_core, "generate_typing_pcm", lambda: b"cue")

    def slow_tool(name, args):
        del name, args
        time.sleep(0.35)
        return {"result": "done"}

    monkeypatch.setattr(bridge_core, "_run_local_tool", slow_tool)

    asyncio.run(
        bridge._handle_tool_call(
            {"functionCalls": [{"id": "call-1", "name": "local_honcho", "args": {"query": "x"}}]},
        ),
    )

    assert output.frames == [b"cue"]
