from __future__ import annotations

import math
import os
import subprocess
import sys
import wave
from array import array

import sfx


def test_malformed_sfx_limits_fall_back_without_import_failure(monkeypatch) -> None:
    env = os.environ.copy()
    env["DISCORD_VOICE_LIVE_SFX_MAX_SECONDS"] = "not-a-number"
    env["DISCORD_VOICE_LIVE_SFX_MAX_PEAK"] = "also-invalid"
    env["DISCORD_VOICE_LIVE_SFX_FADE_MS"] = "bad"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sfx; assert sfx._MAX_DURATION_SECONDS == 0.65; "
            "assert sfx._MAX_PEAK == 0.35; assert sfx._FADE_MS == 12",
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_long_loud_sfx_is_bounded_and_faded(monkeypatch, tmp_path) -> None:
    sample_rate = 24_000
    samples = array(
        "h",
        [int(math.sin(index * 2 * math.pi * 440 / sample_rate) * 32_000) for index in range(sample_rate * 3)],
    )
    path = tmp_path / "error.wav"
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(samples.tobytes())

    monkeypatch.setattr(sfx, "SFX_DIR", tmp_path)
    monkeypatch.setitem(sfx.DEFAULT_SFX_PATHS, "error", "error.wav")
    monkeypatch.setitem(sfx.DEFAULT_SFX_VOLUMES, "error", 1.0)
    sfx.invalidate_cache()

    pcm = sfx.load_slot_pcm("error")
    assert pcm is not None
    rendered = array("h")
    rendered.frombytes(pcm)

    assert len(rendered) <= int(sample_rate * 0.65)
    assert max(abs(value) for value in rendered) <= int(32_767 * 0.35)
    assert abs(rendered[0]) < 100
    assert abs(rendered[-1]) < 100
