"""Reminder evaluation against an injected clock.

No test here touches the wall clock, which is the point of the ``Clock`` protocol:
due detection, overdue detection and notification deduplication are all
deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nodify.domain.clock import FixedClock, SystemClock
from nodify.domain.documents import (
    Timer,
    TimerKind,
    TimerStatus,
)
from nodify.domain.reminder_engine import ReminderEngine, TimerState

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def at(**kwargs: float) -> datetime:
    return NOW + timedelta(**kwargs)


def make_timer(
    *,
    due: datetime | None = None,
    kind: TimerKind = TimerKind.COUNTDOWN,
    status: TimerStatus = TimerStatus.SCHEDULED,
    notified_at: datetime | None = None,
    starts_at: datetime | None = None,
    duration_seconds: int | None = None,
) -> Timer:
    return Timer(
        id="tm1",
        title="Call Sam",
        kind=kind,
        status=status,
        due_at=due,
        notified_at=notified_at,
        starts_at=starts_at,
        duration_seconds=duration_seconds,
    )


class TestClock:
    def test_system_clock_is_aware_utc(self) -> None:
        moment = SystemClock().now()
        assert moment.tzinfo is not None
        assert moment.utcoffset() == timedelta(0)

    def test_fixed_clock_returns_the_same_instant(self) -> None:
        clock = FixedClock(NOW)
        assert clock.now() == NOW
        assert clock.now() == NOW

    def test_fixed_clock_advances(self) -> None:
        clock = FixedClock(NOW)
        clock.advance(hours=2, minutes=30)
        assert clock.now() == NOW + timedelta(hours=2, minutes=30)

    def test_fixed_clock_can_be_set(self) -> None:
        clock = FixedClock(NOW)
        clock.set(at(days=1))
        assert clock.now() == at(days=1)

    def test_fixed_clock_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            FixedClock(datetime(2026, 9, 25, 12, 0))  # noqa: DTZ001

    def test_set_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            FixedClock(NOW).set(datetime(2026, 9, 25, 12, 0))  # noqa: DTZ001


class TestState:
    def test_completed_wins_over_everything(self) -> None:
        timer = make_timer(due=at(days=-1), status=TimerStatus.COMPLETED)
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.COMPLETED

    def test_dismissed_is_terminal(self) -> None:
        timer = make_timer(due=at(days=-1), status=TimerStatus.DISMISSED)
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.DISMISSED

    def test_future_countdown_is_running(self) -> None:
        timer = make_timer(due=at(minutes=5))
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.RUNNING

    def test_future_absolute_is_scheduled(self) -> None:
        timer = make_timer(due=at(hours=5), kind=TimerKind.ABSOLUTE)
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.SCHEDULED

    def test_exact_instant_is_due(self) -> None:
        assert ReminderEngine(FixedClock(NOW)).state_of(make_timer(due=NOW)) is TimerState.DUE

    def test_past_is_overdue(self) -> None:
        timer = make_timer(due=at(seconds=-1))
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.OVERDUE

    def test_no_due_at_is_scheduled(self) -> None:
        assert ReminderEngine(FixedClock(NOW)).state_of(make_timer()) is TimerState.SCHEDULED

    def test_duration_is_derived_into_a_due_instant(self) -> None:
        timer = make_timer(starts_at=at(minutes=-5), duration_seconds=60)
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.OVERDUE

    def test_duration_landing_exactly_on_now_is_due(self) -> None:
        timer = make_timer(starts_at=at(minutes=-5), duration_seconds=300)
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.DUE

    def test_duration_in_the_future_is_running(self) -> None:
        timer = make_timer(starts_at=NOW, duration_seconds=300)
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.RUNNING

    def test_duration_without_start_is_scheduled(self) -> None:
        timer = make_timer(duration_seconds=300)
        assert ReminderEngine(FixedClock(NOW)).state_of(timer) is TimerState.SCHEDULED

    def test_state_follows_the_clock(self) -> None:
        clock = FixedClock(NOW)
        engine = ReminderEngine(clock)
        timer = make_timer(due=at(minutes=5))
        assert engine.state_of(timer) is TimerState.RUNNING

        clock.advance(minutes=5)
        assert engine.state_of(timer) is TimerState.DUE

        clock.advance(minutes=1)
        assert engine.state_of(timer) is TimerState.OVERDUE


class TestNotificationEligibility:
    def test_due_timer_is_eligible(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        assert engine.is_notification_eligible(make_timer(due=NOW))

    def test_overdue_timer_is_eligible(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        assert engine.is_notification_eligible(make_timer(due=at(minutes=-5)))

    def test_running_timer_is_not_eligible(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        assert not engine.is_notification_eligible(make_timer(due=at(minutes=5)))

    def test_completed_timer_is_not_eligible(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        timer = make_timer(due=at(minutes=-5), status=TimerStatus.COMPLETED)
        assert not engine.is_notification_eligible(timer)

    def test_already_notified_timer_is_not_announced_twice(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        timer = make_timer(due=at(minutes=-5), notified_at=at(minutes=-4))
        assert not engine.is_notification_eligible(timer)

    def test_notified_after_due_is_suppressed(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        timer = make_timer(due=at(minutes=-5), notified_at=at(minutes=1))
        assert not engine.is_notification_eligible(timer)

    def test_snoozed_timer_becomes_eligible_again(self) -> None:
        """Clearing notified_at on reschedule re-arms the notification."""
        engine = ReminderEngine(FixedClock(NOW))
        timer = make_timer(due=NOW, notified_at=None)
        assert engine.is_notification_eligible(timer)

    def test_due_for_notification_filters_a_mixed_set(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        timers = [
            make_timer(due=at(minutes=-5)),
            make_timer(due=at(hours=2)),
            make_timer(due=NOW),
            make_timer(due=at(minutes=-9), notified_at=at(minutes=-8)),
        ]
        eligible = engine.due_for_notification(timers)
        assert len(eligible) == 2

    def test_due_for_notification_on_empty_set(self) -> None:
        assert ReminderEngine(FixedClock(NOW)).due_for_notification([]) == []


class TestViews:
    def test_view_carries_state_and_remaining(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        view = engine.view(make_timer(due=at(minutes=2)))
        assert view.state is TimerState.RUNNING
        assert view.remaining_seconds == 120
        assert view.id == "tm1"
        assert view.title == "Call Sam"

    def test_views_covers_every_timer(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        views = engine.views([make_timer(), make_timer(due=NOW)])
        assert len(views) == 2

    def test_snapshot_uses_the_injected_clock(self) -> None:
        clock = FixedClock(at(hours=3))
        views = ReminderEngine(clock).snapshot([make_timer(due=at(hours=1))])
        assert views[0].state is TimerState.OVERDUE

    def test_restart_does_not_silently_complete_a_missed_timer(self) -> None:
        """A timer that came due while the app was closed shows as overdue."""
        clock = FixedClock(at(hours=3))
        engine = ReminderEngine(clock)
        view = engine.snapshot([make_timer(due=at(hours=1))])[0]
        assert view.state is TimerState.OVERDUE
        assert view.timer.status is TimerStatus.SCHEDULED

    def test_explicit_now_overrides_the_clock(self) -> None:
        engine = ReminderEngine(FixedClock(NOW))
        timer = make_timer(due=at(hours=5))
        assert engine.state_of(timer, now=at(hours=6)) is TimerState.OVERDUE
