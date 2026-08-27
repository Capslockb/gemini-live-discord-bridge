from __future__ import annotations

import threading

from sfx import _ACTIVE_SOURCES, _forget, pick_active_source, register_active_source


class OutputSource:
    pass


def test_active_source_replacement_does_not_deadlock() -> None:
    replacement = OutputSource()

    def replace_source() -> None:
        register_active_source("same-session", OutputSource())
        register_active_source("same-session", replacement)

    worker = threading.Thread(target=replace_source, daemon=True)
    worker.start()
    worker.join(timeout=0.5)

    assert not worker.is_alive(), "replacing an active source deadlocked"
    assert pick_active_source() is replacement


def test_expired_old_source_does_not_remove_replacement() -> None:
    old_source = OutputSource()
    replacement = OutputSource()
    register_active_source("generation-session", old_source)
    expired_reference = _ACTIVE_SOURCES["generation-session"]
    register_active_source("generation-session", replacement)

    _forget("generation-session", expired_reference)

    assert pick_active_source() is replacement
