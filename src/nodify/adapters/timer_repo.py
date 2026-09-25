"""Timer and reminder persistence.

A timer lives in its day's file, exactly as a task does, so the day-file
guarantees carry over: editing one timer does not rewrite its siblings, and a
``notified_at`` written for one timer is a real edit that is persisted.

Notification deduplication is the reason ``notified_at`` exists. It is written
*before* the notification is attempted, not after it succeeds, because a
notification that fires as the application exits would otherwise be lost, and a
notification that fires just before a crash would otherwise be re-announced on
the next launch. Recording the intent is the only ordering that cannot double
notify; a user who misses one alert is far less costly than one who gets the
same alert every time they open the app.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from nodify.adapters.day_file import TimerDayFile, normalise_day
from nodify.domain.clock import Clock, SystemClock
from nodify.domain.documents import Timer, TimerKind, TimerStatus
from nodify.domain.ports import (
    DocumentNotFoundError,
    TimerRepository,
    VaultError,
)

ID_PREFIX_TIMER = "timer_"
_ID_COUNTER_MAX = 0xFFFFFFFF

#: Longest countdown a single timer may be given: one day.
MAX_COUNTDOWN = timedelta(days=1)

DEFAULT_COUNTDOWN_MINUTES = 25


class MarkdownDocumentStore(Protocol):
    def read(self, path: Path) -> tuple[dict[str, Any], str]: ...

    def write(self, path: Path, frontmatter: Any, body: str) -> None: ...


class VaultLike(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def documents(self) -> MarkdownDocumentStore: ...


class _IdGenerator:
    def __init__(self) -> None:
        self._counter = 0

    def next_id(self, moment: datetime) -> str:
        self._counter = (self._counter + 1) % _ID_COUNTER_MAX
        return f"{ID_PREFIX_TIMER}{int(moment.timestamp()):010d}{self._counter:08x}"


class MarkdownTimerRepository:
    """A :class:`TimerRepository` backed by day files on disk."""

    def __init__(self, vault: VaultLike, clock: Clock | None = None) -> None:
        self._vault = vault
        self._ids = _IdGenerator()
        self._clock: Clock = clock or SystemClock()

    def now(self) -> datetime:
        return self._clock.now()

    def set_clock(self, clock: Clock) -> None:
        """Replace the clock, for a caller that drives time explicitly."""
        self._clock = clock

    @property
    def _root(self) -> Path:
        return self._vault.root

    @property
    def _documents(self) -> MarkdownDocumentStore:
        return self._vault.documents

    def _load(self, day: datetime) -> TimerDayFile:
        return TimerDayFile.load(self._documents, self._root, day)

    # ------------------------------------------------------------------ read

    def get(self, timer_id: str) -> Timer:
        for day_file in self._day_files():
            if not day_file.has(timer_id):
                continue
            for timer in day_file.timers():
                if timer.id == timer_id:
                    return timer
        raise DocumentNotFoundError(f"no timer with id {timer_id!r}")

    def _day_files(self) -> list[TimerDayFile]:
        """Every timer day file, in chronological order."""
        timer_root = self._root / "timer"
        if not timer_root.is_dir():
            return []
        loaded: list[TimerDayFile] = []
        for month_dir in sorted(timer_root.iterdir(), key=lambda p: p.name):
            if not month_dir.is_dir():
                continue
            for day_path in sorted(month_dir.glob("*.md")):
                day = _day_from_name(day_path.name, month_dir.name)
                if day is None:
                    continue
                try:
                    loaded.append(TimerDayFile.load(self._documents, self._root, day))
                except VaultError:
                    # A corrupt day file must not hide every other day.
                    continue
        return loaded

    def list_for_day(self, day: datetime) -> list[Timer]:
        return self._load(day).timers()

    def all_timers(self) -> list[Timer]:
        timers: list[Timer] = []
        for day_file in self._day_files():
            timers.extend(day_file.timers())
        return timers

    def all_pending(self) -> list[Timer]:
        """Every timer that has not been completed or dismissed.

        This is what the reminder engine evaluates. Ordering is by due time so the
        caller sees the most urgent first without re-sorting.
        """
        pending = [timer for timer in self.all_timers() if timer.status is TimerStatus.SCHEDULED]
        return sorted(pending, key=self._due_key)

    @staticmethod
    def _due_key(timer: Timer) -> tuple[int, datetime, str]:
        if timer.due_at is None:
            return (1, datetime.max.replace(tzinfo=UTC), timer.id)
        return (0, timer.due_at, timer.id)

    # ----------------------------------------------------------------- write

    def create(
        self,
        day: datetime,
        title: str,
        *,
        kind: TimerKind = TimerKind.COUNTDOWN,
        notes: str = "",
        due_at: datetime | None = None,
        duration_seconds: int | None = None,
        tags: list[str] | None = None,
    ) -> Timer:
        """Create a timer or reminder.

        A countdown derives its due time from the duration. An absolute reminder
        must be given an explicit time. Exactly one of the two must be supplied,
        because a timer with neither can never become due.
        """
        cleaned = title.strip()
        if not cleaned:
            raise VaultError("a timer needs a title")

        created = self.now()

        if kind is TimerKind.COUNTDOWN:
            if due_at is not None:
                # A countdown is measured from now. An explicit time would have to
                # be the *start*, and the user has a separate concept for that, so
                # accepting a due time here would be ambiguous rather than helpful.
                raise VaultError("a countdown takes a duration, not a due time")
            seconds = _validate_duration(
                duration_seconds if duration_seconds is not None else DEFAULT_COUNTDOWN_MINUTES * 60
            )
            starts_at = created
            resolved_due = created + timedelta(seconds=seconds)
            resolved_duration: int | None = seconds
        else:
            if due_at is None:
                raise VaultError("an absolute reminder needs a due time")
            starts_at = None
            resolved_due = _as_aware(due_at)
            resolved_duration = duration_seconds

        day_file = self._load(day)
        timer = Timer(
            id=self._ids.next_id(created),
            title=cleaned,
            created_at=created,
            updated_at=created,
            tags=tags or [],
            notes=notes,
            kind=kind,
            starts_at=starts_at,
            due_at=resolved_due,
            duration_seconds=resolved_duration,
            status=TimerStatus.SCHEDULED,
        )
        day_file.add(TimerDayFile.record_for(timer))
        day_file.save(self._documents)
        return timer

    def create_countdown(self, day: datetime, title: str, minutes: int, **kwargs: Any) -> Timer:
        """Create a countdown timer. A convenience for the common case."""
        return self.create(
            day, title, kind=TimerKind.COUNTDOWN, duration_seconds=minutes * 60, **kwargs
        )

    def create_reminder(self, day: datetime, title: str, due_at: datetime, **kwargs: Any) -> Timer:
        """Create an absolute reminder. A convenience for the common case."""
        return self.create(day, title, kind=TimerKind.ABSOLUTE, due_at=due_at, **kwargs)

    def _mutate(self, timer_id: str, change: Any) -> Timer:
        for day_file in self._day_files():
            if not day_file.has(timer_id):
                continue
            timer = next((t for t in day_file.timers() if t.id == timer_id), None)
            if timer is None:
                break
            change(timer)
            timer.updated_at = self.now()
            day_file.put(TimerDayFile.record_for(timer))
            day_file.save(self._documents)
            return timer

        raise DocumentNotFoundError(f"no timer with id {timer_id!r}")

    def update(self, timer_id: str, **changes: Any) -> Timer:
        def apply(timer: Timer) -> None:
            for key, value in changes.items():
                if key == "title" and not str(value).strip():
                    raise VaultError("a timer needs a title")
                if key == "due_at" and value is not None:
                    value = _as_aware(value)
                if key == "kind" and value is not None:
                    value = TimerKind(str(value))
                if key == "status" and value is not None:
                    value = TimerStatus(str(value))
                if key == "duration_seconds" and value is not None:
                    value = _validate_duration(int(value))
                if hasattr(timer, key):
                    setattr(timer, key, value)

        return self._mutate(timer_id, apply)

    def complete(self, timer_id: str, at: datetime) -> Timer:
        def apply(timer: Timer) -> None:
            timer.status = TimerStatus.COMPLETED
            timer.completed_at = _as_aware(at)

        return self._mutate(timer_id, apply)

    def dismiss(self, timer_id: str) -> Timer:
        """Dismiss a timer without completing it.

        Distinct from completing: a dismissed timer is one the user has seen and
        chosen to ignore, and it should not be counted as something that was
        actually done.
        """

        def apply(timer: Timer) -> None:
            timer.status = TimerStatus.DISMISSED

        return self._mutate(timer_id, apply)

    def mark_notified(self, timer_id: str, at: datetime) -> Timer:
        """Record that a due notification has been raised for this timer.

        Written before delivery is attempted, so a notification cannot be
        re-announced after a crash or a clean exit between the write and the
        delivery.
        """

        def apply(timer: Timer) -> None:
            timer.notified_at = _as_aware(at)

        return self._mutate(timer_id, apply)

    def snooze(self, timer_id: str, minutes: int, *, at: datetime) -> Timer:
        """Push a due timer back and re-arm its notification.

        ``notified_at`` is cleared so the reminder becomes eligible again, which
        is the whole point of a snooze: the user asked to be asked again.
        """
        if minutes <= 0:
            raise VaultError("a snooze must be at least one minute")

        def apply(timer: Timer) -> None:
            moment = _as_aware(at)
            base = timer.due_at or moment
            if base < moment:
                base = moment
            timer.due_at = base + timedelta(minutes=minutes)
            timer.notified_at = None
            timer.status = TimerStatus.SCHEDULED

        return self._mutate(timer_id, apply)

    def delete(self, timer_id: str) -> None:
        for day_file in self._day_files():
            if day_file.has(timer_id):
                day_file.remove(timer_id)
                day_file.save(self._documents)
                return
        raise DocumentNotFoundError(f"no timer with id {timer_id!r}")


def _validate_duration(seconds: int) -> int:
    if seconds <= 0:
        raise VaultError("a countdown must be longer than zero")
    if seconds > int(MAX_COUNTDOWN.total_seconds()):
        raise VaultError(f"a countdown cannot exceed {MAX_COUNTDOWN.days} day(s)")
    return seconds


def _as_aware(value: Any) -> datetime:
    """Require a timezone-aware datetime.

    A naive time would be interpreted in the machine's local zone, so the same
    vault would mean different things in two time zones.
    """
    if not isinstance(value, datetime):
        raise VaultError("expected a datetime")
    if value.tzinfo is None:
        raise VaultError("a timer time must be timezone-aware")
    return value.astimezone(UTC)


def _day_from_name(file_name: str, folder_name: str) -> datetime | None:
    """Parse ``25-2026.md`` inside ``2026-09`` back into a date."""
    if not file_name.endswith(".md"):
        return None
    try:
        year_text, month_text = folder_name.split("-")
        day_text, file_year_text = file_name[:-3].split("-")
        year, month, day = int(file_year_text), int(month_text), int(day_text)
    except ValueError:
        return None
    if len(year_text) != 4 or year_text != file_year_text:
        return None
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


__all__ = [
    "DEFAULT_COUNTDOWN_MINUTES",
    "ID_PREFIX_TIMER",
    "MAX_COUNTDOWN",
    "MarkdownTimerRepository",
    "TimerRepository",
    "normalise_day",
]
