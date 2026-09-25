"""Schedule windows and due-time evaluation.

The plan fixes these boundaries: ``now`` is due at or before the evaluation time,
``soon`` is the next 24 hours, and ``week`` is after 24 hours through seven days.
All comparisons are on absolute UTC instants so that a task's stored ``due_at``
always wins over the coarse window it was created in.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nodify.domain.documents import Schedule

SOON = timedelta(hours=24)
WEEK = timedelta(days=7)


def in_schedule_window(
    due_at: datetime | None,
    schedule: Schedule,
    *,
    now: datetime,
) -> bool:
    """Whether ``due_at`` falls inside the window implied by ``schedule``.

    A task with no explicit ``due_at`` is treated as due now, which keeps the
    ``now`` window honest without inventing a timestamp.
    """
    if due_at is None:
        return schedule is Schedule.NOW

    delta = due_at - now
    match schedule:
        case Schedule.NOW:
            return delta <= timedelta(0)
        case Schedule.SOON:
            return timedelta(0) < delta <= SOON
        case Schedule.WEEK:
            return SOON < delta <= WEEK


def resolve_due_at(
    schedule: Schedule,
    *,
    now: datetime,
    explicit: datetime | None = None,
) -> datetime:
    """Pick the due timestamp for a task.

    An explicit ``due_at`` is always preserved. Otherwise the schedule window
    picks a representative point: the middle of the window, so that ``soon`` does
    not immediately read as overdue and ``week`` is not pushed to the last minute.
    """
    if explicit is not None:
        return explicit
    match schedule:
        case Schedule.NOW:
            return now
        case Schedule.SOON:
            return now + SOON / 2
        case Schedule.WEEK:
            return now + SOON + (WEEK - SOON) / 2


def is_overdue(due_at: datetime | None, *, now: datetime) -> bool:
    """Whether something due at ``due_at`` should be shown as overdue."""
    return due_at is not None and due_at < now


def seconds_remaining(due_at: datetime | None, *, now: datetime) -> int | None:
    """Whole seconds until ``due_at``; negative once overdue, ``None`` if unset."""
    if due_at is None:
        return None
    return int((due_at - now).total_seconds())
