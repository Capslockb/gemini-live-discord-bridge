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


if __name__ == "__main__":
    unittest.main()
