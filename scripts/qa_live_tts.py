from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sys
import time
import wave
from array import array
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge_core
from bridge_core import GeminiLiveBridge
from mobile_realtime import MobileUserProfile

QA_DIR = Path("/home/caps/sora-mobile-delivery/qa")
FRAME_BYTES = 640  # 20 ms of 16 kHz mono s16le


class RecordingOutput:
    def __init__(self) -> None:
        self.frames: list[tuple[float, bytes]] = []
        self.clear_events = 0

    def feed(self, pcm: bytes) -> None:
        if pcm:
            self.frames.append((time.monotonic(), bytes(pcm)))

    def wake(self) -> bool:
        return False

    def clear(self) -> None:
        self.clear_events += 1


def merge_chunks(chunks: list[str]) -> str:
    result = ""
    for raw in chunks:
        text = re.sub(r"\s+", " ", raw).strip()
        if not text:
            continue
        if not result:
            result = text
        elif text.startswith(result):
            result = text
        elif result.endswith(text):
            continue
        else:
            overlap = 0
            limit = min(len(result), len(text))
            for size in range(limit, 0, -1):
                if result[-size:].lower() == text[:size].lower():
                    overlap = size
                    break
            result = (result + ("" if overlap else " ") + text[overlap:]).strip()
    return result


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref = re.findall(r"[a-z0-9]+", reference.lower())
    hyp = re.findall(r"[a-z0-9]+", hypothesis.lower())
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for index, word in enumerate(ref, 1):
        current = [index]
        for j, candidate in enumerate(hyp, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != candidate)))
        previous = current
    return previous[-1] / len(ref)


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(pcm)


def audio_metrics(pcm: bytes) -> dict[str, Any]:
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return {"duration_s": 0.0, "clipping_ratio": 0.0, "interior_silence_runs": 0, "max_interior_silence_ms": 0}
    window = 480  # 20 ms at 24 kHz
    active: list[bool] = []
    for start in range(0, len(samples), window):
        chunk = samples[start : start + window]
        rms = math.sqrt(sum(float(v) * float(v) for v in chunk) / max(1, len(chunk))) / 32768.0
        active.append(rms >= 0.004)
    try:
        first = active.index(True)
        last = len(active) - 1 - active[::-1].index(True)
    except ValueError:
        first = last = 0
    runs: list[int] = []
    run = 0
    for is_active in active[first : last + 1]:
        if is_active:
            if run >= 2:
                runs.append(run)
            run = 0
        else:
            run += 1
    if run >= 2:
        runs.append(run)
    clipped = sum(1 for value in samples if abs(value) >= 32760)
    return {
        "duration_s": round(len(samples) / 24_000, 3),
        "clipping_ratio": round(clipped / len(samples), 8),
        "interior_silence_runs": len(runs),
        "max_interior_silence_ms": max(runs, default=0) * 20,
    }


async def wait_until(predicate, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(label)


async def main() -> None:
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)
    logging.getLogger("websockets.server").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("voice-live").setLevel(logging.INFO)
    bridge_core.INITIAL_GREETING = ""
    output = RecordingOutput()
    events: list[dict[str, Any]] = []

    def on_event(event: dict[str, Any]) -> None:
        events.append({"at": time.monotonic(), **event})

    bridge = GeminiLiveBridge(
        output_source=output,
        on_event=on_event,
        context_id="qa-tts-live",
        user_profile=MobileUserProfile(
            {
                "web_search",
                "web_extract",
                "local_honcho",
                "local_delegate_quick",
                "local_delegate_execute",
                "local_delegate_status",
                "local_delegate_health",
            },
            "user",
        ),
        output_echo_guard=False,
    )
    await bridge.connect()
    report: dict[str, Any] = {"model": bridge.metrics.get("model"), "turns": []}
    turns = [
        (
            "live-turn-1",
            "Hello SORA. Please listen to this full sentence, including its natural pauses, and then repeat exactly: cobalt river seven.",
            False,
        ),
        (
            "live-turn-2",
            "Search the web for the current Gemini Live model. Then run OpenCode immediately and have it return the exact phrase: live tools verified.",
            True,
        ),
        (
            "live-turn-3",
            "Check the OpenCode task status now. Read its exact result aloud, and do not claim completion unless the backend is actually finished.",
            True,
        ),
    ]
    try:
        for name, reference, requires_tool in turns:
            event_start = len(events)
            frame_start = len(output.frames)
            raw = (QA_DIR / f"{name}.pcm").read_bytes()
            started = time.monotonic()
            for offset in range(0, len(raw), FRAME_BYTES):
                bridge.feed_audio(raw[offset : offset + FRAME_BYTES])
                await asyncio.sleep(0.02)
            await bridge.end_audio_stream()
            stream_end_at = time.monotonic()

            def complete() -> bool:
                scoped = events[event_start:]
                completed = [
                    event
                    for event in scoped
                    if event.get("kind") == "turn.completed" and event["at"] >= stream_end_at
                ]
                if not completed:
                    return False
                if not requires_tool:
                    return True
                tool_done = [event for event in scoped if event.get("kind") in {"tool.completed", "tool.failed"}]
                return bool(tool_done) and completed[-1]["at"] >= tool_done[-1]["at"]

            await wait_until(complete, 120.0, f"{name} did not complete")
            await asyncio.sleep(0.8)
            scoped = events[event_start:]
            pcm = b"".join(frame for _, frame in output.frames[frame_start:])
            write_wav(QA_DIR / f"{name}-response.wav", pcm)
            input_text = merge_chunks([str(e.get("text", "")) for e in scoped if e.get("kind") == "transcript.user"])
            output_text = merge_chunks([str(e.get("text", "")) for e in scoped if e.get("kind") == "transcript.sora"])
            tools = [
                {"kind": e.get("kind"), "name": e.get("name")}
                for e in scoped
                if str(e.get("kind", "")).startswith("tool.")
            ]
            report["turns"].append(
                {
                    "name": name,
                    "input_transcript": input_text,
                    "input_wer": round(word_error_rate(reference, input_text), 4),
                    "output_transcript": output_text,
                    "tools": tools,
                    "latency_to_first_output_ms": round(
                        ((output.frames[frame_start][0] - started) * 1000) if len(output.frames) > frame_start else -1,
                    ),
                    "audio": audio_metrics(pcm),
                },
            )
            await asyncio.sleep(0.7)
    finally:
        await bridge.disconnect()
    report["bridge_metrics"] = {
        key: value for key, value in bridge.metrics.items()
        if key not in {"notes_file", "last_input_transcript", "last_output_transcript"}
    }
    (QA_DIR / "live-tts-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
