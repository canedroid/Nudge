"""Task persistence.

A task lives inside its day's file, so every mutation is a read, modify, write of
one file. That is why :class:`DayFile` detaches its records: editing one task must
not rewrite the others.

Scheduling is a coarse window, not a promise. ``now``, ``soon`` and ``week`` pick a
representative due time, but an explicitly chosen ``due_at`` always wins, because
a user who set a date means it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from nodify.adapters.day_file import DayFile, normalise_day
from nodify.domain.clock import Clock, SystemClock
from nodify.domain.documents import (
    Schedule,
    Task,
    TaskStatus,
)
from nodify.domain.ports import DocumentNotFoundError, VaultError
from nodify.domain.schedule import WEEK, is_overdue, resolve_due_at


class MarkdownDocumentStore(Protocol):
    """The part of the document store a repository needs."""

    def read(self, path: Path) -> tuple[dict[str, Any], str]: ...

    def write(self, path: Path, frontmatter: Any, body: str) -> None: ...


class VaultLike(Protocol):
    """The part of a :class:`~nodify.adapters.vault.Vault` a repository needs.

    Declared structurally rather than imported, so the adapters do not import each
    other and the repository stays testable with any conforming object.
    """

    @property
    def root(self) -> Path: ...

    @property
    def documents(self) -> MarkdownDocumentStore: ...


ID_PREFIX_TASK = "task_"
_ID_COUNTER_MAX = 0xFFFFFFFF

#: How far ahead :meth:`MarkdownTaskRepository.upcoming` looks by default.
DEFAULT_UPCOMING_DAYS = 7


class _IdGenerator:
    def __init__(self) -> None:
        self._counter = 0

    def next_id(self, moment: datetime) -> str:
        self._counter = (self._counter + 1) % _ID_COUNTER_MAX
        return f"{ID_PREFIX_TASK}{int(moment.timestamp()):010d}{self._counter:08x}"


class MarkdownTaskRepository:
    """A :class:`TaskRepository` backed by day files on disk."""

    def __init__(self, vault: VaultLike, clock: Clock | None = None) -> None:
        self._vault = vault
        self._ids = _IdGenerator()
        self._clock: Clock = clock or SystemClock()

    def now(self) -> datetime:
        return self._clock.now()

    def set_clock(self, clock: Clock) -> None:
        """Replace the clock, for a caller that drives time explicitly.

        A panel previewing "what if this were tomorrow" needs to evaluate the
        schedule views at an arbitrary instant without waiting for it.
        """
        self._clock = clock

    @property
    def _root(self) -> Path:
        return self._vault.root

    @property
    def _documents(self) -> MarkdownDocumentStore:
        return self._vault.documents

    def _load(self, day: datetime) -> DayFile:
        return DayFile.load(self._documents, self._root, day)

    # ------------------------------------------------------------------ read

    def get(self, task_id: str) -> Task:
        """Find a task by id across every day file."""
        for day_file in self._day_files():
            if not day_file.has(task_id):
                continue
            for task in day_file.tasks():
                if task.id == task_id:
                    return task
        raise DocumentNotFoundError(f"no task with id {task_id!r}")

    def _day_files(self) -> list[DayFile]:
        """Every day file in the vault, in chronological order."""
        todos_root = self._root / "todos"
        if not todos_root.is_dir():
            return []
        loaded: list[DayFile] = []
        for month_dir in sorted(todos_root.iterdir(), key=lambda p: p.name):
            if not month_dir.is_dir():
                continue
            for day_path in sorted(month_dir.glob("*.md")):
                day = _day_from_name(day_path.name, month_dir.name)
                if day is None:
                    continue
                try:
                    loaded.append(DayFile.load(self._documents, self._root, day))
                except VaultError:
                    # A corrupt day file must not hide every other day.
                    continue
        return loaded

    def list_for_day(self, day: datetime) -> list[Task]:
        return self._load(day).tasks()

    def all_tasks(self) -> list[Task]:
        """Every task in the vault, oldest day first."""
        tasks: list[Task] = []
        for day_file in self._day_files():
            tasks.extend(day_file.tasks())
        return tasks

    # ----------------------------------------------------------------- views

    def open_tasks(self) -> list[Task]:
        return [task for task in self.all_tasks() if not task.is_done]

    def completed(self, day: datetime | None = None) -> list[Task]:
        source = self.list_for_day(day) if day is not None else self.all_tasks()
        return [task for task in source if task.is_done]

    def upcoming(self, *, now: datetime, days: int = DEFAULT_UPCOMING_DAYS) -> list[Task]:
        """Open tasks due within the next ``days``, soonest first.

        Undated open tasks are included: an undated task is due now, and hiding it
        would make it unreachable.
        """
        horizon = now + timedelta(days=days)
        selected = [
            task for task in self.open_tasks() if task.due_at is None or task.due_at <= horizon
        ]
        return sorted(selected, key=self._due_key)

    def overdue(self, *, now: datetime) -> list[Task]:
        """Open tasks whose due time has passed, most overdue first.

        Most overdue means the earliest due time, so a task that slipped by days
        sorts ahead of one that is only a minute late.
        """
        selected = [task for task in self.open_tasks() if is_overdue(task.due_at, now=now)]
        return sorted(selected, key=self._due_key)

    def needs_attention(self, *, now: datetime) -> list[Task]:
        """Tasks that want the user to act now: overdue first, then due now.

        This is the "immediate attention" view, and it is deliberately narrow. A
        task due in three hours is not something to interrupt anyone for. Only a
        task that is already late, or due at this very instant, appears; widening
        it to everything not overdue would put an ordinary day's tasks on screen
        at once, which is the opposite of useful.
        """
        attention = [
            task for task in self.open_tasks() if task.due_at is not None and task.due_at <= now
        ]
        # Earliest due first, which puts the longest-overdue task at the top and
        # orders tasks due at this instant after everything that is already late.
        return sorted(attention, key=self._due_key)

    @staticmethod
    def _due_key(task: Task) -> tuple[int, datetime, str]:
        """Sort undated tasks last, then by due time, then by id."""
        if task.due_at is None:
            return (1, datetime.max.replace(tzinfo=UTC), task.id)
        return (0, task.due_at, task.id)

    # ----------------------------------------------------------------- write

    def create(
        self,
        day: datetime,
        title: str,
        *,
        notes: str = "",
        schedule: Schedule = Schedule.NOW,
        due_at: datetime | None = None,
        priority: str = "normal",
        tags: list[str] | None = None,
    ) -> Task:
        """Create a task in the given day's file."""
        cleaned = title.strip()
        if not cleaned:
            raise VaultError("a task needs a title")

        day_file = self._load(day)
        created = self.now()

        # Validate before building the record. Letting a naive datetime reach the
        # serialiser raises a bare ValueError from deep inside the mapper, rather
        # than a domain error the caller can act on.
        explicit = _as_aware(due_at) if due_at is not None else None

        task = Task(
            id=self._ids.next_id(created),
            title=cleaned,
            body="",
            created_at=created,
            updated_at=created,
            tags=tags or [],
            notes=notes,
            status=TaskStatus.OPEN,
            priority=priority,
            schedule=schedule,
            due_at=resolve_due_at(schedule, now=created, explicit=explicit),
        )
        day_file.add(DayFile.record_for(task))
        day_file.save(self._documents)
        return task

    def _mutate(self, task_id: str, change: object) -> Task:
        """Apply ``change`` to a task and rewrite its day file.

        The day file is reloaded, changed and saved as one operation so two
        panels editing different days cannot interleave into a lost update, which
        is the failure mode of a bare read-modify-write against a shared file.
        """
        for day_file in self._day_files():
            if not day_file.has(task_id):
                continue

            tasks = {task.id: task for task in day_file.tasks()}
            task = tasks[task_id]
            change(task)  # type: ignore[operator]
            task.updated_at = self.now()

            # ``put`` rather than remove-then-add, so an edited task keeps its
            # position in the day rather than jumping to the bottom.
            day_file.put(DayFile.record_for(task))
            day_file.save(self._documents)
            return task

        raise DocumentNotFoundError(f"no task with id {task_id!r}")

    def update(self, task_id: str, **changes: object) -> Task:
        """Edit a task's fields."""

        def apply(task: Task) -> None:
            for key, value in changes.items():
                if key == "title" and not str(value).strip():
                    raise VaultError("a task needs a title")
                if key == "due_at" and value is not None:
                    value = _as_aware(value)
                if key == "schedule" and value is not None:
                    value = Schedule(str(value))
                if key == "status" and value is not None:
                    value = TaskStatus(str(value))
                if hasattr(task, key):
                    setattr(task, key, value)

        return self._mutate(task_id, apply)

    def complete(self, task_id: str, at: datetime) -> Task:
        """Mark a task done and record when.

        A task completed twice in the same day keeps both entries. The history is
        a list precisely so that "done, undone, done again" is representable
        without lying about the first completion.
        """
        moment = _as_aware(at)

        def apply(task: Task) -> None:
            task.status = TaskStatus.DONE
            task.completed_at = moment
            if moment not in task.completion_history:
                task.completion_history.append(moment)

        return self._mutate(task_id, apply)

    def reopen(self, task_id: str) -> Task:
        """Return a completed task to open.

        ``completed_at`` is cleared but the history is kept, so the record of
        having been finished once is not lost.
        """

        def apply(task: Task) -> None:
            task.status = TaskStatus.OPEN
            task.completed_at = None

        return self._mutate(task_id, apply)

    def delete(self, task_id: str) -> None:
        """Remove a task from its day file."""
        for day_file in self._day_files():
            if day_file.has(task_id):
                day_file.remove(task_id)
                day_file.save(self._documents)
                return
        raise DocumentNotFoundError(f"no task with id {task_id!r}")

    def move(self, task_id: str, day: datetime) -> Task:
        """Move a task to a different day file.

        Used when a task is deferred. The id, history and notes travel with it.
        """
        for day_file in self._day_files():
            if not day_file.has(task_id):
                continue
            task = next((t for t in day_file.tasks() if t.id == task_id), None)
            if task is None:
                break

            target = self._load(day)
            if target.relative_path != day_file.relative_path:
                target.add(DayFile.record_for(task))
                target.save(self._documents)
                day_file.remove(task_id)
                day_file.save(self._documents)
            return task

        raise DocumentNotFoundError(f"no task with id {task_id!r}")


