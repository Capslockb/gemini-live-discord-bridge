from bridge_core import GeminiLiveBridge


class SilentOutput:
    def feed(self, pcm: bytes) -> None:
        del pcm

    def wake(self) -> bool:
        return False

    def clear(self) -> None:
        pass


def test_transcript_subword_deltas_are_concatenated_and_finalized() -> None:
    events: list[dict] = []
    bridge = GeminiLiveBridge(output_source=SilentOutput(), on_event=events.append)
    bridge._append_note_event = lambda *_: None

    bridge._record_transcript("input", {"text": "Ple"})
    bridge._record_transcript("input", {"text": "ase"})
    bridge._record_transcript("input", {"text": " listen"})

    partials = [event for event in events if event.get("kind") == "transcript.user"]
    assert partials[-1]["text"] == "Please listen"
    assert partials[-1]["final"] is False

    bridge._finalize_transcripts()
    finals = [event for event in events if event.get("kind") == "transcript.user" and event.get("final")]
    assert finals[-1]["text"] == "Please listen"

    bridge._record_transcript("input", {"text": "Next"})
    assert events[-1]["text"] == "Next"
