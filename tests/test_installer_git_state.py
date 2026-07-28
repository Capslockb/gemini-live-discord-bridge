import os
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
REVISION = "2222222222222222222222222222222222222222"


class InstallerGitStateFailureTests(unittest.TestCase):
    def test_failed_git_status_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "home"
            hermes = root / "hermes"
            fake_bin = root / "bin"
            install_dir = hermes / "plugins" / "discord-voice"
            python_bin = hermes / "hermes-agent" / "venv" / "bin" / "python"

            home.mkdir()
            fake_bin.mkdir()
            install_dir.mkdir(parents=True)
            (install_dir / ".git").mkdir()
            python_bin.parent.mkdir(parents=True)
            python_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            python_bin.chmod(0o755)

            git = fake_bin / "git"
            git.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "${1:-}" = "-C" ]; then\n'
                '  shift 2\n'
                '  if [ "${1:-}" = "rev-parse" ] && [ "${2:-}" = "--is-inside-work-tree" ]; then\n'
                '    echo true\n'
                '    exit 0\n'
                '  fi\n'
                '  if [ "${1:-}" = "rev-parse" ] && [ "${2:-}" = "HEAD" ]; then\n'
                f"    echo {REVISION}\n"
                '    exit 0\n'
                '  fi\n'
                '  if [ "${1:-}" = "status" ]; then\n'
                '    exit 23\n'
                '  fi\n'
                'fi\n'
                'exit 1\n',
                encoding="utf-8",
            )
            git.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "HERMES_HOME": str(hermes),
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                }
            )
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
            self.assertIn(f"revision: {REVISION}", result.stdout)
            self.assertIn("worktree: unavailable", result.stdout)
            self.assertNotIn("worktree: clean", result.stdout)

    def test_existing_install_is_reported_before_missing_python_preflight(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "home"
            hermes = root / "hermes"
            fake_bin = root / "bin"
            install_dir = hermes / "plugins" / "discord-voice"

            home.mkdir()
            fake_bin.mkdir()
            install_dir.mkdir(parents=True)
            (install_dir / ".git").mkdir()

            git = fake_bin / "git"
            git.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "${1:-}" = "-C" ]; then\n'
                '  shift 2\n'
                '  if [ "${1:-}" = "rev-parse" ] && [ "${2:-}" = "--is-inside-work-tree" ]; then\n'
                '    echo true\n'
                '    exit 0\n'
                '  fi\n'
                '  if [ "${1:-}" = "rev-parse" ] && [ "${2:-}" = "HEAD" ]; then\n'
                f"    echo {REVISION}\n"
                '    exit 0\n'
                '  fi\n'
                '  if [ "${1:-}" = "status" ]; then\n'
                '    exit 0\n'
                '  fi\n'
                'fi\n'
                'exit 1\n',
                encoding="utf-8",
            )
            git.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "HERMES_HOME": str(hermes),
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                }
            )
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
            self.assertIn(f"revision: {REVISION}", result.stdout)
            self.assertIn("worktree: clean", result.stdout)
            self.assertNotIn("Hermes Python venv not found", result.stdout)


if __name__ == "__main__":
    unittest.main()
