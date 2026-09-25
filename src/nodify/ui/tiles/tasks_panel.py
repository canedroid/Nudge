"""The To-Dos tile.

Grouped by schedule window rather than by a flat list, because the plan's
attention model is: what is already late, what is due now, what is coming.
Everything mutates through the :class:`TaskRepository` protocol, so the panel has
no filesystem knowledge and can be driven with a fake.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TypeVar

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nodify.domain.documents import Schedule, Task
from nodify.domain.ports import TaskRepository
from nodify.styles import app_qss

_T = TypeVar("_T")


class _Failure:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<repository call failed>"


FAILED = _Failure()

#: How far ahead a task still counts as "due" rather than merely upcoming. A task
#: due in ten minutes belongs in the DUE group, not three days out in COMING UP.
_ATTENTION_WINDOW = timedelta(hours=1)

#: How far ahead the COMING UP group reaches.
_UPCOMING_DAYS = 7

SCHEDULE_ORDER = (Schedule.NOW, Schedule.SOON, Schedule.WEEK)

SCHEDULE_LABELS = {
    Schedule.NOW: "Now",
    Schedule.SOON: "Soon",
    Schedule.WEEK: "Week",
}


class TasksPanel(QWidget):
    """Task creation, completion and browsing for the overlay."""

    tasks_changed = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        repository: TaskRepository,
        now: Callable[[], datetime],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tile")
        self._repository = repository
        self._now = now
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        self.setStyleSheet(app_qss.tile_qss() + app_qss.notes_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        title = QLabel("TO-DOS")
        title.setObjectName("tileTitle")
        outer.addWidget(title)

        outer.addWidget(self._build_composer())
        outer.addWidget(self._build_list(), 1)
        outer.addLayout(self._build_actions())

        self._status = QLabel("")
        self._status.setObjectName("tileHint")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

    def _build_composer(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("What needs doing?")
        self._title_input.returnPressed.connect(self.add_task)
        layout.addWidget(self._title_input, 1)

        self._schedule_input = QComboBox()
        for window in SCHEDULE_ORDER:
            self._schedule_input.addItem(SCHEDULE_LABELS[window], window)
        self._schedule_input.setFixedWidth(74)
        layout.addWidget(self._schedule_input)

        return container

    def _build_list(self) -> QWidget:
        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_item_changed)
        return self._list

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._add_button = QPushButton("ADD")
        self._add_button.setObjectName("primary")
        self._add_button.clicked.connect(self.add_task)

        self._complete_button = QPushButton("DONE")
        self._complete_button.clicked.connect(self.complete_selected)

        self._reopen_button = QPushButton("REOPEN")
        self._reopen_button.clicked.connect(self.reopen_selected)

        self._delete_button = QPushButton("DELETE")
        self._delete_button.setObjectName("danger")
        self._delete_button.clicked.connect(self.delete_selected)

        for button in (
            self._add_button,
            self._complete_button,
            self._reopen_button,
            self._delete_button,
        ):
            row.addWidget(button)
        return row

    def _call(self, action: Callable[[], _T], message: str) -> _T | _Failure:
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - a panel must not crash
            self.error_occurred.emit(f"{message}: {exc}")
            return FAILED

    # --------------------------------------------------------------- display

    def reload(self) -> None:
        """Rebuild the list from the repository."""
        moment = self._now()

        overdue = self._call(lambda: self._repository.overdue(now=moment), "Cannot read tasks")
        upcoming = self._call(
            lambda: self._repository.upcoming(now=moment, days=_UPCOMING_DAYS),
            "Cannot read tasks",
        )
        open_tasks = self._call(lambda: self._repository.open_tasks(), "Cannot read tasks")
        if isinstance(overdue, _Failure) or isinstance(upcoming, _Failure):
            return
        if isinstance(open_tasks, _Failure):
            return

        # A task that is both overdue and inside the upcoming horizon must appear
        # once, in the overdue group.
        overdue_ids = {task.id for task in overdue}

        # Completed tasks are shown from the full closed set, because ``overdue``
        # and ``upcoming`` are both open-task views by design. Without this a
        # finished task would disappear from the tile the moment it was ticked,
        # which reads as data loss.
        done = self._completed(moment)
        done_ids = {task.id for task in done}

        remaining = [
            task for task in upcoming if task.id not in overdue_ids and task.id not in done_ids
        ]

        self._list.blockSignals(True)
        self._list.clear()
        self._populate_group("OVERDUE", overdue, moment)
        self._populate_group("DUE", self._due_now(open_tasks, moment), moment)
        self._populate_group("COMING UP", remaining, moment)
        self._populate_group("DONE", done, moment)
        self._list.blockSignals(False)

        if self._list.count() == 0:
            self._status.setText("Nothing scheduled. Add a task above.")

    def _completed(self, moment: datetime) -> list[Task]:
        """Tasks completed up to ``moment``, most recent first."""
        result = self._call(lambda: self._repository.completed(None), "Cannot read tasks")
        if isinstance(result, _Failure):
            return []
        visible = [
            task for task in result if task.completed_at is None or task.completed_at <= moment
        ]
        visible.sort(
            key=lambda task: (task.completed_at or datetime.min.replace(tzinfo=UTC), task.id),
            reverse=True,
        )
        return visible

    def _due_now(self, open_tasks: list[Task], moment: datetime) -> list[Task]:
        from nodify.domain.schedule import is_overdue

        return [
            task
            for task in open_tasks
            if task.due_at is not None
            and not is_overdue(task.due_at, now=moment)
            and (task.due_at - moment) <= _ATTENTION_WINDOW
        ]

    def _populate_group(self, label: str, tasks: list[Task], moment: datetime) -> None:
        if not tasks:
            return
        header = QListWidgetItem(label)
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        header.setForeground(Qt.GlobalColor.gray)
        self._list.addItem(header)

        for task in tasks:
            item = QListWidgetItem(self._label_for(task, moment))
            item.setData(Qt.ItemDataRole.UserRole, task.id)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if task.is_done else Qt.CheckState.Unchecked)
            self._list.addItem(item)

    @staticmethod
    def _label_for(task: Task, moment: datetime) -> str:
        from nodify.domain.schedule import seconds_remaining

        remaining = seconds_remaining(task.due_at, now=moment)
        if task.is_done:
            return f"{task.title}"
        if remaining is None:
            return task.title
        if remaining < 0:
            overdue_by = abs(remaining)
            if overdue_by < 3600:
                return f"{task.title}  ({overdue_by // 60}m late)"
            if overdue_by < 86400:
                return f"{task.title}  ({overdue_by // 3600}h late)"
            return f"{task.title}  ({overdue_by // 86400}d late)"
        if remaining < 3600:
            return f"{task.title}  (in {max(remaining // 60, 1)}m)"
        if remaining < 86400:
            return f"{task.title}  (in {remaining // 3600}h)"
        return f"{task.title}  (in {remaining // 86400}d)"

    # ------------------------------------------------------------ operations

    def _selected_task_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else None

    def add_task(self) -> bool:
        """Create a task from the composer."""
        title = self._title_input.text().strip()
        if not title:
            self.error_occurred.emit("A task needs a title.")
            return False

        schedule = self._schedule_input.currentData()
        if not isinstance(schedule, Schedule):
            schedule = Schedule.NOW

        moment = self._now()
        created = self._call(
            lambda: self._repository.create(moment, title, schedule=schedule),
            "Cannot add the task",
        )
        if isinstance(created, _Failure):
            return False

        self._title_input.clear()
        self.reload()
        self._select(created.id)
        self._status.setText("Added.")
        self.tasks_changed.emit()
        return True

    def _select(self, task_id: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == task_id:
                self._list.setCurrentItem(item)
                return

    def complete_selected(self) -> bool:
        """Complete the selected task, or every ticked task if nothing is selected."""
        targets = self._targets_for_toggle()
        if not targets:
            return False

        moment = self._now()
        changed = False
        for task_id in targets:
            outcome = self._call(
                partial(self._repository.complete, task_id, moment),
                "Cannot complete the task",
            )
            changed = changed or not isinstance(outcome, _Failure)

        if changed:
            self.reload()
            self._status.setText("Completed.")
            self.tasks_changed.emit()
        return changed

    def reopen_selected(self) -> bool:
        targets = self._targets_for_toggle()
        if not targets:
            return False

        changed = False
        for task_id in targets:
            outcome = self._call(
                partial(self._repository.reopen, task_id), "Cannot reopen the task"
            )
            changed = changed or not isinstance(outcome, _Failure)

        if changed:
            self.reload()
            self._status.setText("Reopened.")
            self.tasks_changed.emit()
        return changed

    def _targets_for_toggle(self) -> list[str]:
        """The ticked tasks, or the selected one.

        Ticking a checkbox is the natural gesture, so a tick wins over a
        selection. If nothing is ticked, the highlighted row is used instead, so
        the DONE button is not dead when the list has no ticks.
        """
        ticked: list[str] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None or item.checkState() == Qt.CheckState.Unchecked:
                continue
            value = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(value, str):
                ticked.append(value)
        if ticked:
            return ticked

        selected = self._selected_task_id()
        return [selected] if selected else []

    def delete_selected(self) -> bool:
        task_id = self._selected_task_id()
        if task_id is None:
            return False

        outcome = self._call(lambda: self._repository.delete(task_id), "Cannot delete the task")
        if isinstance(outcome, _Failure):
            return False

        self.reload()
        self._status.setText("Deleted.")
        self.tasks_changed.emit()
        return True

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """A tick in the list completes or reopens that task."""
        task_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(task_id, str):
            return  # a group header

        moment = self._now()
        if item.checkState() == Qt.CheckState.Checked:
            self._call(
                lambda: self._repository.complete(task_id, moment), "Cannot complete the task"
            )
        else:
            self._call(lambda: self._repository.reopen(task_id), "Cannot reopen the task")
        self.reload()
        self.tasks_changed.emit()
