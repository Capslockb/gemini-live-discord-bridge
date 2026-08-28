import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import notification


class ScheduledNotificationStorageTests(unittest.TestCase):
    def test_default_path_uses_default_hermes_home(self) -> None:
        home = Path("/example/home")

        resolved = notification.resolve_scheduled_path(environ={}, home=home)

        self.assertEqual(
            resolved,
            home / ".hermes" / notification.SCHEDULED_FILENAME,
        )

    def test_hermes_home_relocates_schedule_store(self) -> None:
        hermes_home = Path("/srv/hermes")

        resolved = notification.resolve_scheduled_path(
            environ={"HERMES_HOME": str(hermes_home)},
            home=Path("/unused"),
        )

        self.assertEqual(resolved, hermes_home / notification.SCHEDULED_FILENAME)

    def test_explicit_file_overrides_hermes_home(self) -> None:
        explicit = Path("/var/lib/hermes/scheduled.jsonl")

        resolved = notification.resolve_scheduled_path(
            environ={
                "HERMES_HOME": "/srv/hermes",
                notification.SCHEDULED_PATH_ENV: str(explicit),
            },
            home=Path("/unused"),
        )

        self.assertEqual(resolved, explicit)

    def test_dual_store_conflict_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "custom" / notification.SCHEDULED_FILENAME
            legacy = root / "legacy" / notification.SCHEDULED_FILENAME
            selected.parent.mkdir(parents=True)
            legacy.parent.mkdir(parents=True)
            selected.write_text("{}\n", encoding="utf-8")
            legacy.write_text("{}\n", encoding="utf-8")

            with patch.object(notification, "SCHEDULED_PATH", selected), patch.object(
                notification, "LEGACY_SCHEDULED_PATH", legacy
            ):
                with self.assertRaises(notification.ScheduledStorageConflictError):
                    notification._checked_scheduled_path()

            self.assertTrue(selected.exists())
            self.assertTrue(legacy.exists())

    def test_legacy_store_blocks_creation_at_a_new_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "custom" / notification.SCHEDULED_FILENAME
            legacy = root / "legacy" / notification.SCHEDULED_FILENAME
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"id":"legacy"}\n', encoding="utf-8")

            with patch.object(notification, "SCHEDULED_PATH", selected), patch.object(
                notification, "LEGACY_SCHEDULED_PATH", legacy
            ):
                with self.assertRaises(notification.ScheduledStorageConflictError):
                    notification.schedule_notification(
                        fire_at=time.time() + 60,
                        text="do not split the store",
                    )

            self.assertFalse(selected.exists())
            self.assertEqual(
                legacy.read_text(encoding="utf-8"),
                '{"id":"legacy"}\n',
            )

    def test_schedule_list_and_cancel_use_selected_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "custom" / notification.SCHEDULED_FILENAME
            legacy = root / "legacy" / notification.SCHEDULED_FILENAME

            with patch.object(notification, "SCHEDULED_PATH", selected), patch.object(
                notification, "LEGACY_SCHEDULED_PATH", legacy
            ):
                result = notification.schedule_notification(
                    fire_at=time.time() + 60,
                    text="review the migration",
                )
                scheduled = notification.list_scheduled()

                self.assertEqual(result["status"], "scheduled")
                self.assertEqual(len(scheduled), 1)
                self.assertEqual(scheduled[0]["text"], "review the migration")
                self.assertTrue(notification.cancel_scheduled(result["id"]))
                self.assertEqual(notification.list_scheduled(), [])

            self.assertTrue(selected.exists())
            self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
