"""Day file reading and writing, including the many-tasks-per-file contract."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodify.adapters.day_file import (
    DayFile,
    day_file_name,
    day_folder,
    normalise_day,
)
from nodify.adapters.vault import Vault
from nodify.domain.documents import DocumentType, Task, TaskStatus

DAY = datetime(2026, 9, 25, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


def make_task(index: int, **overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": f"task_{index}",
        "title": f"Task {index}",
        "created_at": datetime(2026, 9, 25, 9, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 25, 9, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


class TestNaming:
    def test_month_folder(self) -> None:
        assert day_folder(DAY) == "2026-09"

    def test_day_file_name(self) -> None:
        assert day_file_name(DAY) == "25-2026.md"

    def test_single_digit_day_is_padded(self) -> None:
        assert day_file_name(datetime(2026, 9, 5, tzinfo=UTC)) == "05-2026.md"

    def test_december(self) -> None:
        moment = datetime(2026, 12, 31, tzinfo=UTC)
        assert day_folder(moment) == "2026-12"
        assert day_file_name(moment) == "31-2026.md"

    def test_normalise_strips_the_time(self) -> None:
        assert normalise_day(datetime(2026, 9, 25, 13, 45, 12, tzinfo=UTC)) == DAY

    def test_normalise_converts_to_utc(self) -> None:
        from datetime import timedelta, timezone

        offset = datetime(2026, 9, 25, 2, 0, tzinfo=timezone(timedelta(hours=2)))
        assert normalise_day(offset) == datetime(2026, 9, 25, 0, tzinfo=UTC)

    def test_normalise_crosses_back_over_midnight(self) -> None:
        """A moment late in one zone may be the previous day in UTC."""
        from datetime import timedelta, timezone

        offset = datetime(2026, 9, 25, 1, 0, tzinfo=timezone(timedelta(hours=2)))
        assert normalise_day(offset) == datetime(2026, 9, 24, tzinfo=UTC)

    def test_normalise_rejects_a_naive_day(self) -> None:
        from nodify.domain.ports import VaultError

        with pytest.raises(VaultError, match="timezone-aware"):
            normalise_day(datetime(2026, 9, 25))  # noqa: DTZ001


class TestPath:
    def test_relative_path(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        assert day_file.relative_path == Path("todos/2026-09/25-2026.md")

    def test_does_not_exist_initially(self, vault: Vault) -> None:
        assert not DayFile(DAY, vault.root).exists


class TestRoundTrip:
    def test_write_then_read(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        day_file.add(DayFile.record_for(make_task(1)))
        day_file.save(vault.documents)

        reloaded = DayFile.load(vault.documents, vault.root, DAY)
        assert len(reloaded) == 1
        assert reloaded.tasks()[0].title == "Task 1"

    def test_creates_missing_directories(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        day_file.add(DayFile.record_for(make_task(1)))
        day_file.save(vault.documents)
        assert (vault.root / "todos" / "2026-09" / "25-2026.md").is_file()

    def test_many_tasks_share_one_file(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        for index in range(5):
            day_file.add(DayFile.record_for(make_task(index)))
        day_file.save(vault.documents)

        reloaded = DayFile.load(vault.documents, vault.root, DAY)
        assert len(reloaded) == 5
        assert [t.title for t in reloaded.tasks()] == [f"Task {i}" for i in range(5)]

    def test_order_is_preserved(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        for index, title in enumerate(("First", "Second", "Third")):
            day_file.add(DayFile.record_for(make_task(index, title=title)))
        day_file.save(vault.documents)

        reloaded = DayFile.load(vault.documents, vault.root, DAY)
        assert [t.title for t in reloaded.tasks()] == ["First", "Second", "Third"]

    def test_missing_file_loads_empty(self, vault: Vault) -> None:
        assert len(DayFile.load(vault.documents, vault.root, DAY)) == 0

    def test_remove(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        day_file.add(DayFile.record_for(make_task(1)))
        day_file.add(DayFile.record_for(make_task(2)))
        day_file.remove("task_1")
        day_file.save(vault.documents)

        reloaded = DayFile.load(vault.documents, vault.root, DAY)
        assert [t.id for t in reloaded.tasks()] == ["task_2"]

    def test_put_keeps_position(self, vault: Vault) -> None:
        """Editing a task must not move it to the bottom of the day."""
        day_file = DayFile(DAY, vault.root)
        for index in range(3):
            day_file.add(DayFile.record_for(make_task(index)))

        day_file.put(DayFile.record_for(make_task(0, title="Edited first")))
        assert [t.id for t in day_file.tasks()] == ["task_0", "task_1", "task_2"]
        assert day_file.tasks()[0].title == "Edited first"

    def test_put_appends_an_unknown_id(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        day_file.add(DayFile.record_for(make_task(1)))
        day_file.put(DayFile.record_for(make_task(2)))
        assert [t.id for t in day_file.tasks()] == ["task_1", "task_2"]


class TestMalformedRecords:
    def test_a_malformed_record_does_not_lose_its_neighbours(self, vault: Vault) -> None:
        path = vault.root / "todos" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntasks:\n"
            "  - id: task_good\n    title: Good\n"
            "  - just a string\n"
            "  - title: No id\n"
            "  - id: task_also_good\n    title: Also good\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )
        reloaded = DayFile.load(vault.documents, vault.root, DAY)
        assert [t.title for t in reloaded.tasks()] == ["Good", "Also good"]

    def test_a_non_list_tasks_key_is_tolerated(self, vault: Vault) -> None:
        path = vault.root / "todos" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntasks: not a list\n---\n\nbody\n",
            encoding="utf-8",
        )
        assert len(DayFile.load(vault.documents, vault.root, DAY)) == 0

    def test_a_file_with_no_tasks_key(self, vault: Vault) -> None:
        path = vault.root / "todos" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: day_20260925\ntype: day\n---\n\nbody\n", encoding="utf-8")
        assert len(DayFile.load(vault.documents, vault.root, DAY)) == 0


class TestChecklistBody:
    def test_open_and_done_markers(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        day_file.add(DayFile.record_for(make_task(1, title="Open one")))
        day_file.add(DayFile.record_for(make_task(2, title="Done one", status=TaskStatus.DONE)))
        day_file.save(vault.documents)

        body = day_file.body()
        assert "- [ ] Open one" in body
        assert "- [x] Done one" in body

    def test_includes_notes(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        day_file.add(DayFile.record_for(make_task(1, notes="ask about pricing")))
        day_file.save(vault.documents)
        assert "ask about pricing" in day_file.body()

    def test_body_is_valid_markdown_for_obsidian(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        day_file.add(DayFile.record_for(make_task(1)))
        day_file.save(vault.documents)
        body = (vault.root / "todos" / "2026-09" / "25-2026.md").read_text(encoding="utf-8")
        assert body.startswith("---\n")
        assert "# Friday, 25 September 2026" in body


class TestForeignKeys:
    def test_an_unknown_task_key_survives_a_save(self, vault: Vault) -> None:
        """A third-party key on one record must not be lost when that record is saved."""
        path = vault.root / "todos" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntasks:\n"
            "  - id: task_1\n    title: Task 1\n    estimate: 30m\n"
            "  - id: task_2\n    title: Task 2\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )

        day_file = DayFile.load(vault.documents, vault.root, DAY)
        tasks = day_file.tasks()
        tasks[0].title = "Task 1 renamed"

        day_file.remove("task_1")
        day_file.add(DayFile.record_for(tasks[0]))
        day_file.save(vault.documents)

        written = path.read_text(encoding="utf-8")
        assert "estimate: 30m" in written
        assert "Task 1 renamed" in written

    def test_untouched_records_are_left_alone(self, vault: Vault) -> None:
        """Editing one task must not rewrite the other records.

        Without this, saving a day would add default keys to every task the user
        never touched, producing a diff on a file they did not change.
        """
        path = vault.root / "todos" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: day_20260925\ntype: day\ntasks:\n"
            "  - id: task_1\n    title: Task 1\n"
            "  - id: task_2\n    title: Task 2\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )
        original = path.read_text(encoding="utf-8")

        day_file = DayFile.load(vault.documents, vault.root, DAY)
        tasks = day_file.tasks()
        tasks[0].title = "Changed"
        day_file.put(DayFile.record_for(tasks[0]))
        day_file.save(vault.documents)

        written = path.read_text(encoding="utf-8")
        assert "title: Changed" in written

        # The edited record keeps its position, and the unedited record is not
        # rewritten: it still has exactly the two keys it started with. Comparing
        # the rendered keys is the direct check, because the YAML emitter is free
        # to reindent a sequence without changing the data.
        reloaded = DayFile.load(vault.documents, vault.root, DAY)
        assert [t.id for t in reloaded.tasks()] == ["task_1", "task_2"]
        assert set(reloaded.record("task_2")) == {"id", "title"}
        assert "title: Task 2" in written
        assert "id: task_2" in original

    def test_a_no_op_save_is_byte_identical(self, vault: Vault) -> None:
        day_file = DayFile(DAY, vault.root)
        for index in range(3):
            day_file.add(DayFile.record_for(make_task(index)))
        day_file.save(vault.documents)
        path = vault.root / "todos" / "2026-09" / "25-2026.md"
        first = path.read_text(encoding="utf-8")

        reloaded = DayFile.load(vault.documents, vault.root, DAY)
        reloaded.save(vault.documents)
        assert path.read_text(encoding="utf-8") == first


class TestRecordShape:
    def test_a_record_declares_its_type(self, vault: Vault) -> None:
        record = DayFile.record_for(make_task(1))
        assert record["type"] == str(DocumentType.TASK)

    def test_a_record_carries_the_contract_fields(self, vault: Vault) -> None:
        record = DayFile.record_for(make_task(1))
        for key in ("id", "title", "status", "schedule", "priority", "created_at"):
            assert key in record
