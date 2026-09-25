"""File watching, single-instance detection, and the tray.

The behaviours worth protecting are the ones about *not* doing work: no duplicate
observers, no duplicate threads, and a stopped watcher that really stops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nodify.services.platform import (
    ChangeKind,
    CoalescingWatcher,
    PollingWatcher,
    SingleInstanceGuard,
    TrayAction,
    TrayService,
    VaultChange,
    iter_vault_areas,
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for area in ("notes", "todos", "timer"):
        (root / area).mkdir(parents=True)
    return root


class FakeBackend:
    """A stand-in for the watchdog observer."""

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1


class TestCoalescingWatcher:
    def test_starts_and_stops(self, vault: Path) -> None:
        backend = FakeBackend()
        watcher = CoalescingWatcher(vault, lambda _changes: None, backend=backend)
        watcher.start()
        assert watcher.is_running()
        watcher.stop()
        assert not watcher.is_running()
        assert backend.starts == 1
        assert backend.stops == 1

    def test_starting_twice_registers_one_observer(self, vault: Path) -> None:
        """A repeated startup must not leave a duplicate observer running."""
        backend = FakeBackend()
        watcher = CoalescingWatcher(vault, lambda _c: None, backend=backend)
        watcher.start()
        watcher.start()
        assert backend.starts == 1
        watcher.stop()

    def test_stopping_twice_is_safe(self, vault: Path) -> None:
        backend = FakeBackend()
        watcher = CoalescingWatcher(vault, lambda _c: None, backend=backend)
        watcher.start()
        watcher.stop()
        watcher.stop()
        assert backend.stops == 1

    def test_restarting_after_a_stop_works(self, vault: Path) -> None:
        backend = FakeBackend()
        watcher = CoalescingWatcher(vault, lambda _c: None, backend=backend)
        watcher.start()
        watcher.stop()
        watcher.start()
        assert backend.starts == 2
        watcher.stop()

    def test_a_change_while_running_is_delivered(self, vault: Path) -> None:
        seen: list[list[VaultChange]] = []
        watcher = CoalescingWatcher(vault, seen.append, backend=FakeBackend())
        watcher.start()
        watcher.queue_change(VaultChange(ChangeKind.CREATED, vault / "notes" / "a.md"))
        watcher.flush()

        assert len(seen) == 1
        assert seen[0][0].kind is ChangeKind.CREATED
        watcher.stop()

    def test_a_change_while_stopped_is_dropped(self, vault: Path) -> None:
        """A stopped watcher must not do work for changes it is not watching."""
        seen: list[list[VaultChange]] = []
        watcher = CoalescingWatcher(vault, seen.append, backend=FakeBackend())
        watcher.queue_change(VaultChange(ChangeKind.CREATED, vault / "notes" / "a.md"))
        watcher.flush()
        assert seen == []

    def test_a_batch_is_delivered_together(self, vault: Path) -> None:
        """A burst must cause one re-read, not one per file."""
        seen: list[list[VaultChange]] = []
        watcher = CoalescingWatcher(vault, seen.append, backend=FakeBackend())
        watcher.start()
        for index in range(5):
            watcher.queue_change(VaultChange(ChangeKind.MODIFIED, vault / "notes" / f"{index}.md"))
        watcher.flush()

        assert len(seen) == 1
        assert len(seen[0]) == 5
        watcher.stop()

    def test_flushing_with_nothing_pending_does_nothing(self, vault: Path) -> None:
        seen: list[list[VaultChange]] = []
        watcher = CoalescingWatcher(vault, seen.append, backend=FakeBackend())
        watcher.start()
        watcher.flush()
        assert seen == []
        watcher.stop()

    def test_stopping_drops_pending_changes(self, vault: Path) -> None:
        seen: list[list[VaultChange]] = []
        watcher = CoalescingWatcher(vault, seen.append, backend=FakeBackend())
        watcher.start()
        watcher.queue_change(VaultChange(ChangeKind.CREATED, vault / "notes" / "a.md"))
        watcher.stop()
        assert watcher.pending_count == 0
        watcher.flush()
        assert seen == []

    def test_a_backend_batch_reaches_the_callback(self, vault: Path) -> None:
        seen: list[list[VaultChange]] = []
        watcher = CoalescingWatcher(vault, seen.append, backend=FakeBackend())
        watcher.start()
        watcher.flush_backend([VaultChange(ChangeKind.DELETED, vault / "notes" / "gone.md")])
        assert len(seen) == 1
        watcher.stop()

    def test_a_backend_batch_while_stopped_is_dropped(self, vault: Path) -> None:
        seen: list[list[VaultChange]] = []
        watcher = CoalescingWatcher(vault, seen.append, backend=FakeBackend())
        watcher.flush_backend([VaultChange(ChangeKind.DELETED, vault / "notes" / "gone.md")])
        assert seen == []


class TestPollingWatcher:
    def test_detects_a_created_file(self, vault: Path) -> None:
        seen: list[list[VaultChange]] = []
        watcher = PollingWatcher(vault, seen.append)
        watcher.start()
        (vault / "notes" / "a.md").write_text("x", encoding="utf-8")
        changes = watcher.poll()

        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.CREATED
        assert len(seen) == 1
        watcher.stop()

    def test_detects_a_modification(self, vault: Path) -> None:
        path = vault / "notes" / "a.md"
        path.write_text("x", encoding="utf-8")
        watcher = PollingWatcher(vault, lambda _c: None)
        watcher.start()

        import os
        import time

        time.sleep(0.01)
        path.write_text("y" * 100, encoding="utf-8")
        os.utime(path, (0, 0))
        changes = watcher.poll()
        assert any(c.kind is ChangeKind.MODIFIED for c in changes)
        watcher.stop()

    def test_detects_a_deletion(self, vault: Path) -> None:
        path = vault / "notes" / "a.md"
        path.write_text("x", encoding="utf-8")
        watcher = PollingWatcher(vault, lambda _c: None)
        watcher.start()
        path.unlink()

        changes = watcher.poll()
        assert any(c.kind is ChangeKind.DELETED for c in changes)
        watcher.stop()

    def test_nothing_changed(self, vault: Path) -> None:
        watcher = PollingWatcher(vault, lambda _c: None)
        watcher.start()
        assert watcher.poll() == []
        watcher.stop()

    def test_polling_while_stopped_does_nothing(self, vault: Path) -> None:
        """A stopped watcher must not cost a directory walk."""
        seen: list[list[VaultChange]] = []
        watcher = PollingWatcher(vault, seen.append)
        assert watcher.poll() == []
        assert seen == []

    def test_starting_twice_resets_the_baseline(self, vault: Path) -> None:
        (vault / "notes" / "a.md").write_text("x", encoding="utf-8")
        watcher = PollingWatcher(vault, lambda _c: None)
        watcher.start()
        watcher.start()
        assert watcher.poll() == []
        watcher.stop()

    def test_ignores_non_markdown(self, vault: Path) -> None:
        watcher = PollingWatcher(vault, lambda _c: None)
        watcher.start()
        (vault / "notes" / "a.txt").write_text("x", encoding="utf-8")
        assert watcher.poll() == []
        watcher.stop()

    def test_a_missing_area_is_skipped(self, tmp_path: Path) -> None:
        root = tmp_path / "sparse"
        (root / "notes").mkdir(parents=True)
        watcher = PollingWatcher(root, lambda _c: None)
        watcher.start()
        assert watcher.poll() == []
        watcher.stop()


class TestSingleInstanceGuard:
    def test_the_first_claim_succeeds(self) -> None:
        guard = SingleInstanceGuard("NodifyTestPrimary")
        assert guard.acquire()
        assert guard.is_primary
        guard.release()

    def test_a_second_claim_fails(self) -> None:
        """A second process must not open a second fullscreen window."""
        first = SingleInstanceGuard("NodifyTestSecond")
        assert first.acquire()
        try:
            second = SingleInstanceGuard("NodifyTestSecond")
            assert not second.acquire()
            assert not second.is_primary
        finally:
            first.release()

    def test_a_claim_is_released(self) -> None:
        first = SingleInstanceGuard("NodifyTestThird")
        first.acquire()
        first.release()
        second = SingleInstanceGuard("NodifyTestThird")
        assert second.acquire()
        second.release()

    def test_releasing_without_acquiring_is_safe(self) -> None:
        SingleInstanceGuard("NodifyTestFourth").release()

    def test_context_manager(self) -> None:
        with SingleInstanceGuard("NodifyTestContext") as guard:
            assert guard.is_primary
            other = SingleInstanceGuard("NodifyTestContext")
            assert not other.acquire()
        # Released on exit, so a later launch is not blocked by a stale lock.
        later = SingleInstanceGuard("NodifyTestContext")
        assert later.acquire()
        later.release()

    def test_different_keys_do_not_collide(self) -> None:
        first = SingleInstanceGuard("NodifyTestKeyA")
        second = SingleInstanceGuard("NodifyTestKeyB")
        assert first.acquire()
        assert second.acquire()
        first.release()
        second.release()


class TestTrayService:
    def test_offers_exactly_three_actions(self) -> None:
        """Every extra menu item is another way to lose the overlay."""
        tray = TrayService(application=object())
        assert tray.actions() == (
            TrayAction.TOGGLE,
            TrayAction.SETTINGS,
            TrayAction.QUIT,
        )

    def test_toggle_routes_to_the_callback(self) -> None:
        toggled: list[int] = []
        tray = TrayService(application=object(), on_toggle=lambda: toggled.append(1))
        tray.trigger(TrayAction.TOGGLE)
        assert toggled == [1]

    def test_settings_routes_to_the_callback(self) -> None:
        opened: list[int] = []
        tray = TrayService(application=object(), on_settings=lambda: opened.append(1))
        tray.trigger(TrayAction.SETTINGS)
        assert opened == [1]

    def test_quit_routes_to_the_callback(self) -> None:
        quit_called: list[int] = []
        tray = TrayService(application=object(), on_quit=lambda: quit_called.append(1))
        tray.trigger(TrayAction.QUIT)
        assert quit_called == [1]

    def test_show_and_hide(self) -> None:
        tray = TrayService(application=object())
        tray.show()
        assert tray.is_visible()
        tray.hide()
        assert not tray.is_visible()

    def test_notify_without_an_icon_reports_failure(self) -> None:
        tray = TrayService(application=object())
        assert not tray.notify("Sam", "hello")

    def test_notify_with_a_fake_icon(self) -> None:
        class FakeIcon:
            def __init__(self) -> None:
                self.messages: list[tuple[str, str]] = []

            def showMessage(  # noqa: N802 - Qt's own spelling
                self, title: str, body: str, icon: object, msecs: int
            ) -> None:
                self.messages.append((title, body))

        icon = FakeIcon()
        tray = TrayService(application=object(), icon=icon)
        assert tray.notify("Sam", "hello")
        assert icon.messages == [("Sam", "hello")]


class TestVaultAreas:
    def test_lists_the_existing_areas(self, vault: Path) -> None:
        assert [p.name for p in iter_vault_areas(vault)] == ["notes", "todos", "timer"]

    def test_skips_a_missing_area(self, vault: Path) -> None:
        (vault / "timer").rmdir()
        assert [p.name for p in iter_vault_areas(vault)] == ["notes", "todos"]

    def test_a_vault_with_nothing(self, tmp_path: Path) -> None:
        assert list(iter_vault_areas(tmp_path / "empty")) == []


class TestVaultChange:
    def test_describes_itself(self, tmp_path: Path) -> None:
        change = VaultChange(ChangeKind.CREATED, tmp_path / "a.md")
        assert str(change) == "created a.md"
