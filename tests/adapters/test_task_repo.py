"""Task persistence and the schedule-boundary views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nodify.adapters.day_file import DayFile
from nodify.adapters.task_repo import MarkdownTaskRepository
from nodify.adapters.vault import Vault
from nodify.domain.clock import FixedClock
from nodify.domain.documents import Schedule, TaskStatus
from nodify.domain.ports import DocumentNotFoundError, VaultError

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
DAY = datetime(2026, 9, 25, tzinfo=UTC)
TOMORROW = datetime(2026, 9, 26, tzinfo=UTC)


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
def repo(vault: Vault, clock: FixedClock) -> MarkdownTaskRepository:
    return MarkdownTaskRepository(vault, clock)


class TestCreate:
    def test_creates_a_day_file(self, repo: MarkdownTaskRepository, vault: Vault) -> None:
        repo.create(DAY, "Send the invoice")
        assert (vault.root / "todos" / "2026-09" / "25-2026.md").is_file()

    def test_returns_the_task(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Send the invoice")
        assert task.title == "Send the invoice"
        assert task.id.startswith("task_")
        assert not task.is_done

    def test_ids_are_unique(self, repo: MarkdownTaskRepository) -> None:
        ids = {repo.create(DAY, f"Task {i}").id for i in range(20)}
        assert len(ids) == 20

    def test_several_tasks_share_the_day_file(self, repo: MarkdownTaskRepository) -> None:
        for index in range(4):
            repo.create(DAY, f"Task {index}")
        assert len(repo.list_for_day(DAY)) == 4

    def test_blank_title_is_rejected(self, repo: MarkdownTaskRepository, vault: Vault) -> None:
        with pytest.raises(VaultError, match="needs a title"):
            repo.create(DAY, "   ")
        assert not (vault.root / "todos" / "2026-09" / "25-2026.md").exists()

    def test_separate_days_get_separate_files(
        self, repo: MarkdownTaskRepository, vault: Vault
    ) -> None:
        repo.create(DAY, "Today")
        repo.create(TOMORROW, "Tomorrow")
        assert (vault.root / "todos" / "2026-09" / "25-2026.md").is_file()
        assert (vault.root / "todos" / "2026-09" / "26-2026.md").is_file()

    def test_schedule_now_is_due_immediately(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Now", schedule=Schedule.NOW)
        assert task.due_at == NOW

    def test_schedule_soon_is_not_overdue(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Soon", schedule=Schedule.SOON)
        assert task.due_at is not None
        assert task.due_at > NOW

    def test_explicit_due_at_wins_over_the_window(self, repo: MarkdownTaskRepository) -> None:
        explicit = at(days=3)
        task = repo.create(DAY, "Pinned", schedule=Schedule.NOW, due_at=explicit)
        assert task.due_at == explicit

    def test_notes_are_stored(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "With notes", notes="ask about pricing")
        assert repo.get(task.id).notes == "ask about pricing"

    def test_a_naive_due_at_is_rejected(self, repo: MarkdownTaskRepository) -> None:
        with pytest.raises(VaultError, match="timezone-aware"):
            repo.create(DAY, "Bad", due_at=datetime(2026, 9, 26, 9))  # noqa: DTZ001


class TestRead:
    def test_get_by_id(self, repo: MarkdownTaskRepository) -> None:
        created = repo.create(DAY, "Findable")
        assert repo.get(created.id).title == "Findable"

    def test_get_unknown_id_raises(self, repo: MarkdownTaskRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.get("task_nope")

    def test_finds_a_task_in_another_day(self, repo: MarkdownTaskRepository) -> None:
        created = repo.create(TOMORROW, "Tomorrow")
        assert repo.get(created.id).title == "Tomorrow"

    def test_empty_vault(self, repo: MarkdownTaskRepository) -> None:
        assert repo.list_for_day(DAY) == []
        assert repo.all_tasks() == []

    def test_ignores_a_malformed_day_file(self, repo: MarkdownTaskRepository, vault: Vault) -> None:
        repo.create(DAY, "Good")
        path = vault.root / "todos" / "2026-09" / "26-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: [unclosed\n---\nbody\n", encoding="utf-8")

        assert len(repo.all_tasks()) == 1
        assert repo.get(repo.all_tasks()[0].id).title == "Good"

    def test_ignores_a_stray_markdown_file(
        self, repo: MarkdownTaskRepository, vault: Vault
    ) -> None:
        repo.create(DAY, "Good")
        path = vault.root / "todos" / "2026-09" / "notes.md"
        path.write_text("---\nid: day_x\n---\nbody\n", encoding="utf-8")
        assert len(repo.all_tasks()) == 1

    def test_ignores_a_non_standard_month_folder(
        self, repo: MarkdownTaskRepository, vault: Vault
    ) -> None:
        repo.create(DAY, "Good")
        path = vault.root / "todos" / "archive" / "01-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: day_x\ntasks: []\n---\nbody\n", encoding="utf-8")
        assert len(repo.all_tasks()) == 1


class TestComplete:
    def test_marks_done(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Finish")
        done = repo.complete(task.id, at(hours=1))
        assert done.status is TaskStatus.DONE
        assert done.is_done

    def test_records_the_completion_time(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Finish")
        done = repo.complete(task.id, at(hours=1))
        assert done.completed_at == at(hours=1)

    def test_appends_to_the_history(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Finish")
        repo.complete(task.id, at(hours=1))
        reopened = repo.reopen(task.id)
        done_again = repo.complete(task.id, at(hours=2))
        assert len(done_again.completion_history) == 2
        assert reopened.completion_history

    def test_completing_twice_at_the_same_moment_records_once(
        self, repo: MarkdownTaskRepository
    ) -> None:
        task = repo.create(DAY, "Finish")
        repo.complete(task.id, at(hours=1))
        done = repo.complete(task.id, at(hours=1))
        assert len(done.completion_history) == 1

    def test_state_survives_a_reload(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Finish")
        repo.complete(task.id, at(hours=1))
        assert repo.get(task.id).is_done

    def test_reopen(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Finish")
        repo.complete(task.id, at(hours=1))
        reopened = repo.reopen(task.id)
        assert not reopened.is_done
        assert reopened.completed_at is None

    def test_reopen_keeps_the_history(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Finish")
        repo.complete(task.id, at(hours=1))
        reopened = repo.reopen(task.id)
        assert len(reopened.completion_history) == 1

    def test_completing_an_unknown_task_raises(self, repo: MarkdownTaskRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.complete("task_nope", at())


class TestUpdate:
    def test_changes_the_title(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Before")
        assert repo.update(task.id, title="After").title == "After"

    def test_changes_the_notes(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Before", notes="old")
        assert repo.update(task.id, notes="new").notes == "new"

    def test_leaves_other_fields_alone(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Title", notes="keep me")
        updated = repo.update(task.id, title="Renamed")
        assert updated.notes == "keep me"
        assert updated.due_at == task.due_at

    def test_blank_title_is_rejected(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Before")
        with pytest.raises(VaultError, match="needs a title"):
            repo.update(task.id, title="  ")
        assert repo.get(task.id).title == "Before"

    def test_coerces_a_schedule_string(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Task")
        assert repo.update(task.id, schedule="week").schedule is Schedule.WEEK

    def test_bumps_updated_at(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Task")
        assert repo.update(task.id, title="New").updated_at is not None

    def test_unknown_field_is_ignored(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Task")
        assert repo.update(task.id, nonexistent="x").title == "Task"


class TestDelete:
    def test_removes_the_task(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Doomed")
        repo.delete(task.id)
        with pytest.raises(DocumentNotFoundError):
            repo.get(task.id)

    def test_leaves_siblings(self, repo: MarkdownTaskRepository) -> None:
        keep = repo.create(DAY, "Keep")
        drop = repo.create(DAY, "Drop")
        repo.delete(drop.id)
        assert [t.id for t in repo.list_for_day(DAY)] == [keep.id]

    def test_deleting_from_a_multi_task_day_keeps_the_file(
        self, repo: MarkdownTaskRepository
    ) -> None:
        repo.create(DAY, "One")
        repo.create(DAY, "Two")
        drop = repo.create(DAY, "Three")
        repo.delete(drop.id)
        assert len(repo.list_for_day(DAY)) == 2

    def test_unknown_id_raises(self, repo: MarkdownTaskRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.delete("task_nope")


class TestMove:
    def test_moves_to_another_day(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Defer me")
        repo.move(task.id, TOMORROW)
        assert repo.list_for_day(DAY) == []
        assert [t.id for t in repo.list_for_day(TOMORROW)] == [task.id]

    def test_keeps_the_id_and_history(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Defer me")
        repo.complete(task.id, at(hours=1))
        moved = repo.move(task.id, TOMORROW)
        assert moved.id == task.id
        assert len(moved.completion_history) == 1

    def test_moving_to_the_same_day_is_a_no_op(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Stay")
        repo.move(task.id, DAY)
        assert len(repo.list_for_day(DAY)) == 1

    def test_unknown_id_raises(self, repo: MarkdownTaskRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.move("task_nope", TOMORROW)


class TestEditingOneTaskLeavesOthersIntact:
    def test_a_sibling_is_not_normalised(self, repo: MarkdownTaskRepository, vault: Vault) -> None:
        """Editing one task must not rewrite the others.

        This is the plan's acceptance criterion for the day-file contract, and the
        reason ``DayFile`` detaches its records: a task handed out by the
        repository must not alias the stored mapping. A hand-written record with
        only two keys is the strictest form of the check, because any default the
        mapper would add shows up immediately.
        """
        path = vault.root / "todos" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntasks:\n"
            "  - id: task_handwritten\n    title: Handwritten\n"
            "  - id: task_managed\n    title: Managed\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )

        repo.update("task_managed", title="Managed edited")

        day_file = DayFile.load(vault.documents, vault.root, DAY)
        untouched = day_file.record_by_title("Handwritten")
        assert untouched is not None
        assert set(untouched) == {"id", "title"}

    def test_sibling_content_is_unchanged(self, repo: MarkdownTaskRepository) -> None:
        repo.create(DAY, "First", notes="do not lose me")
        second = repo.create(DAY, "Second")
        repo.update(second.id, title="Second edited")
        remaining = {t.title: t for t in repo.list_for_day(DAY)}
        assert remaining["First"].notes == "do not lose me"

    def test_editing_keeps_a_task_in_place(self, repo: MarkdownTaskRepository) -> None:
        """An edit must not reorder the day.

        remove-then-add would move the task to the bottom, so the list the user is
        looking at would reshuffle under them for no reason.
        """
        first = repo.create(DAY, "First")
        repo.create(DAY, "Second")
        repo.create(DAY, "Third")

        repo.update(first.id, title="First edited")
        assert [t.title for t in repo.list_for_day(DAY)] == [
            "First edited",
            "Second",
            "Third",
        ]


class TestViews:
    def test_open_tasks_excludes_completed(self, repo: MarkdownTaskRepository) -> None:
        keep = repo.create(DAY, "Open")
        drop = repo.create(DAY, "Done")
        repo.complete(drop.id, at())
        assert [t.id for t in repo.open_tasks()] == [keep.id]

    def test_completed_for_a_day(self, repo: MarkdownTaskRepository) -> None:
        task = repo.create(DAY, "Done")
        repo.complete(task.id, at())
        assert [t.id for t in repo.completed(DAY)] == [task.id]

    def test_overdue_is_most_overdue_first(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(at(hours=1))
        repo.create(DAY, "Slightly late", due_at=at(hours=0, minutes=-10))
        repo.create(DAY, "Very late", due_at=at(days=-2))
        clock.set(at(hours=1))

        assert [t.title for t in repo.overdue(now=at(hours=1))] == [
            "Very late",
            "Slightly late",
        ]

    def test_overdue_excludes_future_and_due_now(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        repo.create(DAY, "Future", due_at=at(hours=1))
        repo.create(DAY, "Due now", due_at=NOW)
        assert repo.overdue(now=NOW) == []

    def test_upcoming_is_soonest_first(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        repo.create(DAY, "Later", due_at=at(hours=5))
        repo.create(DAY, "Sooner", due_at=at(hours=1))
        assert [t.title for t in repo.upcoming(now=NOW)] == ["Sooner", "Later"]

    def test_upcoming_excludes_beyond_the_horizon(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        repo.create(DAY, "Far", due_at=at(days=30))
        assert repo.upcoming(now=NOW) == []

    def test_upcoming_includes_undated_tasks(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        task = repo.create(DAY, "Undated")
        task.due_at = None
        repo.update(task.id, due_at=None)
        assert [t.id for t in repo.upcoming(now=NOW)] == [task.id]

    def test_upcoming_excludes_completed(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        task = repo.create(DAY, "Done", due_at=at(hours=1))
        repo.complete(task.id, at())
        assert repo.upcoming(now=NOW) == []

    def test_needs_attention_is_overdue_then_due(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        repo.create(DAY, "Due now", due_at=NOW)
        repo.create(DAY, "Late", due_at=at(hours=-1))
        repo.create(DAY, "Later", due_at=at(hours=3))

        assert [t.title for t in repo.needs_attention(now=NOW)] == ["Late", "Due now"]

    def test_needs_attention_excludes_completed(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        task = repo.create(DAY, "Done", due_at=at(hours=-1))
        repo.complete(task.id, at())
        assert repo.needs_attention(now=NOW) == []

    def test_undated_tasks_sort_last(self, repo: MarkdownTaskRepository, clock: FixedClock) -> None:
        clock.set(NOW)
        dated = repo.create(DAY, "Dated", due_at=at(hours=2))
        undated = repo.create(DAY, "Undated")
        repo.update(undated.id, due_at=None)
        ordered = repo.upcoming(now=NOW)
        assert [t.id for t in ordered][-1] == undated.id
        assert ordered[0].id == dated.id


class TestBoundaryTimes:
    def test_a_task_due_exactly_now_is_not_overdue(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        repo.create(DAY, "Edge", due_at=NOW)
        assert repo.overdue(now=NOW) == []
        assert len(repo.needs_attention(now=NOW)) == 1

    def test_a_task_due_one_second_ago_is_overdue(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        repo.create(DAY, "Edge", due_at=at(seconds=-1))
        assert len(repo.overdue(now=NOW)) == 1

    def test_upcoming_horizon_is_inclusive(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        repo.create(DAY, "Edge", due_at=at(days=7))
        assert len(repo.upcoming(now=NOW)) == 1

    def test_upcoming_horizon_excludes_one_second_past(
        self, repo: MarkdownTaskRepository, clock: FixedClock
    ) -> None:
        clock.set(NOW)
        repo.create(DAY, "Edge", due_at=at(days=7, seconds=1))
        assert repo.upcoming(now=NOW) == []
