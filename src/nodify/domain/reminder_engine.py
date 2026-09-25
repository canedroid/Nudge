"""Reminder evaluation.

The engine computes timer state at an injected clock time and decides which timers
are eligible for a notification. It does not deliver notifications, does not
write to disk, and does not import Qt. Everything time-dependent arrives through
the :class:`~nodify.domain.clock.Clock`, so overdue detection and notification
deduplication are fully testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nodify.domain.clock import Clock, SystemClock
from nodify.domain.documents import Timer, TimerKind, TimerStatus
from nodify.domain.schedule import seconds_remaining


class TimerState(StrEnum):
    """Derived UI state. Computed, never stored."""

    SCHEDULED = "scheduled"
    RUNNING = "running"
    DUE = "due"
    OVERDUE = "overdue"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class TimerView:
    """A timer plus its derived state at a point in time."""

    timer: Timer
    state: TimerState
    remaining_seconds: int | None

    @property
    def id(self) -> str:
        return self.timer.id

    @property
    def title(self) -> str:
        return self.timer.title


class ReminderEngine:
    """Evaluates due state without depending on any UI or storage adapter."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def now(self) -> datetime:
        return self._clock.now()

    def state_of(self, timer: Timer, *, now: datetime | None = None) -> TimerState:
        """Derive a single timer's state."""
        moment = now if now is not None else self._clock.now()

        if timer.status is TimerStatus.COMPLETED:
            return TimerState.COMPLETED
        if timer.status is TimerStatus.DISMISSED:
            return TimerState.DISMISSED

        due_at = timer.due_at
        if due_at is None and timer.duration_seconds is not None and timer.starts_at:
            from datetime import timedelta

            due_at = timer.starts_at + timedelta(seconds=timer.duration_seconds)

        if due_at is None:
            return TimerState.SCHEDULED
        if due_at < moment:
            return TimerState.OVERDUE
        if due_at == moment:
            return TimerState.DUE
        if timer.kind is TimerKind.COUNTDOWN:
            return TimerState.RUNNING
        return TimerState.SCHEDULED

    def view(self, timer: Timer, *, now: datetime | None = None) -> TimerView:
        moment = now if now is not None else self._clock.now()
        return TimerView(
            timer=timer,
            state=self.state_of(timer, now=moment),
            remaining_seconds=seconds_remaining(timer.due_at, now=moment),
        )

    def views(self, timers: Sequence[Timer], *, now: datetime | None = None) -> list[TimerView]:
        moment = now if now is not None else self._clock.now()
        return [self.view(timer, now=moment) for timer in timers]

    def is_notification_eligible(self, timer: Timer, *, now: datetime | None = None) -> bool:
        """Whether ``timer`` should raise a notification right now.

        ``notified_at`` is the deduplication gate, and it is persisted, so a timer
        that fired before the app was closed is not announced again on the next
        launch. A timer that becomes due again after being snoozed has its
        ``notified_at`` cleared, which makes it eligible once more.
        """
        moment = now if now is not None else self._clock.now()
        state = self.state_of(timer, now=moment)
        if state not in (TimerState.DUE, TimerState.OVERDUE):
            return False
        # Both sides are required: a timer carrying notified_at but no due_at
        # cannot be compared, and must not be treated as already announced.
        already_announced = (
            timer.notified_at is not None
            and timer.due_at is not None
            and timer.notified_at >= timer.due_at
        )
        return not already_announced

    def due_for_notification(
        self, timers: Sequence[Timer], *, now: datetime | None = None
    ) -> list[Timer]:
        moment = now if now is not None else self._clock.now()
        return [t for t in timers if self.is_notification_eligible(t, now=moment)]

    def snapshot(self, timers: Sequence[Timer]) -> list[TimerView]:
        """A restart-safe view of every timer at the current time.

        Called at launch to surface timers that came due while the application was
        closed, as overdue rather than silently completed.
        """
        return self.views(timers, now=self._clock.now())
