from __future__ import annotations

import unittest

import numpy as np

from bridge import GeminiLiveBridge
from bridge_config import GEMINI_WS_URL


class FakeOutput:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.clear_count = 0

    def feed(self, pcm: bytes) -> None:
        self.chunks.append(pcm)

    def wake(self) -> bool:
        return True

    def clear(self) -> None:
        self.clear_count += 1
        self.chunks.clear()


class TestTransportNeutralSession(unittest.TestCase):
    def test_transport_contract_transcripts_interrupt_and_header_auth(self):
        events: list[dict] = []
        output = FakeOutput()
        bridge = GeminiLiveBridge(
            output_source=output,
            on_event=events.append,
            api_key="x" * 32,
            context_id="ctx-test",
        )
        self.assertNotIn(bridge._api_key, GEMINI_WS_URL)
        self.assertEqual(bridge._provider_headers()["x-goog-api-key"], bridge._api_key)

        bridge._record_transcript("input", {"text": "hello", "finished": True})
        bridge._record_transcript("output", {"text": "hi", "finished": False})
        output.feed(b"model-audio")
        bridge._output_turn_open = True
        samples = np.full(320, 8000, dtype=np.int16).tobytes()
        bridge.feed_audio(samples)

        kinds = [event["kind"] for event in events]
        self.assertEqual(kinds[:2], ["transcript.user", "transcript.sora"])
        self.assertIn("audio.interrupted", kinds)
        self.assertEqual(events[0]["contextId"], "ctx-test")
        self.assertTrue(events[0]["final"])
        self.assertEqual(output.clear_count, 1)
        self.assertFalse(bridge._output_turn_open)

if __name__ == "__main__":
    unittest.main()
