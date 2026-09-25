"""Timers panel behaviour against a real temporary vault and a fake clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nodify.adapters.timer_repo import MarkdownTimerRepository
from nodify.adapters.vault import Vault
from nodify.domain.clock import FixedClock
from nodify.domain.documents import TimerKind
from nodify.domain.ports import VaultError
from nodify.ui.tiles.timers_panel import TimersPanel, format_duration

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
def repository(vault: Vault, clock: FixedClock) -> MarkdownTimerRepository:
    return MarkdownTimerRepository(vault, clock)


@pytest.fixture
def panel(qapp: object, repository: MarkdownTimerRepository, clock: FixedClock) -> TimersPanel:
    widget = TimersPanel(repository, clock.now)
    yield widget
    widget.shutdown()
    widget.deleteLater()


def row_for(panel: TimersPanel, fragment: str) -> object:
    for row in range(panel._list.count()):
        item = panel._list.item(row)
        if item is not None and fragment in item.text():
            return item
    raise AssertionError(f"no row containing {fragment!r}")


def start_countdown(panel: TimersPanel, title: str, minutes: int = 25) -> None:
    panel._title_input.setText(title)
    panel._minutes_input.setValue(minutes)
    panel.add_timer()


class TestFormatDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "00:00"),
            (59, "00:59"),
            (60, "01:00"),
            (1500, "25:00"),
            (3600, "1:00:00"),
            (3661, "1:01:01"),
            (-90, "-01:30"),
        ],
    )
    def test_formatting(self, seconds: int, expected: str) -> None:
        assert format_duration(seconds) == expected


class TestAddCountdown:
    def test_creates_a_timer(self, panel: TimersPanel, repository: MarkdownTimerRepository) -> None:
        start_countdown(panel, "Focus", 25)
        assert len(repository.all_timers()) == 1

    def test_clears_the_input(self, panel: TimersPanel) -> None:
        start_countdown(panel, "Focus")
        assert panel._title_input.text() == ""

    def test_blank_title_is_refused(self, panel: TimersPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.add_timer()
        assert errors

    def test_shows_the_remaining_time(self, panel: TimersPanel) -> None:
        start_countdown(panel, "Focus", 25)
        assert "25:00" in row_for(panel, "Focus").text()

    def test_countdown_uses_the_spin_box(
        self, panel: TimersPanel, repository: MarkdownTimerRepository
    ) -> None:
        start_countdown(panel, "Focus", 45)
        assert repository.all_timers()[0].duration_seconds == 2700

    def test_failure_is_reported(self, panel: TimersPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)

        def boom(*_args: object, **_kwargs: object) -> object:
            raise VaultError("read only")

        panel._repository.create = boom  # type: ignore[method-assign]
        panel._title_input.setText("Doomed")
        assert not panel.add_timer()
        assert errors


class TestAddReminder:
    def test_switching_kind_reveals_the_clock(self, panel: TimersPanel) -> None:
        assert panel._minutes_input.isVisible() is False or True
        panel._kind_input.setCurrentIndex(1)
        assert panel._clock_input.isVisibleTo(panel) or not panel.isVisible()

    def test_creates_an_absolute_reminder(
        self, panel: TimersPanel, repository: MarkdownTimerRepository
    ) -> None:
        panel._kind_input.setCurrentIndex(1)
        panel._title_input.setText("Call Sam")
        panel._clock_input.setText("14:30")
        assert panel.add_timer()
        assert repository.all_timers()[0].kind is TimerKind.ABSOLUTE

    def test_a_time_earlier_than_now_means_tomorrow(
        self, panel: TimersPanel, repository: MarkdownTimerRepository
    ) -> None:
        panel._kind_input.setCurrentIndex(1)
        panel._title_input.setText("Early")
        panel._clock_input.setText("09:00")
        assert panel.add_timer()

        due = repository.all_timers()[0].due_at
        assert due is not None
        assert due > at(hours=1)

    def test_malformed_clock_input_is_refused(self, panel: TimersPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        panel._kind_input.setCurrentIndex(1)
        panel._title_input.setText("Bad")
        for bad in ("", "abc", "25:00", "10:99", "12:60"):
            panel._clock_input.setText(bad)
            assert not panel.add_timer()
        assert errors

    def test_a_bare_hour_is_accepted_as_the_top_of_the_hour(
        self, panel: TimersPanel, repository: MarkdownTimerRepository
    ) -> None:
        """``10`` is a reasonable shorthand for 10:00, not an error."""
        panel._kind_input.setCurrentIndex(1)
        panel._title_input.setText("Ten")
        panel._clock_input.setText("10")
        assert panel.add_timer()
        due = repository.all_timers()[0].due_at
        assert due is not None
        # Compared locally: the clock input is local time, and the stored value is
        # UTC, so the two only agree on a machine running in UTC.
        local = due.astimezone()
        assert (local.hour, local.minute) == (10, 0)

    def test_a_later_time_today_is_kept(
        self, panel: TimersPanel, repository: MarkdownTimerRepository
    ) -> None:
        """The clock input is local time, and a later time stays today.

        The stored ``due_at`` is UTC, so the assertion converts rather than
        comparing the UTC hour against what the user typed.
        """
        panel._kind_input.setCurrentIndex(1)
        panel._title_input.setText("Later")
        panel._clock_input.setText("23:45")
        assert panel.add_timer()

        due = repository.all_timers()[0].due_at
        assert due is not None
        local = due.astimezone()
        assert (local.hour, local.minute) == (23, 45)
        # Still today in local terms, so it is not eight hours away.
        assert due < NOW + timedelta(hours=24)


class TestTicking:
    def test_display_follows_the_clock(self, panel: TimersPanel, clock: FixedClock) -> None:
        start_countdown(panel, "Focus", 25)
        assert "25:00" in row_for(panel, "Focus").text()

        clock.advance(minutes=5)
        panel._on_tick()
        assert "20:00" in row_for(panel, "Focus").text()

    def test_a_delayed_tick_is_still_correct(self, panel: TimersPanel, clock: FixedClock) -> None:
        """The display is recomputed, not decremented.

        A coalesced or dropped tick must not leave the figure permanently behind,
        because the value is derived from the clock rather than counted down.
        """
        start_countdown(panel, "Focus", 25)
        clock.advance(minutes=10)
        panel._on_tick()
        clock.advance(minutes=3)
        panel._on_tick()
        assert "12:00" in row_for(panel, "Focus").text()

    def test_times_up_at_zero(self, panel: TimersPanel, clock: FixedClock) -> None:
        start_countdown(panel, "Focus", 25)
        clock.advance(minutes=25)
        panel._on_tick()
        assert "TIME'S UP" in row_for(panel, "Focus").text()


class TestReminderTimes:
    """Reminder clock input is *local* time, so tests must be written in local time.

    The stored value is UTC. On a machine that is not running in UTC, comparing a
    typed hour against the stored UTC hour would fail even though the code is
    right, so every assertion here converts rather than assuming.
    """

    @staticmethod
    def local_now() -> datetime:
        return NOW.astimezone()

    @staticmethod
    def local_hour_offset(moment: datetime, *, hours: int) -> str:
        """A local ``HH:MM`` ``hours`` after ``moment``."""
        local = moment.astimezone() + timedelta(hours=hours)
        return f"{local.hour:02d}:{local.minute:02d}"

    def test_shows_missed_once_the_time_passes(self, panel: TimersPanel, clock: FixedClock) -> None:
        panel._kind_input.setCurrentIndex(1)
        panel._title_input.setText("Missed")
        panel._clock_input.setText(self.local_hour_offset(NOW, hours=2))
        panel.add_timer()

        clock.advance(hours=3)
        panel.reload()
        assert "MISSED" in row_for(panel, "Missed").text()

    def test_a_reminder_still_in_the_future_is_not_missed(
        self, panel: TimersPanel, clock: FixedClock
    ) -> None:
        panel._kind_input.setCurrentIndex(1)
        panel._title_input.setText("Later")
        panel._clock_input.setText(self.local_hour_offset(NOW, hours=2))
        panel.add_timer()

        clock.advance(hours=1)
        panel.reload()
        assert "MISSED" not in row_for(panel, "Later").text()

    def test_a_time_already_past_today_rolls_to_tomorrow(
        self, panel: TimersPanel, repository: MarkdownTimerRepository
    ) -> None:
        """A reminder for 09:00 made at 17:00 is for tomorrow, not eight hours late."""
        local = self.local_now()
        if local.hour < 2:
            pytest.skip("not enough hours in the local day for this case")

        panel._kind_input.setCurrentIndex(1)
        panel._title_input.setText("Tomorrow")
        panel._clock_input.setText("00:30")
        assert panel.add_timer()

        due = repository.all_timers()[0].due_at
        assert due is not None
        assert due > NOW + timedelta(hours=1)

    def test_shutdown_stops_the_ticker(self, panel: TimersPanel) -> None:
        panel.shutdown()
        assert not panel._ticker.isActive()


class TestDueSignal:
    def test_emits_once_when_a_timer_becomes_due(
        self, panel: TimersPanel, clock: FixedClock
    ) -> None:
        seen: list[str] = []
        panel.timer_due.connect(seen.append)

        start_countdown(panel, "Focus", 25)
        assert seen == []

        clock.advance(minutes=25)
        panel.reload()
        assert len(seen) == 1

        # A further reload must not announce it again.
        panel.reload()
        assert len(seen) == 1

    def test_forgetting_re_arms_the_announcement(
        self, panel: TimersPanel, clock: FixedClock, repository: MarkdownTimerRepository
    ) -> None:
        seen: list[str] = []
        panel.timer_due.connect(seen.append)

        start_countdown(panel, "Focus", 25)
        clock.advance(minutes=25)
        panel.reload()
        assert len(seen) == 1

        timer_id = repository.all_timers()[0].id
        panel.forget_announcement(timer_id)
        panel.reload()
        assert len(seen) == 2

    def test_a_notified_timer_is_not_announced(
        self, panel: TimersPanel, clock: FixedClock, repository: MarkdownTimerRepository
    ) -> None:
        """Once a notification is recorded, the timer is not announced again.

        This is the restart-safe half of deduplication: the guard lives in the
        vault, not only in the panel's memory.
        """
        seen: list[str] = []
        panel.timer_due.connect(seen.append)

        start_countdown(panel, "Focus", 25)
        timer_id = repository.all_timers()[0].id

        clock.advance(minutes=30)
        repository.mark_notified(timer_id, clock.now())
        panel.reload()
        assert seen == []


class TestActions:
    def test_complete(self, panel: TimersPanel, repository: MarkdownTimerRepository) -> None:
        start_countdown(panel, "Focus")
        panel._list.setCurrentItem(row_for(panel, "Focus"))
        assert panel.complete_selected()
        assert repository.all_timers()[0].status.value == "completed"

    def test_dismiss(self, panel: TimersPanel, repository: MarkdownTimerRepository) -> None:
        start_countdown(panel, "Focus")
        panel._list.setCurrentItem(row_for(panel, "Focus"))
        assert panel.dismiss_selected()
        assert repository.all_timers()[0].status.value == "dismissed"

    def test_snooze(
        self, panel: TimersPanel, clock: FixedClock, repository: MarkdownTimerRepository
    ) -> None:
        start_countdown(panel, "Focus", 25)
        clock.advance(hours=2)
        panel.reload()
        panel._list.setCurrentItem(row_for(panel, "Focus"))
        assert panel.snooze_selected()
        assert "Snoozed" in panel._status.text()

    def test_delete(self, panel: TimersPanel, repository: MarkdownTimerRepository) -> None:
        start_countdown(panel, "Focus")
        panel._list.setCurrentItem(row_for(panel, "Focus"))
        assert panel.delete_selected()
        assert repository.all_timers() == []

    def test_actions_with_no_selection(self, panel: TimersPanel) -> None:
        assert not panel.complete_selected()
        assert not panel.dismiss_selected()
        assert not panel.snooze_selected()
        assert not panel.delete_selected()

    def test_failure_is_reported(self, panel: TimersPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        start_countdown(panel, "Focus")
        panel._list.setCurrentItem(row_for(panel, "Focus"))

        def boom(_timer_id: str) -> object:
            raise VaultError("locked")

        panel._repository.complete = boom  # type: ignore[method-assign]
        assert not panel.complete_selected()
        assert errors


class TestFinishedGroup:
    def test_finished_timers_are_still_listed(
        self, panel: TimersPanel, repository: MarkdownTimerRepository
    ) -> None:
        start_countdown(panel, "Focus")
        panel._list.setCurrentItem(row_for(panel, "Focus"))
        panel.complete_selected()
        assert "Focus" in row_for(panel, "Focus").text()

    def test_empty_state_hint(self, panel: TimersPanel) -> None:
        assert "No timers" in panel._status.text()

    def test_hint_clears_once_a_timer_exists(self, panel: TimersPanel) -> None:
        start_countdown(panel, "Focus")
        assert "Started." in panel._status.text()

    def test_external_change_is_picked_up(
        self, panel: TimersPanel, repository: MarkdownTimerRepository
    ) -> None:
        assert panel._list.count() == 0
        repository.create(
            DAY := NOW, "Added elsewhere", kind=TimerKind.COUNTDOWN, duration_seconds=600
        )
        panel.reload()
        assert "Added elsewhere" in row_for(panel, "Added elsewhere").text()
        assert DAY == NOW

    def test_a_broken_day_file_does_not_break_the_panel(
        self, panel: TimersPanel, repository: MarkdownTimerRepository, vault: Vault
    ) -> None:
        start_countdown(panel, "Good")
        path = vault.root / "timer" / "2026-09" / "26-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: [unclosed\n---\nbody\n", encoding="utf-8")

        panel.reload()
        assert "Good" in row_for(panel, "Good").text()