def _as_aware(value: object) -> datetime:
    """Require a timezone-aware datetime.

    A naive due time is a bug waiting to happen: it would be interpreted in the
    machine's local zone, so the same vault would mean different things in two
    time zones.
    """
    if not isinstance(value, datetime):
        raise VaultError("expected a datetime")
    if value.tzinfo is None:
        raise VaultError("a task time must be timezone-aware")
    return value.astimezone(UTC)


def _day_from_name(file_name: str, folder_name: str) -> datetime | None:
    """Parse ``25-2026.md`` inside ``2026-09`` back into a date.

    Returns ``None`` for any file that does not follow the convention, so a
    stray note dropped into ``todos/`` is ignored rather than guessed at.
    """
    if not file_name.endswith(".md"):
        return None
    try:
        year_text, month_text = folder_name.split("-")
        day_text, file_year_text = file_name[:-3].split("-")
        year, month, day = int(file_year_text), int(month_text), int(day_text)
    except ValueError:
        return None

    if len(year_text) != 4 or year_text != file_year_text:
        # The year in the folder and in the file name must agree, otherwise the
        # path does not describe a single day.
        return None

    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


__all__ = [
    "DEFAULT_UPCOMING_DAYS",
    "ID_PREFIX_TASK",
    "MarkdownTaskRepository",
    "WEEK",
    "normalise_day",
]
