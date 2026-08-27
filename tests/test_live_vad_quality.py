from bridge_core import _build_realtime_input_config


def test_live_vad_preserves_complete_utterances(monkeypatch) -> None:
    monkeypatch.delenv("SORA_LIVE_VAD_SILENCE_MS", raising=False)
    monkeypatch.delenv("SORA_LIVE_VAD_PREFIX_MS", raising=False)

    config = _build_realtime_input_config()["automaticActivityDetection"]

    assert 1_000 < config["silenceDurationMs"] <= 1_200
    assert config["prefixPaddingMs"] >= 20


def test_live_vad_overrides_are_clamped_to_quality_safe_ranges(monkeypatch) -> None:
    monkeypatch.setenv("SORA_LIVE_VAD_SILENCE_MS", "40")
    monkeypatch.setenv("SORA_LIVE_VAD_PREFIX_MS", "0")

    config = _build_realtime_input_config()["automaticActivityDetection"]

    assert config["silenceDurationMs"] == 800
    assert config["prefixPaddingMs"] == 20

    monkeypatch.setenv("SORA_LIVE_VAD_SILENCE_MS", "2000")
    high = _build_realtime_input_config()["automaticActivityDetection"]
    assert high["silenceDurationMs"] == 1_500
