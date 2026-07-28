import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


class InstallerNestedParentWorktreeTests(unittest.TestCase):
    def test_non_repository_child_is_not_attributed_parent_git_state(self):
        git = shutil.which("git")
        self.assertIsNotNone(git, "git is required for this installer regression")

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "home"
            parent_repo = root / "parent-repo"
            hermes = parent_repo / "hermes"
            install_dir = hermes / "plugins" / "discord-voice"

            home.mkdir()
            parent_repo.mkdir()
            subprocess.run([git, "init", "-q"], cwd=parent_repo, check=True)
            subprocess.run(
                [git, "config", "user.email", "installer-test@example.invalid"],
                cwd=parent_repo,
                check=True,
            )
            subprocess.run(
                [git, "config", "user.name", "Installer Test"],
                cwd=parent_repo,
                check=True,
            )
            (parent_repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run([git, "add", "tracked.txt"], cwd=parent_repo, check=True)
            subprocess.run(
                [git, "commit", "-qm", "test parent revision"],
                cwd=parent_repo,
                check=True,
            )
            parent_revision = subprocess.check_output(
                [git, "rev-parse", "HEAD"], cwd=parent_repo, text=True
            ).strip()

            install_dir.mkdir(parents=True)
            (parent_repo / "dirty.txt").write_text("untracked\n", encoding="utf-8")

            env = os.environ.copy()
            env.update({"HOME": str(home), "HERMES_HOME": str(hermes)})
            result = subprocess.run(
                [str(INSTALLER)],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("existing directory (refused)", result.stdout)
            self.assertIn("revision: unversioned", result.stdout)
            self.assertIn("worktree: not-applicable", result.stdout)
            self.assertNotIn(parent_revision, result.stdout)
            self.assertNotIn("worktree: modified", result.stdout)

    def test_from_local_summary_does_not_report_parent_revision(self):
        git = shutil.which("git")
        self.assertIsNotNone(git, "git is required for this installer regression")

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "home"
            parent_repo = root / "parent-repo"
            hermes = parent_repo / "hermes"
            source = parent_repo / "plugin-source"
            python_bin = hermes / "hermes-agent" / "venv" / "bin" / "python"

            home.mkdir()
            parent_repo.mkdir()
            subprocess.run([git, "init", "-q"], cwd=parent_repo, check=True)
            subprocess.run(
                [git, "config", "user.email", "installer-test@example.invalid"],
                cwd=parent_repo,
                check=True,
            )
            subprocess.run(
                [git, "config", "user.name", "Installer Test"],
                cwd=parent_repo,
                check=True,
            )
            (parent_repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run([git, "add", "tracked.txt"], cwd=parent_repo, check=True)
            subprocess.run(
                [git, "commit", "-qm", "test parent revision"],
                cwd=parent_repo,
                check=True,
            )
            parent_revision = subprocess.check_output(
                [git, "rev-parse", "HEAD"], cwd=parent_repo, text=True
            ).strip()

            source.mkdir()
            (source / "plugin.yaml").write_text("name: discord-voice\n", encoding="utf-8")
            (source / "requirements.txt").write_text("", encoding="utf-8")
            (source / "bridge_stub.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / "scripts").mkdir()
            (source / "scripts" / "video-frame-feeder.py").write_text(
                "#!/usr/bin/env python3\n", encoding="utf-8"
            )
            (source / "tests").mkdir()
            (source / "docs").mkdir()

            python_bin.parent.mkdir(parents=True)
            python_bin.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "${1:-}" = "-c" ]; then printf "generated-test-secret\\n"; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            python_bin.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "HERMES_HOME": str(hermes),
                    "DISCORD_BOT_TOKEN": "discord-test-secret",
                    "GEMINI_API_KEY": "gemini-test-secret",
                }
            )
            result = subprocess.run(
                [str(INSTALLER), "--from-local", "--no-prompt"],
                cwd=source,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("Source revision:     local-unversioned", result.stdout)
            self.assertNotIn(parent_revision, result.stdout)
            install_dir = hermes / "plugins" / "discord-voice"
            self.assertTrue(install_dir.is_symlink())
            self.assertEqual(install_dir.resolve(), source.resolve())


if __name__ == "__main__":
    unittest.main()
