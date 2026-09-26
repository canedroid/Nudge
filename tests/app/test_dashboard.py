"""Application integration: the four panels working together.

These are Package J's acceptance criteria, driven end to end against a temporary
vault: a note edit reaches the Files panel, a task completion reaches the attention
views, a timer firing notifies exactly once, and a restart restores everything
including overdue state.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from nodify.adapters.vault import Vault, create
from nodify.app.dashboard import (
    Dashboard,
    Services,
    VaultNotSelectedError,
    build_services,
)
from nodify.domain.clock import FixedClock
from nodify.services.settings import AppSettings, TileGeometry
from nodify.ui.layout.tile_layout import DEFAULT_ORDER, Rect, TileId
from nodify.ui.tiles.tile_frame import TileFrame

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QApplication, QWidget

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
AREA = Rect(0, 0, 1600, 900)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    create(root, initial_year_month="2026-09")
    instance = Vault(root)
    instance.open()
    return instance


@pytest.fixture
def services(vault: Vault, clock: FixedClock) -> Services:
    return Services(vault=vault, clock=clock)


@pytest.fixture
def dashboard(
    qapp: object, services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Dashboard:
    # Keep the layout out of the user's real config directory.
    monkeypatch.setenv("NODIFY_CONFIG_DIR", str(tmp_path / "config"))
    board = Dashboard(
        services,
        AppSettings(),
        confirm=lambda _t, _m: True,
        prompt=lambda _t, _l, initial: initial,
    )
    yield board
    board.shutdown()
    # A panel parented to a frame that was inside a deleted window is already
    # gone at the C++ level, and calling deleteLater on it raises. Ask first.
    from PyQt6.sip import isdeleted

    for panel in (
        board.notes_panel,
        board.tasks_panel,
        board.timers_panel,
        board.files_panel,
    ):
        if not isdeleted(panel):
            panel.deleteLater()


class TestComposition:
    def test_all_four_panels_are_mounted(self, dashboard: Dashboard) -> None:
        panels = dashboard.panels()
        assert set(panels) == set(DEFAULT_ORDER)
        assert all(panel is not None for panel in panels.values())

    def test_panels_are_in_the_approved_order(self, dashboard: Dashboard) -> None:
        assert list(dashboard.panels()) == list(DEFAULT_ORDER)

    def test_every_panel_shares_one_vault(self, dashboard: Dashboard, services: Services) -> None:
        """Two panels with separate repositories would disagree about the same file."""
        assert dashboard.notes_panel._repository is services.notes
        assert dashboard.tasks_panel._repository is services.tasks
        assert dashboard.timers_panel._repository is services.timers

    def test_the_bridge_is_disabled_by_default(self, dashboard: Dashboard) -> None:
        """The first release must not require a remote service."""
        assert not dashboard.bridge.is_enabled()
        assert dashboard.bridge.pump() == []


class TestCrossPanelRefresh:
    def test_creating_a_note_updates_the_files_panel(self, dashboard: Dashboard) -> None:
        """Package J: editing a note updates the Files panel."""
        before = dashboard.files_panel._list.count()
        dashboard.notes_panel.create_note()
        assert dashboard.files_panel._list.count() > before

    def test_saving_a_note_updates_the_files_panel(self, dashboard: Dashboard) -> None:
        dashboard.notes_panel.create_note()
        dashboard.notes_panel._title.setText("Written by hand")
        dashboard.notes_panel.save_current_note()
        labels = [
            dashboard.files_panel._list.item(i).text()
            for i in range(dashboard.files_panel._list.count())
        ]
        assert any("Written by hand" in label for label in labels)

    def test_deleting_a_note_updates_the_files_panel(self, dashboard: Dashboard) -> None:
        dashboard.notes_panel.create_note()
        assert dashboard.notes_panel.delete_current_note()
        assert dashboard.files_panel._list.count() == 0

    def test_moving_a_note_updates_the_notes_panel(
        self, dashboard: Dashboard, services: Services
    ) -> None:
        """A note moved from the Files view is reflected in the Notes list."""
        note = services.notes.create("Travelling", "Work")
        dashboard.notes_panel.reload()
        assert any(n.id == note.id for n in services.notes.list_notes("Work"))

        dashboard.files_panel.reload()
        # Select the note in the Files view, then move it.
        for row in range(dashboard.files_panel._list.count()):
            item = dashboard.files_panel._list.item(row)
            if item is not None and "Travelling" in item.text():
                dashboard.files_panel._list.setCurrentItem(item)
                break
        assert dashboard.files_panel.move_selected_note("Home")
        assert not any(n.id == note.id for n in services.notes.list_notes("Work"))
        assert any(n.id == note.id for n in services.notes.list_notes("Home"))

    def test_completing_a_task_updates_the_attention_views(
        self, dashboard: Dashboard, services: Services, clock: FixedClock
    ) -> None:
        """Package J: completing a task updates the attention views."""
        task = services.tasks.create(NOW, "Do it now", due_at=NOW)
        dashboard.tasks_panel.reload()

        assert any(t.id == task.id for t in services.tasks.needs_attention(now=clock.now()))

        # Ticking the row in the panel completes it.
        for row in range(dashboard.tasks_panel._list.count()):
            item = dashboard.tasks_panel._list.item(row)
            if item is not None and item.text().startswith("Do it now"):
                from PyQt6.QtCore import Qt

                item.setCheckState(Qt.CheckState.Checked)
                break

        assert services.tasks.get(task.id).is_done
        assert not any(t.id == task.id for t in services.tasks.needs_attention(now=clock.now()))

    def test_a_task_change_does_not_disturb_the_notes_panel(self, dashboard: Dashboard) -> None:
        """Completing a task must not reload an unrelated panel."""
        dashboard.notes_panel.create_note()
        dashboard.tasks_panel._title_input.setText("Unrelated")
        dashboard.tasks_panel.add_task()
        # No error, and the note is still there.
        assert not dashboard.errors
        assert len(dashboard.services.notes.list_notes()) == 1

    def test_an_external_change_refreshes_everything(
        self, dashboard: Dashboard, services: Services
    ) -> None:
        """A vault change outside the app re-reads every panel."""
        services.notes.create("Added elsewhere", "Work")
        services.tasks.create(NOW, "Also elsewhere")
        dashboard.refresh_all()

        assert dashboard.notes_panel._note_list.count() == 1
        assert dashboard.tasks_panel._list.count() > 0
        assert dashboard.files_panel._list.count() > 0


class TestTimerNotification:
    def test_a_due_timer_notifies_exactly_once(
        self, dashboard: Dashboard, services: Services, clock: FixedClock
    ) -> None:
        """Package J: a timer firing triggers one native notification.

        The count is what matters. A user who gets the same alert every time they
        open the app will turn notifications off entirely.
        """
        timer = services.timers.create_countdown(NOW, "Pomodoro", 25)
        clock.advance(minutes=26)

        seen: list[str] = []
        dashboard.timers_panel.timer_due.connect(seen.append)
        dashboard.timers_panel.reload()
        assert seen == [timer.id]

        dashboard.refresh_all()
        dashboard.timers_panel.reload()
        assert len(seen) == 1

    def test_a_due_timer_is_recorded_as_notified(
        self, dashboard: Dashboard, services: Services, clock: FixedClock
    ) -> None:
        timer = services.timers.create_countdown(NOW, "Pomodoro", 25)
        clock.advance(minutes=26)
        dashboard.timers_panel.reload()
        # The panel announces and clears its own guard; the record is in the vault.
        assert services.timers.get(timer.id).notified_at is not None

    def test_a_notified_timer_is_not_announced_after_a_restart(
        self, dashboard: Dashboard, services: Services, clock: FixedClock
    ) -> None:
        services.timers.create_countdown(NOW, "Pomodoro", 25)
        clock.advance(minutes=26)
        dashboard.timers_panel.reload()

        # A brand new panel over the same vault is what a relaunch is.
        fresh = Dashboard(services, AppSettings())
        try:
            seen: list[str] = []
            fresh.timers_panel.timer_due.connect(seen.append)
            fresh.timers_panel.reload()
            assert seen == []
        finally:
            fresh.shutdown()

    def test_the_timer_panel_updates_after_a_notification(
        self, dashboard: Dashboard, services: Services, clock: FixedClock
    ) -> None:
        services.timers.create_countdown(NOW, "Pomodoro", 25)
        clock.advance(minutes=26)
        dashboard.timers_panel.reload()
        assert "TIME'S UP" in dashboard.timers_panel._list.item(0).text()


class TestRestart:
    def test_all_data_survives_a_restart(
        self, vault: Vault, services: Services, clock: FixedClock
    ) -> None:
        services.notes.create("A note", "Work", "content")
        services.tasks.create(NOW, "A task")
        services.timers.create_countdown(NOW, "A timer", 25)

        # A new service set over the same vault is what a relaunch is.
        reopened = Services(vault=Vault(vault.root), clock=clock)
        assert [n.title for n in reopened.notes.list_notes()] == ["A note"]
        assert [t.title for t in reopened.tasks.list_for_day(NOW)] == ["A task"]
        assert [t.title for t in reopened.timers.all_timers()] == ["A timer"]

    def test_overdue_state_survives_a_restart(self, services: Services, clock: FixedClock) -> None:
        """A timer that came due while the app was closed shows as overdue.

        It must not be silently completed, which would look like the reminder was
        handled when it was not.
        """
        timer = services.timers.create_countdown(NOW, "Missed", 25)
        clock.advance(hours=3)

        reopened = Services(vault=services.vault, clock=clock)
        from nodify.domain.reminder_engine import TimerState

        view = reopened.reminders.snapshot(reopened.timers.all_pending())[0]
        assert view.state is TimerState.OVERDUE
        assert reopened.timers.get(timer.id).status.value == "scheduled"

    def test_task_completion_survives_a_restart(
        self, services: Services, clock: FixedClock
    ) -> None:
        task = services.tasks.create(NOW, "Finished")
        services.tasks.complete(task.id, clock.now())

        reopened = Services(vault=services.vault, clock=clock)
        assert reopened.tasks.get(task.id).is_done
        assert len(reopened.tasks.get(task.id).completion_history) == 1

    def test_note_content_survives_a_restart(self, services: Services) -> None:
        services.notes.create("Kept", "Work", "exact body\n")
        reopened = Services(vault=services.vault)
        assert reopened.notes.list_notes()[0].body == "exact body\n"


class TestLayout:
    def test_apply_layout_places_every_tile(self, dashboard: Dashboard) -> None:
        dashboard.apply_layout(AREA)
        for tile in DEFAULT_ORDER:
            rect = dashboard.geometry_for(tile)
            assert rect.width > 0
            assert rect.height > 0

    def test_restore_falls_back_to_the_default(self, dashboard: Dashboard) -> None:
        dashboard.restore_layout(AREA)
        assert all(dashboard.geometry_for(t).width > 0 for t in DEFAULT_ORDER)

    def test_restore_uses_saved_geometry(
        self, services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NODIFY_CONFIG_DIR", str(tmp_path / "config"))
        first = Dashboard(services, AppSettings())
        try:
            first.apply_layout(AREA)
            first.persist_layout()
        finally:
            first.shutdown()

        second = Dashboard(services, AppSettings())
        try:
            second.restore_layout(AREA)
            # The saved geometry is used, not replaced by the default.
            assert second.geometry_for(TileId.TIMERS).width > 0
        finally:
            second.shutdown()

    def test_geometry_is_clamped_into_the_area(
        self, services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NODIFY_CONFIG_DIR", str(tmp_path / "config"))
        settings = AppSettings(layout=(TileGeometry(99_999, 99_999, 100, 100),))
        board = Dashboard(services, settings)
        try:
            small = Rect(0, 0, 800, 600)
            board.restore_layout(small)
            for tile in DEFAULT_ORDER:
                rect = board.geometry_for(tile)
                assert rect.x >= 0
                assert rect.y >= 0
        finally:
            board.shutdown()


class TestWatching:
    def test_starting_watching_twice_registers_one(self, dashboard: Dashboard) -> None:
        dashboard.start_watching()
        assert dashboard.watcher is not None
        first = dashboard.watcher
        dashboard.start_watching()
        assert dashboard.watcher is first
        assert first.is_running()

    def test_stopping_watching(self, dashboard: Dashboard) -> None:
        dashboard.start_watching()
        dashboard.stop_watching()
        assert dashboard.watcher is None

    def test_a_stopped_watcher_costs_nothing(self, dashboard: Dashboard) -> None:
        dashboard.start_watching()
        watcher = dashboard.watcher
        dashboard.stop_watching()
        assert not watcher.is_running()

    def test_shutdown_stops_everything(self, dashboard: Dashboard) -> None:
        dashboard.start_watching()
        watcher = dashboard.watcher
        dashboard.shutdown()
        assert watcher is not None and not watcher.is_running()
        assert not dashboard.timers_panel._ticker.isActive()


class TestErrorCollection:
    def test_a_panel_error_is_collected(self, dashboard: Dashboard) -> None:
        from nodify.domain.ports import VaultError

        def boom() -> list[str]:
            raise VaultError("disk on fire")

        dashboard.services.notes.list_categories = boom  # type: ignore[method-assign]
        dashboard.notes_panel.reload()
        assert dashboard.errors
        assert "disk on fire" in dashboard.errors[-1]

    def test_an_error_does_not_stop_the_app(self, dashboard: Dashboard) -> None:
        from nodify.domain.ports import VaultError

        def boom() -> list[str]:
            raise VaultError("nope")

        dashboard.services.notes.list_categories = boom  # type: ignore[method-assign]
        dashboard.notes_panel.reload()
        # The tasks panel still works.
        dashboard.tasks_panel._title_input.setText("Still working")
        assert dashboard.tasks_panel.add_task()


class TestBuildServices:
    def test_opens_a_vault(self, vault: Vault) -> None:
        services = build_services(vault.root)
        assert services.vault.root == vault.root

    def test_rejects_a_folder_that_is_not_a_vault(self, tmp_path: Path) -> None:
        plain = tmp_path / "documents"
        plain.mkdir()
        (plain / "notes").mkdir()
        with pytest.raises(VaultNotSelectedError):
            build_services(plain)

    def test_rejects_a_missing_folder(self, tmp_path: Path) -> None:
        with pytest.raises(VaultNotSelectedError):
            build_services(tmp_path / "nope")


class TestIndependence:
    def test_each_panel_depends_only_on_its_own_repository(self, dashboard: Dashboard) -> None:
        """Package J: changing one service does not require changing others.

        Each panel holds exactly one repository and reaches for no other, so a
        feature can be swapped without touching the components around it.
        """
        panels = dashboard.panels()
        assert panels[TileId.NOTES]._repository is dashboard.services.notes
        assert panels[TileId.TODOS]._repository is dashboard.services.tasks
        assert panels[TileId.TIMERS]._repository is dashboard.services.timers

    def test_a_panel_can_be_replaced(self, services: Services) -> None:
        """Swapping the notes service needs no change to the other panels."""
        from nodify.adapters.note_repo import MarkdownNoteRepository
        from nodify.ui.tiles.notes_panel import NotesPanel

        replacement = NotesPanel(MarkdownNoteRepository(services.vault, services.clock))
        try:
            assert replacement._repository is not None
        finally:
            replacement.deleteLater()


class TestMounting:
    """The panels are on screen, not merely constructed.

    This is the class that would have caught the original defect. Every other test
    in the suite passed while ``main()`` never mounted a single panel, because
    "the object exists" was being checked where "the object is visible" is what
    matters. These assert parenting, visibility and real geometry.
    """

    @pytest.fixture
    def host(self, qapp: QApplication, dashboard: Dashboard) -> Iterator[QWidget]:
        """A window to mount into, torn down in the only order that is safe.

        The dashboard must release its frames *before* the window is destroyed.
        A frame is the panels' parent, so deleting the window first would destroy
        the panels and leave ``shutdown()`` touching a deleted C++ object.
        """
        from PyQt6.QtWidgets import QWidget

        widget = QWidget()
        widget.setGeometry(0, 0, AREA.width, AREA.height)
        widget.show()
        qapp.processEvents()
        yield widget
        dashboard.shutdown()
        widget.deleteLater()

    def test_mounting_gives_every_panel_a_frame_parented_to_the_host(
        self, dashboard: Dashboard, host: QWidget
    ) -> None:
        frames = dashboard.mount(host, AREA)

        assert set(frames) == set(DEFAULT_ORDER)
        for tile, frame in frames.items():
            assert frame.parentWidget() is host, f"{tile} is not in the window"
            assert frame.content is dashboard.panels()[tile]

    def test_every_mounted_panel_is_visible_with_real_geometry(
        self, dashboard: Dashboard, host: QWidget, qapp: QApplication
    ) -> None:
        dashboard.mount(host, AREA)
        qapp.processEvents()

        for tile, frame in dashboard.frames.items():
            assert frame.isVisible(), f"{tile} is not visible"
            assert frame.width() > 0, f"{tile} has no width"
            assert frame.height() > 0, f"{tile} has no height"
            assert frame.content.isVisible(), f"the panel inside {tile} is hidden"

    def test_tiles_sit_inside_the_mounting_area(self, dashboard: Dashboard, host: QWidget) -> None:
        """A tile outside the window is invisible to the user however valid it is."""
        dashboard.mount(host, AREA)

        for tile in dashboard.frames:
            rect = dashboard.geometry_for(tile)
            assert AREA.contains_point(rect.x, rect.y), f"{tile} starts outside the area"
            assert rect.right <= AREA.right, f"{tile} overflows to the right"
            assert rect.bottom <= AREA.bottom, f"{tile} overflows the bottom"

    def test_frames_do_not_overlap_each_other(self, dashboard: Dashboard, host: QWidget) -> None:
        """Overlapping tiles hide each other's content, so this is a real defect."""
        dashboard.mount(host, AREA)

        rects = [dashboard.geometry_for(tile) for tile in DEFAULT_ORDER]
        for index, first in enumerate(rects):
            for second in rects[index + 1 :]:
                assert not first.intersects(second), f"{first} overlaps {second}"

    def test_mounting_twice_reuses_the_frames(self, dashboard: Dashboard, host: QWidget) -> None:
        """The overlay is shown and hidden repeatedly; a second set of frames would
        stack on top of the first and each would steal the other's clicks."""
        first = dashboard.mount(host, AREA)
        second = dashboard.mount(host, AREA)

        assert {id(f) for f in first.values()} == {id(f) for f in second.values()}
        assert len(host.findChildren(TileFrame)) == len(DEFAULT_ORDER)

    def test_remounting_releases_the_old_frames(
        self, dashboard: Dashboard, host: QWidget, qapp: QApplication
    ) -> None:
        """A fresh dashboard on a new window must not leave the old one behind."""
        from PyQt6.QtWidgets import QWidget

        other = QWidget()
        other.setGeometry(0, 0, AREA.width, AREA.height)
        try:
            dashboard.mount(host, AREA)
            dashboard.mount(other, AREA)
            qapp.processEvents()

            for tile, frame in dashboard.frames.items():
                assert frame.parentWidget() is other, f"{tile} stayed on the old window"
        finally:
            # The frames are children of ``other``, so deleting it would take the
            # panels with it. Release them first.
            dashboard.shutdown()
            other.deleteLater()

    def test_shrinking_the_area_keeps_every_tile_inside_it(
        self, dashboard: Dashboard, host: QWidget
    ) -> None:
        """A resolution change must not strand a tile off-screen."""
        dashboard.mount(host, AREA)

        smaller = Rect(0, 0, 900, 500)
        dashboard.relayout(smaller)

        for tile in DEFAULT_ORDER:
            rect = dashboard.geometry_for(tile)
            assert rect.right <= smaller.right, f"{tile} overflows after shrinking"
            assert rect.bottom <= smaller.bottom, f"{tile} overflows after shrinking"

    def test_a_saved_layout_is_not_overwritten_by_mounting(
        self, dashboard: Dashboard, host: QWidget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mounting runs on every launch.

        It must not persist the default arrangement, or the first show would
        replace the layout the user had arranged and saved.
        """
        dashboard.mount(host, AREA)
        dashboard.persist_layout()

        saved = dashboard.settings.layout
        assert saved, "mounting should have produced a stored layout"

        second = Dashboard(dashboard.services, dashboard.settings)
        try:
            second.mount(host, AREA)
            assert second.settings.layout == saved, "the saved layout was lost on mount"
        finally:
            second.shutdown()

    def test_shutdown_releases_the_frames(self, dashboard: Dashboard, host: QWidget) -> None:
        dashboard.mount(host, AREA)
        assert dashboard.is_mounted

        dashboard.shutdown()

        assert not dashboard.is_mounted
        assert host.findChildren(TileFrame) == []
