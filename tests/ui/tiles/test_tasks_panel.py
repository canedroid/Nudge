"""To-Dos panel behaviour against a real temporary vault."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from nodify.adapters.task_repo import MarkdownTaskRepository
from nodify.adapters.vault import Vault
from nodify.domain.clock import FixedClock
from nodify.domain.documents import Schedule
from nodify.domain.ports import VaultError
from nodify.ui.tiles.tasks_panel import TasksPanel

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def at(**kwargs: float) -> datetime:
    return NOW + timedelta(**kwargs)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def repository(vault: Vault, clock: FixedClock) -> MarkdownTaskRepository:
    return MarkdownTaskRepository(vault, clock)


@pytest.fixture
def panel(qapp: object, repository: MarkdownTaskRepository, clock: FixedClock) -> TasksPanel:
    widget = TasksPanel(repository, clock.now)
    yield widget
    widget.deleteLater()


def tick(panel: TasksPanel, title: str) -> None:
    panel._title_input.setText(title)
    panel.add_task()


def row_for(panel: TasksPanel, title_fragment: str) -> object:
    for row in range(panel._list.count()):
        item = panel._list.item(row)
        if item is not None and title_fragment in item.text():
            return item
    raise AssertionError(f"no row containing {title_fragment!r}")


def group_labels(panel: TasksPanel) -> list[str]:
    return [
        panel._list.item(row).text()
        for row in range(panel._list.count())
        if panel._list.item(row) is not None
        and panel._list.item(row).data(Qt.ItemDataRole.UserRole) is None
    ]


class TestAdd:
    def test_adds_a_task(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        tick(panel, "Send the invoice")
        assert len(repository.list_for_day(NOW)) == 1

    def test_clears_the_input(self, panel: TasksPanel) -> None:
        tick(panel, "Send the invoice")
        assert panel._title_input.text() == ""

    def test_blank_title_is_refused(self, panel: TasksPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.add_task()
        assert errors

    def test_appears_in_the_list(self, panel: TasksPanel) -> None:
        tick(panel, "Send the invoice")
        assert "Send the invoice" in row_for(panel, "Send the invoice").text()

    def test_uses_the_selected_schedule(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        panel._schedule_input.setCurrentIndex(2)
        tick(panel, "Later task")
        task = repository.list_for_day(NOW)[0]
        assert task.schedule is Schedule.WEEK

    def test_repository_failure_is_reported(self, panel: TasksPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)

        def boom(*_args: object, **_kwargs: object) -> object:
            raise VaultError("read only")

        panel._repository.create = boom  # type: ignore[method-assign]
        panel._title_input.setText("Doomed")
        assert not panel.add_task()
        assert errors


class TestGrouping:
    def test_nothing_scheduled(self, panel: TasksPanel) -> None:
        assert "Nothing scheduled" in panel._status.text()

    def test_overdue_group(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        repository.create(NOW, "Late", due_at=at(hours=-2))
        panel.reload()
        assert "OVERDUE" in group_labels(panel)

    def test_due_group_within_the_window(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        repository.create(NOW, "Soon-ish", due_at=at(minutes=20))
        panel.reload()
        assert "DUE" in group_labels(panel)

    def test_coming_up_for_far_off_tasks(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        repository.create(NOW, "Later", due_at=at(days=2))
        panel.reload()
        assert "COMING UP" in group_labels(panel)

    def test_a_task_appears_once(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        """An overdue task is also inside the upcoming horizon; it must not double."""
        repository.create(NOW, "Late", due_at=at(hours=-2))
        panel.reload()
        matches = [
            panel._list.item(row).text()
            for row in range(panel._list.count())
            if panel._list.item(row) is not None and "Late" in panel._list.item(row).text()
        ]
        assert len(matches) == 1

    def test_group_headers_cannot_be_selected(self, panel: TasksPanel) -> None:
        tick(panel, "A task")
        headers = [
            panel._list.item(row)
            for row in range(panel._list.count())
            if panel._list.item(row) is not None
            and panel._list.item(row).data(Qt.ItemDataRole.UserRole) is None
        ]
        if headers:
            assert headers[0].flags() == Qt.ItemFlag.NoItemFlags


class TestRelativeTimes:
    def test_overdue_minutes(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        repository.create(NOW, "Task", due_at=at(minutes=-10))
        panel.reload()
        assert "10m late" in row_for(panel, "Task").text()

    def test_overdue_hours(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        repository.create(NOW, "Task", due_at=at(hours=-3))
        panel.reload()
        assert "3h late" in row_for(panel, "Task").text()

    def test_overdue_days(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        repository.create(NOW, "Task", due_at=at(days=-2))
        panel.reload()
        assert "2d late" in row_for(panel, "Task").text()

    def test_countdown_minutes(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        repository.create(NOW, "Task", due_at=at(minutes=25))
        panel.reload()
        assert "in 25m" in row_for(panel, "Task").text()

    def test_countdown_hours(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        repository.create(NOW, "Task", due_at=at(hours=5))
        panel.reload()
        assert "in 5h" in row_for(panel, "Task").text()

    def test_undated_task_has_no_countdown(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        task = repository.create(NOW, "Task", due_at=at(hours=5))
        repository.update(task.id, due_at=None)
        panel.reload()
        assert row_for(panel, "Task").text() == "Task"


class TestComplete:
    def test_tick_completes(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        tick(panel, "Finish this")
        task_id = repository.list_for_day(NOW)[0].id
        item = row_for(panel, "Finish this")
        item.setCheckState(Qt.CheckState.Checked)
        assert repository.get(task_id).is_done

    def test_untick_reopens(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        tick(panel, "Finish this")
        task_id = repository.list_for_day(NOW)[0].id
        repository.complete(task_id, NOW)
        panel.reload()

        item = row_for(panel, "Finish this")
        assert item.checkState() == Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Unchecked)
        assert not repository.get(task_id).is_done

    def test_done_button_uses_the_selection(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        tick(panel, "Finish this")
        panel._list.setCurrentItem(row_for(panel, "Finish this"))
        assert panel.complete_selected()
        assert repository.list_for_day(NOW)[0].is_done

    def test_done_with_nothing_selected(self, panel: TasksPanel) -> None:
        tick(panel, "Finish this")
        panel._list.setCurrentRow(-1)
        assert not panel.complete_selected()

    def test_completion_persists_across_a_reload(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        tick(panel, "Finish this")
        task_id = repository.list_for_day(NOW)[0].id
        repository.complete(task_id, NOW)
        panel.reload()
        assert row_for(panel, "Finish this").checkState() == Qt.CheckState.Checked

    def test_completed_task_moves_to_done_group(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        task = repository.create(NOW, "Finished", due_at=at(days=1))
        repository.complete(task.id, NOW)
        panel.reload()
        assert "DONE" in group_labels(panel)


class TestReopen:
    def test_reopen_button(self, panel: TasksPanel, repository: MarkdownTaskRepository) -> None:
        task = repository.create(NOW, "Finish this")
        repository.complete(task.id, NOW)
        panel.reload()
        panel._list.setCurrentItem(row_for(panel, "Finish this"))
        assert panel.reopen_selected()
        assert not repository.get(task.id).is_done

    def test_reopen_with_nothing_selected(self, panel: TasksPanel) -> None:
        assert not panel.reopen_selected()


class TestDelete:
    def test_deletes_the_selection(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        tick(panel, "Doomed")
        panel._list.setCurrentItem(row_for(panel, "Doomed"))
        assert panel.delete_selected()
        assert repository.list_for_day(NOW) == []

    def test_delete_with_nothing_selected(self, panel: TasksPanel) -> None:
        assert not panel.delete_selected()

    def test_delete_failure_is_reported(self, panel: TasksPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        tick(panel, "Doomed")
        panel._list.setCurrentItem(row_for(panel, "Doomed"))

        def boom(_task_id: str) -> None:
            raise VaultError("locked")

        panel._repository.delete = boom  # type: ignore[method-assign]
        assert not panel.delete_selected()
        assert errors


class TestRefresh:
    def test_reload_reflects_an_external_change(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        assert panel._list.count() == 0
        repository.create(NOW, "Added elsewhere", due_at=at(hours=2))
        panel.reload()
        assert "Added elsewhere" in row_for(panel, "Added elsewhere").text()

    def test_header_click_is_ignored(
        self, panel: TasksPanel, repository: MarkdownTaskRepository
    ) -> None:
        """Toggling a group header must not touch a task."""
        tick(panel, "A task")
        before = len(repository.list_for_day(NOW))
        for row in range(panel._list.count()):
            item = panel._list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) is None:
                item.setCheckState(Qt.CheckState.Checked)
        assert len(repository.list_for_day(NOW)) == before

    def test_a_broken_day_file_does_not_break_the_panel(
        self, panel: TasksPanel, repository: MarkdownTaskRepository, vault: Vault
    ) -> None:
        repository.create(NOW, "Good", due_at=at(hours=2))
        path = vault.root / "todos" / "2026-09" / "26-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: [unclosed\n---\nbody\n", encoding="utf-8")

        panel.reload()
        assert "Good" in row_for(panel, "Good").text()
