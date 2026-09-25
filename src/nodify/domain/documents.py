"""Document model shared by every vault feature.

These are plain dataclasses with no I/O. The YAML frontmatter keys are defined
here once so that the storage kernel and the feature repositories cannot drift
apart, and so that unknown keys can be preserved verbatim on round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

DOCUMENT_ID_KEY = "id"
DOCUMENT_TYPE_KEY = "type"
TITLE_KEY = "title"
CREATED_AT_KEY = "created_at"
UPDATED_AT_KEY = "updated_at"
TAGS_KEY = "tags"


class DocumentType(StrEnum):
    NOTE = "note"
    TASK = "task"
    TIMER = "timer"


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class Schedule(StrEnum):
    """Coarse scheduling windows, per PLAN.MD section 6.3."""

    NOW = "now"
    SOON = "soon"
    WEEK = "week"


class TimerKind(StrEnum):
    COUNTDOWN = "countdown"
    ABSOLUTE = "absolute"


class TimerStatus(StrEnum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


def utc_now() -> datetime:
    """Current time as an aware UTC datetime. Adapters may use this; domain code
    must receive time through an injected :class:`~nodify.domain.clock.Clock`."""
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    """Serialise a datetime to a stable UTC ISO-8601 string."""
    if moment.tzinfo is None:
        raise ValueError("refusing to serialise a naive datetime")
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def from_iso(text: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``."""
    cleaned = text.strip()
    if cleaned.endswith(("Z", "z")):
        cleaned = f"{cleaned[:-1]}+00:00"
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is not timezone-aware: {text!r}")
    return parsed.astimezone(UTC)


def parse_optional_iso(value: Any) -> datetime | None:
    """Parse a timestamp that may legitimately be null or empty."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else None
    if not isinstance(value, str):
        return None
    try:
        return from_iso(value)
    except ValueError:
        return None


@dataclass(slots=True)
class Document:
    """A parsed Markdown document: frontmatter plus body.

    ``extra`` holds any frontmatter keys the application does not recognise. They
    are written back unchanged so that Obsidian metadata and third-party keys
    survive a save.

    ``frontmatter_source`` is the mapping this document was parsed from, kept so a
    save can update it in place. Rebuilding a fresh dict instead would discard the
    original key order and any comments attached to keys, which is the metadata
    loss the storage layer exists to prevent. It is typed as a plain ``dict`` so
    that no YAML library leaks into the domain; the concrete parser produces a
    dict subclass that satisfies this.
    """

    id: str
    type: DocumentType
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    body: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    frontmatter_source: dict[str, Any] | None = None

    def frontmatter(self) -> dict[str, Any]:
        """The canonical frontmatter mapping, including preserved extra keys."""
        data: dict[str, Any] = {DOCUMENT_ID_KEY: self.id, DOCUMENT_TYPE_KEY: str(self.type)}
        data[TITLE_KEY] = self.title
        if self.created_at is not None:
            data[CREATED_AT_KEY] = to_iso(self.created_at)
        if self.updated_at is not None:
            data[UPDATED_AT_KEY] = to_iso(self.updated_at)
        if self.tags:
            # An empty list is left out entirely rather than written as ``tags: []``,
            # so saving a note that has no tags does not add a key the user never
            # wrote and then re-diffs the file on every subsequent save.
            data[TAGS_KEY] = list(self.tags)
        return data


@dataclass(slots=True)
class Note(Document):
    """A note, stored at ``notes/[Category]/[Note-Title].md``."""

    category: str = ""

    def __init__(
        self,
        id: str,
        title: str,
        category: str,
        body: str = "",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        tags: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        frontmatter_source: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            id=id,
            type=DocumentType.NOTE,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            tags=tags or [],
            body=body,
            extra=extra or {},
            frontmatter_source=frontmatter_source,
        )
        self.category = category


@dataclass(slots=True, init=False)
class Task(Document):
    """A task, stored inside a day file under ``todos/[YYYY-MM]/[DD-YYYY].md``."""

    notes: str = ""
    status: TaskStatus = TaskStatus.OPEN
    priority: str = "normal"
    schedule: Schedule = Schedule.NOW
    due_at: datetime | None = None
    completed_at: datetime | None = None
    completion_history: list[datetime] = field(default_factory=list)

    def __init__(
        self,
        id: str,
        title: str,
        body: str = "",
        notes: str = "",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        tags: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        frontmatter_source: dict[str, Any] | None = None,
        status: TaskStatus = TaskStatus.OPEN,
        priority: str = "normal",
        schedule: Schedule = Schedule.NOW,
        due_at: datetime | None = None,
        completed_at: datetime | None = None,
        completion_history: list[datetime] | None = None,
    ) -> None:
        super().__init__(
            id=id,
            type=DocumentType.TASK,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            tags=tags or [],
            body=body,
            extra=extra or {},
            frontmatter_source=frontmatter_source,
        )
        self.notes = notes
        self.status = status
        self.priority = priority
        self.schedule = schedule
        self.due_at = due_at
        self.completed_at = completed_at
        self.completion_history = completion_history or []

    @property
    def is_done(self) -> bool:
        return self.status is TaskStatus.DONE


@dataclass(slots=True, init=False)
class Timer(Document):
    """A timer or reminder, stored under ``timer/[YYYY-MM]/[DD-YYYY].md``."""

    notes: str = ""
    kind: TimerKind = TimerKind.COUNTDOWN
    starts_at: datetime | None = None
    due_at: datetime | None = None
    duration_seconds: int | None = None
    status: TimerStatus = TimerStatus.SCHEDULED
    notified_at: datetime | None = None
    completed_at: datetime | None = None

    def __init__(
        self,
        id: str,
        title: str,
        body: str = "",
        notes: str = "",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        tags: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        frontmatter_source: dict[str, Any] | None = None,
        kind: TimerKind = TimerKind.COUNTDOWN,
        starts_at: datetime | None = None,
        due_at: datetime | None = None,
        duration_seconds: int | None = None,
        status: TimerStatus = TimerStatus.SCHEDULED,
        notified_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        super().__init__(
            id=id,
            type=DocumentType.TIMER,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            tags=tags or [],
            body=body,
            extra=extra or {},
            frontmatter_source=frontmatter_source,
        )
        self.notes = notes
        self.kind = kind
        self.starts_at = starts_at
        self.due_at = due_at
        self.duration_seconds = duration_seconds
        self.status = status
        self.notified_at = notified_at
        self.completed_at = completed_at
