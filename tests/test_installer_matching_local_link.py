import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
REVISION = "2222222222222222222222222222222222222222"


class InstallerMatchingLocalLinkTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.hermes = self.root / "hermes"
        self.source = self.root / "source"
        self.fake_bin = self.root / "bin"
        for path in (self.home, self.source, self.fake_bin):
            path.mkdir(parents=True, exist_ok=True)

        python_bin = self.hermes / "hermes-agent" / "venv" / "bin" / "python"
        python_bin.parent.mkdir(parents=True)
        python_bin.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "${1:-}" = "-c" ]; then printf "generated-test-secret\\n"; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        python_bin.chmod(0o755)

        git = self.fake_bin / "git"
        git.write_text(
            "#!/usr/bin/env bash\n"
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

        (self.source / ".git").mkdir()
        (self.source / "plugin.yaml").write_text("name: discord-voice\n", encoding="utf-8")
        (self.source / "requirements.txt").write_text("", encoding="utf-8")
        (self.source / "bridge_stub.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.source / "scripts").mkdir()
        (self.source / "scripts" / "video-frame-feeder.py").write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8"
        )
        (self.source / "docs").mkdir()

        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.home),
                "HERMES_HOME": str(self.hermes),
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "DISCORD_BOT_TOKEN": "discord-test-token",
                "GEMINI_API_KEY": "gemini-test-key",
            }
        )
        self.env.pop("GOOGLE_API_KEY", None)

    def tearDown(self):
        self.tempdir.cleanup()

    @property
    def install_dir(self):
        return self.hermes / "plugins" / "discord-voice"

    def run_installer(self):
        return subprocess.run(
            [str(INSTALLER), "--from-local", "--no-prompt"],
            cwd=self.source,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_preserved_matching_link_reports_clean_then_modified_git_state(self):
        first = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertTrue(self.install_dir.is_symlink())
        self.assertEqual(self.install_dir.resolve(), self.source.resolve())

        clean = self.run_installer()
        self.assertEqual(clean.returncode, 0, clean.stdout)
        self.assertIn("disposition: existing local link preserved", clean.stdout)
        self.assertIn(f"revision: {REVISION}", clean.stdout)
        self.assertIn("worktree: clean", clean.stdout)
        self.assertEqual(self.install_dir.resolve(), self.source.resolve())

        (self.source / ".dirty").write_text("yes\n", encoding="utf-8")
        modified = self.run_installer()
        self.assertEqual(modified.returncode, 0, modified.stdout)
        self.assertIn("disposition: existing local link preserved", modified.stdout)
        self.assertIn(f"revision: {REVISION}", modified.stdout)
        self.assertIn("worktree: modified", modified.stdout)
        self.assertTrue((self.source / ".dirty").exists())
        self.assertEqual(self.install_dir.resolve(), self.source.resolve())


if __name__ == "__main__":
    unittest.main()
