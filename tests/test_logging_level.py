import logging

from bridge_config import _configured_log_level


def test_voice_logging_defaults_to_info_and_allows_explicit_debug(monkeypatch) -> None:
    monkeypatch.delenv("SORA_VOICE_LOG_LEVEL", raising=False)
    assert _configured_log_level() == logging.INFO

    monkeypatch.setenv("SORA_VOICE_LOG_LEVEL", "DEBUG")
    assert _configured_log_level() == logging.DEBUG

    monkeypatch.setenv("SORA_VOICE_LOG_LEVEL", "not-a-level")
    assert _configured_log_level() == logging.INFO
