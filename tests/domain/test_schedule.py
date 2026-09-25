"""Schedule windows and due-time resolution.

The boundaries come from PLAN.MD section 6.3: ``now`` is due, ``soon`` is the next
24 hours, ``week`` is after 24 hours up to seven days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nodify.domain.documents import Schedule
from nodify.domain.schedule import (
    SOON,
    WEEK,
    in_schedule_window,
    is_overdue,
    resolve_due_at,
    seconds_remaining,
)

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def at(**kwargs: float) -> datetime:
    return NOW + timedelta(**kwargs)


class TestInScheduleWindow:
    def test_now_window_is_due_up_to_and_including_now(self) -> None:
        assert in_schedule_window(at(hours=-1), Schedule.NOW, now=NOW)
        assert in_schedule_window(NOW, Schedule.NOW, now=NOW)

    def test_now_window_excludes_the_future(self) -> None:
        assert not in_schedule_window(at(seconds=1), Schedule.NOW, now=NOW)

    def test_soon_window_is_strictly_after_now(self) -> None:
        assert not in_schedule_window(NOW, Schedule.SOON, now=NOW)
        assert in_schedule_window(at(seconds=1), Schedule.SOON, now=NOW)

    def test_soon_window_includes_exactly_24_hours(self) -> None:
        assert in_schedule_window(at(hours=24), Schedule.SOON, now=NOW)
        assert not in_schedule_window(at(hours=24, seconds=1), Schedule.SOON, now=NOW)

    def test_week_window_starts_after_24_hours(self) -> None:
        assert not in_schedule_window(at(hours=24), Schedule.WEEK, now=NOW)
        assert in_schedule_window(at(hours=24, seconds=1), Schedule.WEEK, now=NOW)

    def test_week_window_includes_exactly_seven_days(self) -> None:
        assert in_schedule_window(at(days=7), Schedule.WEEK, now=NOW)
        assert not in_schedule_window(at(days=7, seconds=1), Schedule.WEEK, now=NOW)

    def test_beyond_a_week_is_outside_every_window(self) -> None:
        for window in Schedule:
            assert not in_schedule_window(at(days=8), window, now=NOW)

    def test_missing_due_at_reads_as_now_only(self) -> None:
        assert in_schedule_window(None, Schedule.NOW, now=NOW)
        for window in (Schedule.SOON, Schedule.WEEK):
            assert not in_schedule_window(None, window, now=NOW)

    def test_windows_are_mutually_exclusive(self) -> None:
        for offset in (
            {"hours": -2},
            {"hours": 1},
            {"hours": 5},
            {"days": 3},
            {"days": 9},
        ):
            moment = at(**offset)
            matches = [window for window in Schedule if in_schedule_window(moment, window, now=NOW)]
            assert len(matches) <= 1, f"{offset} matched {matches}"


class TestResolveDueAt:
    def test_explicit_due_at_is_always_kept(self) -> None:
        explicit = at(days=3)
        for window in Schedule:
            assert resolve_due_at(window, now=NOW, explicit=explicit) == explicit

    def test_now_resolves_to_now(self) -> None:
        assert resolve_due_at(Schedule.NOW, now=NOW) == NOW

    def test_soon_resolves_into_the_middle_of_its_window(self) -> None:
        resolved = resolve_due_at(Schedule.SOON, now=NOW)
        assert in_schedule_window(resolved, Schedule.SOON, now=NOW)

    def test_week_resolves_into_the_middle_of_its_window(self) -> None:
        resolved = resolve_due_at(Schedule.WEEK, now=NOW)
        assert in_schedule_window(resolved, Schedule.WEEK, now=NOW)

    def test_resolved_due_at_is_not_immediately_overdue(self) -> None:
        for window in Schedule:
            assert not is_overdue(resolve_due_at(window, now=NOW), now=NOW)

    def test_window_constants(self) -> None:
        assert timedelta(hours=24) == SOON
        assert timedelta(days=7) == WEEK


class TestIsOverdue:
    def test_past_is_overdue(self) -> None:
        assert is_overdue(at(seconds=-1), now=NOW)

    def test_exactly_now_is_not_overdue(self) -> None:
        assert not is_overdue(NOW, now=NOW)

    def test_future_is_not_overdue(self) -> None:
        assert not is_overdue(at(seconds=1), now=NOW)

    def test_unset_is_not_overdue(self) -> None:
        assert not is_overdue(None, now=NOW)


class TestSecondsRemaining:
    def test_future_is_positive(self) -> None:
        assert seconds_remaining(at(seconds=90), now=NOW) == 90

    def test_past_is_negative(self) -> None:
        assert seconds_remaining(at(seconds=-90), now=NOW) == -90

    def test_unset_is_none(self) -> None:
        assert seconds_remaining(None, now=NOW) is None

    def test_truncates_towards_the_instant(self) -> None:
        assert seconds_remaining(at(milliseconds=1500), now=NOW) == 1

    @pytest.mark.parametrize("seconds", [0, 1, 59, 3600, 86399])
    def test_whole_seconds_are_exact(self, seconds: int) -> None:
        assert seconds_remaining(at(seconds=seconds), now=NOW) == seconds
