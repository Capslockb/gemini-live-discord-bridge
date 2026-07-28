import os
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
REVISION = "2222222222222222222222222222222222222222"


class FromLocalConflictReportingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.hermes = self.root / "hermes"
        self.source = self.root / "source"
        self.fake_bin = self.root / "bin"
        for path in (self.home, self.source, self.fake_bin):
            path.mkdir(parents=True, exist_ok=True)
        (self.source / "plugin.yaml").write_text("name: discord-voice\n", encoding="utf-8")

        git = self.fake_bin / "git"
        git.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "${1:-}" = "-C" ]; then\n'
            '  dir="$2"; shift 2\n'
            '  if [ "${1:-}" = "rev-parse" ] && [ "${2:-}" = "--is-inside-work-tree" ]; then\n'
            '    [ -d "$dir/.git" ] && { echo true; exit 0; }\n'
            '    exit 1\n'
            '  fi\n'
            '  if [ "${1:-}" = "rev-parse" ] && [ "${2:-}" = "HEAD" ]; then\n'
            f"    echo {REVISION}; exit 0\n"
            '  fi\n'
            '  if [ "${1:-}" = "status" ]; then\n'
            '    [ -f "$dir/.dirty" ] && echo " M tracked-file"\n'
            '    exit 0\n'
            '  fi\n'
            'fi\n'
            'exit 1\n',
            encoding="utf-8",
        )
        git.chmod(0o755)

        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.home),
                "HERMES_HOME": str(self.hermes),
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
            }
        )

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

    def populate_git_install(self, *, dirty=False):
        self.install_dir.mkdir(parents=True)
        (self.install_dir / ".git").mkdir()
        marker = self.install_dir / "preserve.me"
        marker.write_text("keep\n", encoding="utf-8")
        if dirty:
            (self.install_dir / ".dirty").write_text("yes\n", encoding="utf-8")
        return marker

    def test_from_local_conflicting_clean_directory_reports_revision_and_state(self):
        marker = self.populate_git_install(dirty=False)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("existing directory (refused)", result.stdout)
        self.assertIn(f"revision: {REVISION}", result.stdout)
        self.assertIn("worktree: clean", result.stdout)
        self.assertIn("Refusing to delete or overwrite it", result.stdout)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_from_local_conflicting_modified_directory_reports_modified(self):
        marker = self.populate_git_install(dirty=True)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("existing directory (refused)", result.stdout)
        self.assertIn(f"revision: {REVISION}", result.stdout)
        self.assertIn("worktree: modified", result.stdout)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_from_local_conflicting_regular_file_is_classified_and_preserved(self):
        self.install_dir.parent.mkdir(parents=True)
        self.install_dir.write_text("preserve\n", encoding="utf-8")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("existing file (refused)", result.stdout)
        self.assertIn("revision: not-applicable", result.stdout)
        self.assertIn("worktree: not-applicable", result.stdout)
        self.assertIn("Refusing to delete or overwrite it", result.stdout)
        self.assertEqual(self.install_dir.read_text(encoding="utf-8"), "preserve\n")

    def test_from_local_mismatched_symlink_reports_target_revision_and_preserves_link(self):
        target = self.root / "other-checkout"
        target.mkdir(parents=True)
        (target / ".git").mkdir()
        marker = target / "preserve.me"
        marker.write_text("keep\n", encoding="utf-8")
        self.install_dir.parent.mkdir(parents=True)
        self.install_dir.symlink_to(target, target_is_directory=True)

        result = self.run_installer()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("existing symlink (refused)", result.stdout)
        self.assertIn(f"target: {target}", result.stdout)
        self.assertIn(f"revision: {REVISION}", result.stdout)
        self.assertIn("worktree: clean", result.stdout)
        self.assertIn("Refusing to replace it", result.stdout)
        self.assertTrue(self.install_dir.is_symlink())
        self.assertEqual(self.install_dir.resolve(), target)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_from_local_dangling_symlink_reports_controlled_unavailable_state(self):
        missing_target = self.root / "missing-checkout"
        self.install_dir.parent.mkdir(parents=True)
        self.install_dir.symlink_to(missing_target, target_is_directory=True)

        result = self.run_installer()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("existing symlink (refused)", result.stdout)
        self.assertIn(f"target: {missing_target}", result.stdout)
        self.assertIn("revision: unavailable", result.stdout)
        self.assertIn("worktree: unavailable", result.stdout)
        self.assertIn("Refusing to replace it", result.stdout)
        self.assertTrue(self.install_dir.is_symlink())
        self.assertEqual(os.readlink(self.install_dir), str(missing_target))


if __name__ == "__main__":
    unittest.main()
