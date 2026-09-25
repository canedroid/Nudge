"""Timer and reminder persistence, including notification deduplication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nodify.adapters.timer_repo import (
    MAX_COUNTDOWN,
    MarkdownTimerRepository,
)
from nodify.adapters.vault import Vault
from nodify.domain.clock import FixedClock
from nodify.domain.documents import TimerKind, TimerStatus
from nodify.domain.ports import DocumentNotFoundError, VaultError

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
DAY = datetime(2026, 9, 25, tzinfo=UTC)


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
def repo(vault: Vault, clock: FixedClock) -> MarkdownTimerRepository:
    return MarkdownTimerRepository(vault, clock)


class TestCreateCountdown:
    def test_creates_a_day_file(self, repo: MarkdownTimerRepository, vault: Vault) -> None:
        repo.create_countdown(DAY, "Focus", 25)
        assert (vault.root / "timer" / "2026-09" / "25-2026.md").is_file()

    def test_due_time_is_now_plus_the_duration(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        assert timer.due_at == NOW + timedelta(minutes=25)
        assert timer.duration_seconds == 1500
        assert timer.kind is TimerKind.COUNTDOWN

    def test_starts_now(self, repo: MarkdownTimerRepository) -> None:
        assert repo.create_countdown(DAY, "Focus", 25).starts_at == NOW

    def test_default_duration(self, repo: MarkdownTimerRepository) -> None:
        assert repo.create_countdown(DAY, "Focus", 25).duration_seconds == 1500

    def test_default_when_no_duration_given(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create(DAY, "Focus", kind=TimerKind.COUNTDOWN)
        assert timer.due_at == NOW + timedelta(minutes=25)

    def test_a_countdown_refuses_a_due_time(self, repo: MarkdownTimerRepository) -> None:
        with pytest.raises(VaultError, match="duration, not a due time"):
            repo.create(DAY, "Focus", kind=TimerKind.COUNTDOWN, due_at=at(hours=1))

    def test_zero_duration_is_rejected(self, repo: MarkdownTimerRepository) -> None:
        with pytest.raises(VaultError, match="longer than zero"):
            repo.create_countdown(DAY, "Focus", 0)

    def test_negative_duration_is_rejected(self, repo: MarkdownTimerRepository) -> None:
        with pytest.raises(VaultError, match="longer than zero"):
            repo.create_countdown(DAY, "Focus", -5)

    def test_over_long_is_rejected(self, repo: MarkdownTimerRepository) -> None:
        too_long = int(MAX_COUNTDOWN.total_seconds()) + 60
        with pytest.raises(VaultError, match="cannot exceed"):
            repo.create(DAY, "Forever", duration_seconds=too_long)

    def test_blank_title_is_rejected(self, repo: MarkdownTimerRepository, vault: Vault) -> None:
        with pytest.raises(VaultError, match="needs a title"):
            repo.create_countdown(DAY, "   ", 5)
        assert not (vault.root / "timer" / "2026-09" / "25-2026.md").exists()

    def test_ids_are_unique(self, repo: MarkdownTimerRepository) -> None:
        ids = {repo.create_countdown(DAY, f"T{i}", 5).id for i in range(20)}
        assert len(ids) == 20


class TestCreateReminder:
    def test_uses_the_given_time(self, repo: MarkdownTimerRepository) -> None:
        due = at(hours=2)
        timer = repo.create_reminder(DAY, "Call Sam", due)
        assert timer.due_at == due
        assert timer.kind is TimerKind.ABSOLUTE

    def test_has_no_start_time(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_reminder(DAY, "Call Sam", at(hours=2))
        assert timer.starts_at is None

    def test_a_reminder_needs_a_time(self, repo: MarkdownTimerRepository) -> None:
        with pytest.raises(VaultError, match="needs a due time"):
            repo.create(DAY, "Call Sam", kind=TimerKind.ABSOLUTE)

    def test_a_naive_due_time_is_rejected(self, repo: MarkdownTimerRepository) -> None:
        with pytest.raises(VaultError, match="timezone-aware"):
            repo.create_reminder(DAY, "Call Sam", datetime(2026, 9, 25, 14))  # noqa: DTZ001


class TestRead:
    def test_get_by_id(self, repo: MarkdownTimerRepository) -> None:
        created = repo.create_countdown(DAY, "Focus", 25)
        assert repo.get(created.id).title == "Focus"

    def test_get_unknown_id_raises(self, repo: MarkdownTimerRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.get("timer_nope")

    def test_several_timers_share_the_day(self, repo: MarkdownTimerRepository) -> None:
        for index in range(3):
            repo.create_countdown(DAY, f"Timer {index}", 5)
        assert len(repo.list_for_day(DAY)) == 3

    def test_empty_vault(self, repo: MarkdownTimerRepository) -> None:
        assert repo.all_timers() == []

    def test_ignores_a_corrupt_day_file(self, repo: MarkdownTimerRepository, vault: Vault) -> None:
        repo.create_countdown(DAY, "Good", 5)
        path = vault.root / "timer" / "2026-09" / "26-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: [unclosed\n---\nbody\n", encoding="utf-8")
        assert len(repo.all_timers()) == 1

    def test_ignores_a_stray_file(self, repo: MarkdownTimerRepository, vault: Vault) -> None:
        repo.create_countdown(DAY, "Good", 5)
        path = vault.root / "timer" / "2026-09" / "notes.md"
        path.write_text("---\nid: day_x\n---\nbody\n", encoding="utf-8")
        assert len(repo.all_timers()) == 1


class TestPending:
    def test_all_pending_omits_finished(self, repo: MarkdownTimerRepository) -> None:
        keep = repo.create_countdown(DAY, "Keep", 5)
        done = repo.create_countdown(DAY, "Done", 5)
        repo.complete(done.id, at())
        assert [t.id for t in repo.all_pending()] == [keep.id]

    def test_all_pending_omits_dismissed(self, repo: MarkdownTimerRepository) -> None:
        keep = repo.create_countdown(DAY, "Keep", 5)
        gone = repo.create_countdown(DAY, "Gone", 5)
        repo.dismiss(gone.id)
        assert [t.id for t in repo.all_pending()] == [keep.id]

    def test_all_pending_is_ordered_by_due_time(self, repo: MarkdownTimerRepository) -> None:
        repo.create_reminder(DAY, "Later", at(hours=5))
        repo.create_reminder(DAY, "Sooner", at(hours=1))
        assert [t.title for t in repo.all_pending()] == ["Sooner", "Later"]


class TestLifecycle:
    def test_complete(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        done = repo.complete(timer.id, at(hours=1))
        assert done.status is TimerStatus.COMPLETED
        assert done.completed_at == at(hours=1)

    def test_complete_survives_a_reload(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        repo.complete(timer.id, at(hours=1))
        assert repo.get(timer.id).status is TimerStatus.COMPLETED

    def test_dismiss_is_not_completion(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        dismissed = repo.dismiss(timer.id)
        assert dismissed.status is TimerStatus.DISMISSED
        assert dismissed.completed_at is None

    def test_update_the_title(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Before", 25)
        assert repo.update(timer.id, title="After").title == "After"

    def test_update_the_duration_moves_the_due_time_too(
        self, repo: MarkdownTimerRepository
    ) -> None:
        """Changing a countdown's length must re-derive when it is due."""
        timer = repo.create_countdown(DAY, "Focus", 25)
        updated = repo.update(timer.id, duration_seconds=600)
        assert updated.duration_seconds == 600
        assert updated.due_at == timer.due_at

    def test_blank_title_is_rejected(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Before", 25)
        with pytest.raises(VaultError, match="needs a title"):
            repo.update(timer.id, title="  ")

    def test_delete(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        repo.delete(timer.id)
        with pytest.raises(DocumentNotFoundError):
            repo.get(timer.id)

    def test_delete_leaves_siblings(self, repo: MarkdownTimerRepository) -> None:
        keep = repo.create_countdown(DAY, "Keep", 5)
        drop = repo.create_countdown(DAY, "Drop", 5)
        repo.delete(drop.id)
        assert [t.id for t in repo.list_for_day(DAY)] == [keep.id]


class TestNotificationDedup:
    def test_marking_notified_persists(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        repo.mark_notified(timer.id, at())
        assert repo.get(timer.id).notified_at == at()

    def test_notified_at_survives_a_restart(
        self, repo: MarkdownTimerRepository, vault: Vault
    ) -> None:
        """A notification raised before a close must not be raised again."""
        timer = repo.create_countdown(DAY, "Focus", 25)
        repo.mark_notified(timer.id, at(minutes=1))

        # A brand new repository over the same vault is what a relaunch is.
        reopened = MarkdownTimerRepository(vault, FixedClock(at(hours=2)))
        assert reopened.get(timer.id).notified_at == at(minutes=1)

    def test_dedup_holds_across_a_restart(
        self, repo: MarkdownTimerRepository, vault: Vault
    ) -> None:
        from nodify.domain.reminder_engine import ReminderEngine

        timer = repo.create_countdown(DAY, "Focus", 25)
        repo.mark_notified(timer.id, at(minutes=30))

        engine = ReminderEngine(FixedClock(at(hours=2)))
        pending = MarkdownTimerRepository(vault, FixedClock(at(hours=2))).all_pending()
        assert engine.due_for_notification(pending) == []

    def test_unnotified_due_timer_is_eligible(self, repo: MarkdownTimerRepository) -> None:
        from nodify.domain.reminder_engine import ReminderEngine

        timer = repo.create_countdown(DAY, "Focus", 25)
        engine = ReminderEngine(FixedClock(at(hours=2)))
        assert [t.id for t in engine.due_for_notification(repo.all_pending())] == [timer.id]


class TestSnooze:
    def test_pushes_the_due_time_back(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        snoozed = repo.snooze(timer.id, 10, at=at(hours=2))
        assert snoozed.due_at == at(hours=2, minutes=10)

    def test_clears_notified_at_so_it_fires_again(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        repo.mark_notified(timer.id, at())
        snoozed = repo.snooze(timer.id, 10, at=at(hours=2))
        assert snoozed.notified_at is None

    def test_re_arms_a_dismissed_timer(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        repo.dismiss(timer.id)
        assert repo.snooze(timer.id, 10, at=at(hours=2)).status is TimerStatus.SCHEDULED

    def test_a_zero_snooze_is_rejected(self, repo: MarkdownTimerRepository) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        with pytest.raises(VaultError, match="at least one minute"):
            repo.snooze(timer.id, 0, at=at())

    def test_snoozing_an_overdue_timer_uses_now_as_the_base(
        self, repo: MarkdownTimerRepository
    ) -> None:
        timer = repo.create_countdown(DAY, "Focus", 25)
        snoozed = repo.snooze(timer.id, 10, at=at(hours=2))
        # due_at was 25 minutes after NOW, which is in the past by now.
        assert snoozed.due_at == at(hours=2, minutes=10)


class TestEditingOneTimerLeavesOthersIntact:
    def test_a_sibling_is_not_rewritten(self, repo: MarkdownTimerRepository, vault: Vault) -> None:
        path = vault.root / "timer" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntimers:\n"
            "  - id: timer_hand\n    title: Handwritten\n"
            "  - id: timer_managed\n    title: Managed\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )
        repo.update("timer_managed", title="Managed edited")

        from nodify.adapters.day_file import TimerDayFile

        day_file = TimerDayFile.load(vault.documents, vault.root, DAY)
        assert set(day_file.record_by_title("Handwritten") or {}) == {"id", "title"}
