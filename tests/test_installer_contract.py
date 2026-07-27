import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
REVISION = "1111111111111111111111111111111111111111"


class InstallerContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.hermes = self.root / "hermes"
        self.fake_bin = self.root / "bin"
        self.template = self.root / "template"
        self.log = self.root / "commands.log"
        for path in (self.home, self.fake_bin, self.template):
            path.mkdir(parents=True, exist_ok=True)

        python_bin = self.hermes / "hermes-agent" / "venv" / "bin" / "python"
        python_bin.parent.mkdir(parents=True)
        python_bin.write_text(
            "#!/usr/bin/env bash\n"
            'printf "python cwd=%q" "$PWD" >> "$FAKE_COMMAND_LOG"\n'
            'printf " %q" "$@" >> "$FAKE_COMMAND_LOG"\n'
            'printf "\\n" >> "$FAKE_COMMAND_LOG"\n'
            'if [ "${1:-}" = "-m" ] && [ "${2:-}" = "unittest" ] && [ -n "${EXPECTED_TEST_CWD:-}" ]; then\n'
            '  actual=$(readlink -f "$PWD" 2>/dev/null || printf "%s" "$PWD")\n'
            '  expected=$(readlink -f "$EXPECTED_TEST_CWD" 2>/dev/null || printf "%s" "$EXPECTED_TEST_CWD")\n'
            '  if [ "$actual" != "$expected" ]; then\n'
            '    echo "unexpected unittest cwd: $PWD" >&2\n'
            '    exit 42\n'
            '  fi\n'
            'fi\n'
            'if [ "${1:-}" = "-c" ]; then printf "generated-test-secret\\n"; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        python_bin.chmod(0o755)

        git = self.fake_bin / "git"
        git.write_text(
            "#!/usr/bin/env bash\n"
            'printf "git" >> "$FAKE_COMMAND_LOG"\n'
            'printf " %q" "$@" >> "$FAKE_COMMAND_LOG"\n'
            'printf "\\n" >> "$FAKE_COMMAND_LOG"\n'
            'if [ "${1:-}" = "clone" ]; then\n'
            '  mkdir -p "$3"\n'
            '  cp -a "$FAKE_REPO_TEMPLATE/." "$3/"\n'
            '  mkdir -p "$3/.git"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "${1:-}" = "-C" ]; then\n'
            '  dir="$2"; shift 2\n'
            '  if [ "${1:-}" = "rev-parse" ] && [ "${2:-}" = "--is-inside-work-tree" ]; then\n'
            '    [ -d "$dir/.git" ] && { echo true; exit 0; }\n'
            "    exit 1\n"
            "  fi\n"
            '  if [ "${1:-}" = "rev-parse" ] && [ "${2:-}" = "HEAD" ]; then\n'
            f"    echo {REVISION}; exit 0\n"
            "  fi\n"
            '  if [ "${1:-}" = "status" ]; then\n'
            '    [ -f "$dir/.dirty" ] && echo " M tracked-file"\n'
            "    exit 0\n"
            "  fi\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        git.chmod(0o755)

        (self.template / "plugin.yaml").write_text("name: discord-voice\n", encoding="utf-8")
        (self.template / "requirements.txt").write_text("", encoding="utf-8")
        (self.template / "bridge_stub.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.template / "scripts").mkdir()
        (self.template / "scripts" / "video-frame-feeder.py").write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8"
        )
        (self.template / "tests").mkdir()
        (self.template / "docs").mkdir()

        self.base_env = os.environ.copy()
        self.base_env.update(
            {
                "HOME": str(self.home),
                "HERMES_HOME": str(self.hermes),
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "FAKE_COMMAND_LOG": str(self.log),
                "FAKE_REPO_TEMPLATE": str(self.template),
                "EXPECTED_TEST_CWD": str(self.install_dir),
            }
        )
        for key in ("DISCORD_BOT_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            self.base_env.pop(key, None)

    def tearDown(self):
        self.tempdir.cleanup()

    @property
    def install_dir(self):
        return self.hermes / "plugins" / "discord-voice"

    def run_installer(self, *args, cwd=None, input_text=None, env=None):
        return subprocess.run(
            [str(INSTALLER), *args],
            cwd=cwd or self.root,
            env=env or self.base_env,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def populate_existing(self, dirty=False):
        shutil.copytree(self.template, self.install_dir)
        (self.install_dir / ".git").mkdir()
        if dirty:
            (self.install_dir / ".dirty").write_text("yes\n", encoding="utf-8")

    def ready_env(self, *, google_fallback=False):
        env = self.base_env.copy()
        env["DISCORD_BOT_TOKEN"] = "discord-super-secret"
        if google_fallback:
            env["GOOGLE_API_KEY"] = "google-super-secret"
        else:
            env["GEMINI_API_KEY"] = "gemini-super-secret"
        return env

    def test_fresh_plain_install_reports_revision_and_readiness_separately(self):
        result = self.run_installer(input_text="\n\n\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(self.install_dir.is_dir())
        self.assertIn("Source disposition:  fresh remote clone", result.stdout)
        self.assertIn(f"Source revision:     {REVISION}", result.stdout)
        self.assertIn("Regression tests:    passed", result.stdout)
        self.assertIn("Runtime credentials: incomplete", result.stdout)
        command_log = self.log.read_text(encoding="utf-8")
        self.assertIn("git clone", command_log)
        self.assertIn("python cwd=", command_log)

    def test_existing_clean_install_is_refused_without_mutation(self):
        self.populate_existing(dirty=False)
        marker = self.install_dir / "preserve.me"
        marker.write_text("keep\n", encoding="utf-8")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing directory (refused)", result.stdout)
        self.assertIn(f"revision: {REVISION}", result.stdout)
        self.assertIn("worktree: clean", result.stdout)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
        self.assertNotIn("python -m pip", self.log.read_text(encoding="utf-8"))

    def test_existing_modified_install_is_refused_and_reported(self):
        self.populate_existing(dirty=True)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("worktree: modified", result.stdout)
        self.assertTrue((self.install_dir / ".dirty").exists())

    def test_existing_symlink_reports_target_revision_and_worktree(self):
        source = self.root / "existing-source"
        shutil.copytree(self.template, source)
        (source / ".git").mkdir()
        self.install_dir.parent.mkdir(parents=True)
        self.install_dir.symlink_to(source, target_is_directory=True)

        result = self.run_installer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing symlink (refused)", result.stdout)
        self.assertIn(f"target: {source}", result.stdout)
        self.assertIn(f"revision: {REVISION}", result.stdout)
        self.assertIn("worktree: clean", result.stdout)
        self.assertFalse(self.log.read_text(encoding="utf-8").find("python -m pip") >= 0)

    def test_existing_regular_file_is_classified_without_mutation(self):
        self.install_dir.parent.mkdir(parents=True)
        self.install_dir.write_text("preserve\n", encoding="utf-8")

        result = self.run_installer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing file (refused)", result.stdout)
        self.assertIn("revision: not-applicable", result.stdout)
        self.assertEqual(self.install_dir.read_text(encoding="utf-8"), "preserve\n")

    def test_existing_rerun_reports_disposition_before_no_prompt_credentials(self):
        self.populate_existing(dirty=False)
        result = self.run_installer("--no-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing directory (refused)", result.stdout)
        self.assertIn(f"revision: {REVISION}", result.stdout)
        self.assertNotIn("--no-prompt requires", result.stdout)
        self.assertNotIn("python -m pip", self.log.read_text(encoding="utf-8"))

    def test_from_local_links_exact_checkout_and_preserves_same_link(self):
        source = self.root / "source"
        shutil.copytree(self.template, source)
        (source / ".git").mkdir()
        env = self.ready_env()
        first = self.run_installer("--from-local", "--no-prompt", cwd=source, env=env)
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertTrue(self.install_dir.is_symlink())
        self.assertEqual(self.install_dir.resolve(), source.resolve())
        second = self.run_installer("--from-local", "--no-prompt", cwd=source, env=env)
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertIn("existing local link preserved", second.stdout)
        self.assertEqual(self.install_dir.resolve(), source.resolve())

    def test_from_local_refuses_conflicting_existing_directory(self):
        source = self.root / "source"
        shutil.copytree(self.template, source)
        self.install_dir.mkdir(parents=True)
        marker = self.install_dir / "preserve.me"
        marker.write_text("keep\n", encoding="utf-8")
        result = self.run_installer(
            "--from-local", "--no-prompt", cwd=source, env=self.ready_env()
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to delete or overwrite it", result.stdout)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_no_prompt_missing_credentials_fails_before_mutation(self):
        result = self.run_installer("--no-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DISCORD_BOT_TOKEN", result.stdout)
        self.assertIn("GEMINI_API_KEY or GOOGLE_API_KEY", result.stdout)
        self.assertFalse((self.hermes / "plugins").exists())
        self.assertFalse(self.log.exists())

    def test_no_prompt_rejects_quoted_empty_and_whitespace_credentials(self):
        placeholders = ('""', "''", "   ", '"   "', "'   '")
        for placeholder in placeholders:
            with self.subTest(discord_placeholder=placeholder):
                self.hermes.mkdir(parents=True, exist_ok=True)
                (self.hermes / ".env").write_text(
                    f"DISCORD_BOT_TOKEN={placeholder}\n"
                    "GEMINI_API_KEY=valid-api-key\n",
                    encoding="utf-8",
                )
                result = self.run_installer("--no-prompt")
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("DISCORD_BOT_TOKEN", result.stdout)
                self.assertFalse((self.hermes / "plugins").exists())
                self.assertFalse(self.log.exists())

        for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            with self.subTest(api_key=key):
                (self.hermes / ".env").write_text(
                    "DISCORD_BOT_TOKEN=valid-discord-token\n"
                    f"{key}=\"   \"\n",
                    encoding="utf-8",
                )
                result = self.run_installer("--no-prompt")
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("GEMINI_API_KEY or GOOGLE_API_KEY", result.stdout)
                self.assertFalse((self.hermes / "plugins").exists())
                self.assertFalse(self.log.exists())

    def test_no_prompt_accepts_gemini_key(self):
        result = self.run_installer("--no-prompt", env=self.ready_env())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Runtime credentials: configured", result.stdout)

    def test_no_prompt_accepts_google_key_fallback_from_env_file(self):
        self.hermes.mkdir(parents=True, exist_ok=True)
        (self.hermes / ".env").write_text(
            "DISCORD_BOT_TOKEN=discord-from-file\n"
            "GOOGLE_API_KEY=google-from-file\n",
            encoding="utf-8",
        )
        result = self.run_installer("--no-prompt")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Runtime credentials: configured", result.stdout)

    def test_secret_values_are_not_emitted_or_written_to_test_artifacts(self):
        env = self.ready_env()
        result = self.run_installer("--no-prompt", env=env)
        self.assertEqual(result.returncode, 0, result.stdout)
        combined = result.stdout
        for path in self.root.rglob("*"):
            if path.is_file() and path.stat().st_size < 1_000_000:
                try:
                    combined += path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    pass
        self.assertNotIn(env["DISCORD_BOT_TOKEN"], combined)
        self.assertNotIn(env["GEMINI_API_KEY"], combined)


if __name__ == "__main__":
    unittest.main()
