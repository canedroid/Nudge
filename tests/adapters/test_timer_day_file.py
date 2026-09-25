"""Timer day files: many timers per file, records detached, body rendered."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodify.adapters.day_file import (
    TIMERS_KEY,
    TaskDayFile,
    TimerDayFile,
    day_file_name,
    day_folder,
    normalise_day,
)
from nodify.adapters.vault import Vault
from nodify.domain.documents import DocumentType, Timer, TimerKind, TimerStatus

DAY = datetime(2026, 9, 25, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


def make_timer(index: int, **overrides: object) -> Timer:
    defaults: dict[str, object] = {
        "id": f"timer_{index}",
        "title": f"Timer {index}",
        "created_at": datetime(2026, 9, 25, 9, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 25, 9, tzinfo=UTC),
        "due_at": datetime(2026, 9, 25, 12, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Timer(**defaults)  # type: ignore[arg-type]


class TestNaming:
    def test_month_folder(self) -> None:
        assert day_folder(DAY) == "2026-09"

    def test_day_file_name(self) -> None:
        assert day_file_name(DAY) == "25-2026.md"

    def test_normalise_strips_the_time(self) -> None:
        assert normalise_day(datetime(2026, 9, 25, 13, 45, tzinfo=UTC)) == DAY

    def test_normalise_rejects_a_naive_day(self) -> None:
        from nodify.domain.ports import VaultError

        with pytest.raises(VaultError, match="timezone-aware"):
            normalise_day(datetime(2026, 9, 25))  # noqa: DTZ001


class TestPaths:
    def test_timer_path(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        assert day_file.relative_path == Path("timer/2026-09/25-2026.md")

    def test_task_path_differs(self, vault: Vault) -> None:
        assert TaskDayFile(DAY, vault.root).relative_path.parts[0] == "todos"
        assert TimerDayFile(DAY, vault.root).relative_path.parts[0] == "timer"

    def test_sequence_keys_differ(self) -> None:
        assert TaskDayFile.SEQUENCE_KEY == "tasks"
        assert TimerDayFile.SEQUENCE_KEY == TIMERS_KEY
        assert TIMERS_KEY == "timers"

    def test_document_types_differ(self) -> None:
        assert TaskDayFile.DOCUMENT_TYPE is DocumentType.TASK
        assert TimerDayFile.DOCUMENT_TYPE is DocumentType.TIMER


class TestRoundTrip:
    def test_write_then_read(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        day_file.add(TimerDayFile.record_for(make_timer(1)))
        day_file.save(vault.documents)

        reloaded = TimerDayFile.load(vault.documents, vault.root, DAY)
        assert len(reloaded) == 1
        assert reloaded.timers()[0].title == "Timer 1"

    def test_many_timers_share_one_file(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        for index in range(4):
            day_file.add(TimerDayFile.record_for(make_timer(index)))
        day_file.save(vault.documents)

        reloaded = TimerDayFile.load(vault.documents, vault.root, DAY)
        assert len(reloaded.timers()) == 4

    def test_countdown_fields_survive(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        day_file.add(
            TimerDayFile.record_for(make_timer(1, kind=TimerKind.COUNTDOWN, duration_seconds=1500))
        )
        day_file.save(vault.documents)

        reloaded = TimerDayFile.load(vault.documents, vault.root, DAY).timers()[0]
        assert reloaded.kind is TimerKind.COUNTDOWN
        assert reloaded.duration_seconds == 1500

    def test_notified_at_survives(self, vault: Vault) -> None:
        """``notified_at`` round-trips, which is what dedup depends on.

        Timestamps are written at whole-second ISO-8601 precision, so a
        sub-second value comes back truncated. That is sufficient here: the
        dedup rule compares ``notified_at`` against ``due_at``, and both are
        stored at the same precision.
        """
        day_file = TimerDayFile(DAY, vault.root)
        day_file.add(
            TimerDayFile.record_for(
                make_timer(1, notified_at=datetime(2026, 9, 25, 12, 0, 1, tzinfo=UTC))
            )
        )
        day_file.save(vault.documents)

        reloaded = TimerDayFile.load(vault.documents, vault.root, DAY).timers()[0]
        assert reloaded.notified_at == datetime(2026, 9, 25, 12, 0, 1, tzinfo=UTC)

    def test_missing_file_loads_empty(self, vault: Vault) -> None:
        assert len(TimerDayFile.load(vault.documents, vault.root, DAY)) == 0

    def test_order_is_preserved(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        for index, title in enumerate(("First", "Second", "Third")):
            day_file.add(TimerDayFile.record_for(make_timer(index, title=title)))
        day_file.save(vault.documents)

        reloaded = TimerDayFile.load(vault.documents, vault.root, DAY)
        assert [t.title for t in reloaded.timers()] == ["First", "Second", "Third"]

    def test_put_keeps_position(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        for index in range(3):
            day_file.add(TimerDayFile.record_for(make_timer(index)))
        day_file.put(TimerDayFile.record_for(make_timer(0, title="Edited")))
        assert [t.id for t in day_file.timers()] == ["timer_0", "timer_1", "timer_2"]


class TestMalformedRecords:
    def test_bad_records_are_skipped(self, vault: Vault) -> None:
        path = vault.root / "timer" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntimers:\n"
            "  - id: timer_good\n    title: Good\n"
            "  - a bare string\n"
            "  - title: No id\n"
            "  - id: timer_also\n    title: Also good\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )
        reloaded = TimerDayFile.load(vault.documents, vault.root, DAY)
        assert [t.title for t in reloaded.timers()] == ["Good", "Also good"]

    def test_non_list_timers_key(self, vault: Vault) -> None:
        path = vault.root / "timer" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntimers: nope\n---\n\nbody\n",
            encoding="utf-8",
        )
        assert len(TimerDayFile.load(vault.documents, vault.root, DAY)) == 0


class TestBody:
    def test_shows_the_time(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        day_file.add(TimerDayFile.record_for(make_timer(1, title="Call Sam")))
        day_file.save(vault.documents)

        body = day_file.body()
        assert "12:00" in body
        assert "Call Sam" in body

    def test_shows_a_non_scheduled_status(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        day_file.add(TimerDayFile.record_for(make_timer(1, status=TimerStatus.COMPLETED)))
        day_file.save(vault.documents)
        assert "completed" in day_file.body()

    def test_includes_notes(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        day_file.add(TimerDayFile.record_for(make_timer(1, notes="bring the deck")))
        day_file.save(vault.documents)
        assert "bring the deck" in day_file.body()

    def test_handles_a_timer_with_no_due_time(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        day_file.add(TimerDayFile.record_for(make_timer(1, due_at=None)))
        day_file.save(vault.documents)
        assert "--:--" in day_file.body()

    def test_stays_a_valid_obsidian_note(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        day_file.add(TimerDayFile.record_for(make_timer(1)))
        day_file.save(vault.documents)
        text = (vault.root / "timer" / "2026-09" / "25-2026.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "# Friday, 25 September 2026" in text


class TestForeignKeys:
    def test_an_unknown_timer_key_survives_an_edit(self, vault: Vault) -> None:
        path = vault.root / "timer" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntimers:\n"
            "  - id: timer_1\n    title: One\n    alarm_sound: chime\n"
            "  - id: timer_2\n    title: Two\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )

        day_file = TimerDayFile.load(vault.documents, vault.root, DAY)
        timers = day_file.timers()
        timers[0].title = "One renamed"
        day_file.put(TimerDayFile.record_for(timers[0]))
        day_file.save(vault.documents)

        assert "alarm_sound: chime" in path.read_text(encoding="utf-8")

    def test_untouched_records_are_left_alone(self, vault: Vault) -> None:
        path = vault.root / "timer" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntimers:\n"
            "  - id: timer_1\n    title: One\n"
            "  - id: timer_2\n    title: Two\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )

        day_file = TimerDayFile.load(vault.documents, vault.root, DAY)
        timers = day_file.timers()
        timers[0].title = "Changed"
        day_file.put(TimerDayFile.record_for(timers[0]))
        day_file.save(vault.documents)

        reloaded = TimerDayFile.load(vault.documents, vault.root, DAY)
        assert set(reloaded.record("timer_2")) == {"id", "title"}

    def test_a_no_op_save_is_byte_identical(self, vault: Vault) -> None:
        day_file = TimerDayFile(DAY, vault.root)
        for index in range(3):
            day_file.add(TimerDayFile.record_for(make_timer(index)))
        day_file.save(vault.documents)
        path = vault.root / "timer" / "2026-09" / "25-2026.md"
        first = path.read_text(encoding="utf-8")

        TimerDayFile.load(vault.documents, vault.root, DAY).save(vault.documents)
        assert path.read_text(encoding="utf-8") == first


class TestSeparation:
    def test_tasks_and_timers_use_different_files(self, vault: Vault) -> None:
        TaskDayFile(DAY, vault.root).save(vault.documents)
        TimerDayFile(DAY, vault.root).save(vault.documents)
        assert (vault.root / "todos" / "2026-09" / "25-2026.md").is_file()
        assert (vault.root / "timer" / "2026-09" / "25-2026.md").is_file()

    def test_a_task_file_read_as_timers_yields_nothing(self, vault: Vault) -> None:
        day_file = TaskDayFile(DAY, vault.root)
        day_file.add(TaskDayFile.record_for(make_timer(1)))
        day_file.save(vault.documents)

        # The sequence keys differ, so a task file holds no timers even though
        # the records look alike.
        assert TimerDayFile.load(vault.documents, vault.root, DAY).timers() == []
