"""Vault file enumeration, day derivation and change detection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodify.adapters.file_index import FileSystemFileIndex, parse_day_from_path
from nodify.domain.ports import FileEntry


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for area in ("notes", "todos", "timer"):
        (root / area).mkdir(parents=True)
    return root


@pytest.fixture
def index(vault: Path) -> FileSystemFileIndex:
    return FileSystemFileIndex(vault)


def touch(path: Path, content: str = "---\nid: x\n---\nbody\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestDayDerivation:
    def test_parses_conventional_day_file(self) -> None:
        path = Path("todos/2026-09/25-2026.md")
        assert parse_day_from_path(path) == datetime(2026, 9, 25, tzinfo=UTC)

    def test_parses_single_digit_day(self) -> None:
        assert parse_day_from_path(Path("timer/2026-09/05-2026.md")) == datetime(
            2026, 9, 5, tzinfo=UTC
        )

    def test_rejects_wrong_depth(self) -> None:
        assert parse_day_from_path(Path("25-2026.md")) is None
        assert parse_day_from_path(Path("todos/25-2026.md")) is None

    def test_rejects_unknown_area(self) -> None:
        assert parse_day_from_path(Path("journal/2026-09/25-2026.md")) is None

    def test_rejects_bad_month_folder(self) -> None:
        assert parse_day_from_path(Path("todos/2026-13/25-2026.md")) is None
        assert parse_day_from_path(Path("todos/2026-9/25-2026.md")) is None

    def test_rejects_impossible_day(self) -> None:
        assert parse_day_from_path(Path("todos/2026-02/31-2026.md")) is None
        assert parse_day_from_path(Path("todos/2026-02/00-2026.md")) is None

    def test_rejects_malformed_names(self) -> None:
        assert parse_day_from_path(Path("todos/2026-09/week-2026.md")) is None
        assert parse_day_from_path(Path("todos/2026-09/25-26.md")) is None


class TestRefresh:
    def test_empty_vault_lists_nothing(self, index: FileSystemFileIndex) -> None:
        assert index.refresh() == []

    def test_lists_markdown_only(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "notes" / "A.md")
        (index.root / "notes" / "B.txt").write_text("x", encoding="utf-8")
        assert [e.name for e in index.refresh()] == ["A"]

    def test_recurses_into_nested_folders(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "notes" / "Work" / "Deep" / "A.md")
        assert len(index.refresh()) == 1

    def test_reports_area_and_category(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "notes" / "Work" / "A.md")
        entry = index.refresh()[0]
        assert entry.area == "notes"
        assert entry.category == "Work"
        assert entry.day is None

    def test_reports_day_for_day_files(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "todos" / "2026-09" / "25-2026.md")
        entry = index.refresh()[0]
        assert entry.day == datetime(2026, 9, 25, tzinfo=UTC)

    def test_sorted_by_area_then_path(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "timer" / "2026-09" / "25-2026.md")
        touch(index.root / "notes" / "Zeta.md")
        touch(index.root / "notes" / "alpha.md")
        # Compared as parts, not as a string, so the expectation is not written
        # with the wrong separator for the platform.
        assert [e.relative_path.parts for e in index.refresh()] == [
            ("notes", "alpha.md"),
            ("notes", "Zeta.md"),
            ("timer", "2026-09", "25-2026.md"),
        ]

    def test_reports_size_and_mtime(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "notes" / "A.md", "x" * 50)
        entry = index.refresh()[0]
        assert entry.size >= 50
        assert entry.modified_at.tzinfo is UTC

    def test_ignores_unreadable_directory(
        self, index: FileSystemFileIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        touch(index.root / "notes" / "A.md")

        def deny(self: Path) -> list[Path]:
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "iterdir", deny)
        assert index.refresh() == []

    def test_missing_area_is_skipped(self, vault: Path) -> None:
        (vault / "todos").rmdir()
        index = FileSystemFileIndex(vault)
        assert index.refresh() == []

    def test_vault_root_absent(self, tmp_path: Path) -> None:
        assert FileSystemFileIndex(tmp_path / "nope").refresh() == []


def entry_for(path: Path, size: int = 10) -> FileEntry:
    return FileEntry(
        relative_path=path,
        name=path.stem,
        area="notes",
        category=None,
        day=None,
        size=size,
        modified_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


class TestChangesSince:
    def test_no_changes(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "notes" / "A.md")
        before = index.refresh()
        assert index.changes_since(before) == type(index.changes_since(before))()

    def test_detects_addition(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "notes" / "A.md")
        before = index.refresh()
        touch(index.root / "notes" / "B.md")
        changes = index.changes_since(before)
        assert [e.name for e in changes.added] == ["B"]
        assert changes.modified == ()

    def test_detects_removal(self, index: FileSystemFileIndex) -> None:
        a = touch(index.root / "notes" / "A.md")
        touch(index.root / "notes" / "B.md")
        before = index.refresh()
        a.unlink()
        changes = index.changes_since(before)
        assert [p.name for p in changes.removed] == ["A.md"]

    def test_detects_modification_by_size(self, index: FileSystemFileIndex) -> None:
        path = touch(index.root / "notes" / "A.md", "small")
        before = index.refresh()
        path.write_text("x" * 500, encoding="utf-8")
        changes = index.changes_since(before)
        assert [e.name for e in changes.modified] == ["A"]

    def test_empty_baseline_reports_everything_as_added(self, index: FileSystemFileIndex) -> None:
        touch(index.root / "notes" / "A.md")
        changes = index.changes_since([])
        assert [e.name for e in changes.added] == ["A"]
        assert changes.removed == ()
