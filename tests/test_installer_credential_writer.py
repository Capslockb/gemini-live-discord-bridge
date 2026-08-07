"""Focused regression tests for literal, atomic installer credential writes."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WRITER_PATH = ROOT / "scripts" / "write_env_value.py"
INSTALLER_PATH = ROOT / "install.sh"


def _load_writer():
    spec = importlib.util.spec_from_file_location("_credential_writer_test_target", WRITER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load credential writer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCredentialWriter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.writer = _load_writer()

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.env_file = Path(self.tempdir.name) / ".env"

    def test_replaces_once_and_preserves_literal_special_characters(self):
        self.env_file.write_bytes(
            b"# keep this comment\n"
            b"DISCORD_BOT_TOKEN=old\n"
            b"OTHER=value\n"
            b"DISCORD_BOT_TOKEN=stale\n"
        )
        value = "A&|\\ spaces ' \" = Ț".encode("utf-8")

        self.writer.write_env_value(self.env_file, "DISCORD_BOT_TOKEN", value)

        data = self.env_file.read_bytes()
        self.assertEqual(data.count(b"DISCORD_BOT_TOKEN="), 1)
        self.assertIn(b"DISCORD_BOT_TOKEN=" + value + b"\n", data)
        self.assertIn(b"# keep this comment\n", data)
        self.assertIn(b"OTHER=value\n", data)

    def test_appends_absent_key_without_corrupting_prior_line(self):
        self.env_file.write_bytes(b"OTHER=value")

        self.writer.write_env_value(self.env_file, "GEMINI_API_KEY", b"new-value")

        self.assertEqual(
            self.env_file.read_bytes(),
            b"OTHER=value\nGEMINI_API_KEY=new-value\n",
        )

    def test_rejects_line_breaks_and_nul_without_mutation(self):
        original = b"DISCORD_BOT_TOKEN=old\nOTHER=value\n"
        self.env_file.write_bytes(original)

        for value in (b"first\nsecond", b"first\rsecond", b"first\x00second"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.writer.write_env_value(self.env_file, "DISCORD_BOT_TOKEN", value)
                self.assertEqual(self.env_file.read_bytes(), original)

    def test_destination_permissions_are_restrictive(self):
        self.env_file.write_bytes(b"DISCORD_BOT_TOKEN=old\n")
        os.chmod(self.env_file, 0o644)

        self.writer.write_env_value(self.env_file, "DISCORD_BOT_TOKEN", b"replacement")

        self.assertEqual(os.stat(self.env_file).st_mode & 0o777, 0o600)

    def test_replace_failure_preserves_previous_file_and_cleans_temp(self):
        original = b"DISCORD_BOT_TOKEN=old\nOTHER=value\n"
        self.env_file.write_bytes(original)

        with patch.object(self.writer.os, "replace", side_effect=OSError("synthetic rename failure")):
            with self.assertRaises(OSError):
                self.writer.write_env_value(self.env_file, "DISCORD_BOT_TOKEN", b"replacement")

        self.assertEqual(self.env_file.read_bytes(), original)
        self.assertEqual(list(self.env_file.parent.glob("..env.*")), [])

    def test_success_has_no_post_replace_chmod_failure_point(self):
        self.env_file.write_bytes(b"DISCORD_BOT_TOKEN=old\n")

        with patch.object(self.writer.os, "chmod", side_effect=OSError("must not run")) as chmod_mock:
            self.writer.write_env_value(self.env_file, "DISCORD_BOT_TOKEN", b"replacement")

        chmod_mock.assert_not_called()
        self.assertEqual(
            self.env_file.read_bytes(),
            b"DISCORD_BOT_TOKEN=replacement\n",
        )
        self.assertEqual(os.stat(self.env_file).st_mode & 0o777, 0o600)

    def test_cli_never_echoes_credential_on_success_or_failure(self):
        sentinel = "sentinel-&|\\-Ț-secret".encode("utf-8")

        success = subprocess.run(
            [sys.executable, str(WRITER_PATH), str(self.env_file), "GEMINI_API_KEY"],
            input=sentinel,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(success.returncode, 0)
        self.assertNotIn(sentinel, success.stdout)
        self.assertNotIn(sentinel, success.stderr)

        before = self.env_file.read_bytes()
        failure = subprocess.run(
            [sys.executable, str(WRITER_PATH), str(self.env_file), "GEMINI_API_KEY"],
            input=sentinel + b"\nsecond-line",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(failure.returncode, 1)
        self.assertNotIn(sentinel, failure.stdout)
        self.assertNotIn(sentinel, failure.stderr)
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_installer_passes_secret_only_over_stdin(self):
        installer = INSTALLER_PATH.read_text(encoding="utf-8")
        expected = (
            "printf '%s' \"$val\" | \"$PYTHON_BIN\" \"$CREDENTIAL_WRITER\" "
            "\"$ENV_FILE\" \"$var\""
        )
        self.assertIn(expected, installer)
        self.assertNotIn('sed -i "s|^${var}=.*|${var}=${val}|"', installer)
        self.assertNotIn('echo "${var}=${val}" >> "$ENV_FILE"', installer)


if __name__ == "__main__":
    unittest.main()
