"""Repository and service contracts.

The plan requires modules to communicate through typed interfaces rather than
reaching into one another's implementations. These are the ports; the concrete
filesystem and platform implementations live in ``adapters`` and ``native``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from nodify.domain.documents import Note, Task, Timer


class VaultError(Exception):
    """Base class for every recoverable vault failure."""


class PathOutsideVaultError(VaultError):
    """A path resolved outside the selected vault."""


class DocumentFormatError(VaultError):
    """A file could not be parsed. The original file is left untouched."""


class CollisionError(VaultError):
    """A write would overwrite an existing file."""


@dataclass(frozen=True, slots=True)
class VaultArea:
    """One of the three fixed top-level vault areas."""

    key: str
    directory: str

    @property
    def path_name(self) -> str:
        return self.directory


NOTES = VaultArea("notes", "notes")
TODOS = VaultArea("todos", "todos")
TIMERS = VaultArea("timer", "timer")
ALL_AREAS: tuple[VaultArea, ...] = (NOTES, TODOS, TIMERS)


class VaultPathResolver(Protocol):
    """Validates paths and maps logical names to safe filesystem paths."""

    @property
    def root(self) -> Path:
        """The selected vault root."""
        ...

    def resolve(self, *parts: str) -> Path:
        """Resolve a path below the vault, or raise :class:`PathOutsideVaultError`."""
        ...

    def ensure_area(self, area: VaultArea) -> Path:
        """Create an area directory if missing and return it."""
        ...

    def area_of(self, path: Path) -> VaultArea | None:
        """Which top-level area a path belongs to, if any."""
        ...


class MarkdownRepository(Protocol):
    """Reads and writes normalised Markdown documents."""

    def read(self, path: Path) -> tuple[dict[str, object], str]:
        """Return the frontmatter mapping and the Markdown body."""
        ...

    def write(self, path: Path, frontmatter: dict[str, object], body: str) -> None:
        """Atomically replace the document at ``path``."""
        ...

    def exists(self, path: Path) -> bool: ...


class NoteRepository(Protocol):
    """Note-specific queries and mutations."""

    def list_categories(self) -> list[str]: ...

    def list_notes(self, category: str | None = None) -> list[Note]: ...

    def get(self, note_id: str) -> Note: ...

    def create(self, title: str, category: str, body: str = "") -> Note: ...

    def update(self, note_id: str, *, title: str | None, body: str | None) -> Note: ...

    def rename(self, note_id: str, new_title: str) -> Note: ...

    def move(self, note_id: str, new_category: str) -> Note: ...

    def delete(self, note_id: str) -> None: ...

    def search(self, query: str) -> list[Note]: ...


class TaskRepository(Protocol):
    """Task-specific queries and mutations."""

    def list_for_day(self, day: datetime) -> list[Task]: ...

    def create(self, day: datetime, title: str, **kwargs: object) -> Task: ...

    def get(self, task_id: str) -> Task: ...

    def update(self, task_id: str, **changes: object) -> Task: ...

    def complete(self, task_id: str, at: datetime) -> Task: ...

    def reopen(self, task_id: str) -> Task: ...

    def delete(self, task_id: str) -> None: ...

    def upcoming(self, *, now: datetime, days: int = 7) -> list[Task]: ...

    def overdue(self, *, now: datetime) -> list[Task]: ...


class TimerRepository(Protocol):
    """Timer and reminder-specific queries and mutations."""

    def list_for_day(self, day: datetime) -> list[Timer]: ...

    def create(self, day: datetime, title: str, **kwargs: object) -> Timer: ...

    def get(self, timer_id: str) -> Timer: ...

    def update(self, timer_id: str, **changes: object) -> Timer: ...

    def complete(self, timer_id: str, at: datetime) -> Timer: ...

    def dismiss(self, timer_id: str) -> Timer: ...

    def mark_notified(self, timer_id: str, at: datetime) -> Timer: ...

    def all_pending(self) -> list[Timer]: ...


class CategoryRepository(Protocol):
    """Category and folder operations for notes."""

    def create(self, name: str) -> str: ...

    def rename(self, old: str, new: str) -> None: ...

    def remove(self, name: str) -> None: ...

    def exists(self, name: str) -> bool: ...


class FileEntry:
    """A regular file below the vault, described without parsing its contents."""

    __slots__ = ("relative_path", "name", "area", "category", "day", "size", "modified_at")

    def __init__(
        self,
        relative_path: Path,
        name: str,
        area: str | None,
        category: str | None,
        day: datetime | None,
        size: int,
        modified_at: datetime,
    ) -> None:
        self.relative_path = relative_path
        self.name = name
        self.area = area
        self.category = category
        self.day = day
        self.size = size
        self.modified_at = modified_at

    def __repr__(self) -> str:
        return f"FileEntry({str(self.relative_path)!r}, area={self.area!r})"


class FileIndex(Protocol):
    """Lists vault files without interpreting their Markdown."""

    def refresh(self) -> list[FileEntry]: ...

    def changes_since(self, previous: Sequence[FileEntry]) -> FileChanges: ...


@dataclass(frozen=True, slots=True)
class FileChanges:
    added: tuple[FileEntry, ...] = ()
    modified: tuple[FileEntry, ...] = ()
    removed: tuple[Path, ...] = ()


class NotificationService(Protocol):
    """Sends native notifications through a replaceable interface."""

    def notify(self, title: str, body: str) -> bool: ...


class GlobalShortcutService(Protocol):
    """Registers and unregisters the application hotkey."""

    def register(self, accelerator: str) -> bool: ...

    def unregister(self) -> None: ...


class PhoneNotificationSource(Protocol):
    """A future-facing input contract with a placeholder implementation."""

    def poll(self) -> Iterator[dict[str, object]]: ...

    def is_enabled(self) -> bool: ...
