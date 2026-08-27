from __future__ import annotations

from array import array

from bridge_core import GeminiLiveBridge


class RecordingOutput:
    def __init__(self) -> None:
        self.clear_count = 0

    def feed(self, pcm: bytes) -> None:
        del pcm

    def wake(self) -> bool:
        return False

    def clear(self) -> None:
        self.clear_count += 1


def pcm_frame(amplitude: int) -> bytes:
    return array("h", [amplitude] * 320).tobytes()


def test_mobile_echo_guard_suppresses_low_residual_during_output() -> None:
    output = RecordingOutput()
    bridge = GeminiLiveBridge(output_source=output, output_echo_guard=True)
    bridge._output_turn_open = True

    bridge.feed_audio(pcm_frame(500))

    assert bridge._output_turn_open is True
    assert output.clear_count == 0
    assert bridge._send_q.empty()


def test_mobile_echo_guard_confirms_and_preserves_real_barge_in() -> None:
    output = RecordingOutput()
    bridge = GeminiLiveBridge(output_source=output, output_echo_guard=True)
    bridge._output_turn_open = True
    speech = pcm_frame(8000)

    for _ in range(3):
        bridge.feed_audio(speech)
        assert bridge._output_turn_open is True
        assert bridge._send_q.empty()

    bridge.feed_audio(speech)

    assert bridge._output_turn_open is False
    assert output.clear_count == 1
    assert [bridge._send_q.get_nowait() for _ in range(4)] == [speech] * 4
    assert bridge._send_q.empty()
