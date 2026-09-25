"""Hardening: malformed files, locks, conflicts, and the no-data-loss guarantee.

Package K's definition of done is "no data-loss path exists in the supported
failure cases" and "unsupported or malformed files remain available for repair".
These tests are the evidence for both, driven against a real temporary vault
rather than mocks, because the guarantee is about what is on disk afterwards.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodify.adapters.vault import Vault, create
from nodify.domain.clock import FixedClock
from nodify.domain.ports import (
    CollisionError,
    DocumentFormatError,
    PathOutsideVaultError,
)
from nodify.services.diagnostics import (
    Diagnosis,
    FileStamp,
    Problem,
    Severity,
    VaultReport,
    WriteGuard,
    atomic_write_is_available,
    classify_vault_error,
    diagnose_path,
    diagnose_vault,
    is_locked,
    scan_for_malformed,
    surviving_files,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    create(root, initial_year_month="2026-09")
    instance = Vault(root)
    instance.open()
    return instance


def broken_file(vault: Vault, relative: str, text: str) -> Path:
    path = vault.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestNoDataLoss:
    def test_a_malformed_file_is_left_byte_for_byte(self, vault: Vault) -> None:
        """The core guarantee: a file Nodify cannot read is not written to."""
        original = "---\nid: [unclosed\ntype: note\n---\nMy precious text\n"
        path = broken_file(vault, "notes/Broken.md", original)
        before = path.read_bytes()

        diagnose_vault(vault.root)
        assert path.read_bytes() == before

    def test_a_non_utf8_file_is_left_alone(self, vault: Vault) -> None:
        path = vault.root / "notes" / "Legacy.md"
        payload = b"\xff\xfe\x00legacy content"
        path.write_bytes(payload)

        diagnose_vault(vault.root)
        assert path.read_bytes() == payload

    def test_broken_files_remain_available_for_repair(self, vault: Vault) -> None:
        first = broken_file(vault, "notes/Broken1.md", "---\nid: [a\n---\ntext\n")
        second = broken_file(vault, "notes/Broken2.md", "not even frontmatter\n")
        surviving = surviving_files(vault.root, [first, second])
        assert surviving == [first, second]

    def test_a_malformed_file_does_not_block_other_notes(self, vault: Vault) -> None:
        from nodify.adapters.note_repo import MarkdownNoteRepository

        repository = MarkdownNoteRepository(vault, FixedClock(NOW))
        good = repository.create("Good", "Work", "content\n")
        broken_file(vault, "notes/Broken.md", "---\nid: [x\n---\ntext\n")

        # The good note is unaffected by its broken neighbour.
        assert repository.get(good.id).title == "Good"
        assert [n.title for n in repository.list_notes()] == ["Good"]

    def test_a_failed_write_leaves_the_original(self, vault: Vault) -> None:
        original = "---\nid: n1\ntype: note\ntitle: Keep me\n---\nBody\n"
        path = broken_file(vault, "notes/Keep.md", original)
        before = path.read_bytes()

        class Unrepresentable:
            pass

        with pytest.raises(DocumentFormatError):
            vault.documents.write(path, {"id": "n1", "bad": Unrepresentable()}, "new\n")

        assert path.read_bytes() == before

    def test_a_refused_overwrite_leaves_both_files(self, vault: Vault) -> None:
        from nodify.adapters.note_repo import MarkdownNoteRepository

        repository = MarkdownNoteRepository(vault, FixedClock(NOW))
        first = repository.create("Shared", "Work", "first body\n")
        repository.create("Shared", "Work", "second body\n")

        assert repository.get(first.id).body == "first body\n"
        assert len(repository.list_notes("Work")) == 2


class TestDiagnosePath:
    def test_a_healthy_file_has_no_diagnosis(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/Good.md", "---\nid: n1\n---\nBody\n")
        assert diagnose_path(path) is None

    def test_a_non_utf8_file_is_reported(self, vault: Vault) -> None:
        path = vault.root / "notes" / "Legacy.md"
        path.write_bytes(b"\xff\xfe\x00bad")
        diagnosis = diagnose_path(path)
        assert diagnosis is not None
        assert diagnosis.problem is Problem.MALFORMED
        assert diagnosis.severity is Severity.WARNING

    def test_a_missing_file_has_no_diagnosis(self, vault: Vault) -> None:
        assert diagnose_path(vault.root / "notes" / "Gone.md") is None

    def test_a_non_markdown_file_is_ignored(self, vault: Vault) -> None:
        path = vault.root / "notes" / "image.png"
        path.write_bytes(b"\xff\xfe\x00")
        assert diagnose_path(path) is None

    def test_a_directory_is_ignored(self, vault: Vault) -> None:
        assert diagnose_path(vault.root / "notes" / "Sub") is None

    def test_a_malformed_file_is_only_a_warning(self, vault: Vault) -> None:
        """A warning, because the file is still there and still repairable."""
        path = vault.root / "notes" / "Legacy.md"
        path.write_bytes(b"\xff\xfe\x00bad")
        diagnosis = diagnose_path(path)
        assert diagnosis is not None
        assert not diagnosis.is_blocking


class TestDiagnoseVault:
    def test_a_clean_vault(self, vault: Vault) -> None:
        broken_file(vault, "notes/Good.md", "---\nid: n1\n---\nBody\n")
        report = diagnose_vault(vault.root)
        assert report.is_clean
        assert report.summary() == "No problems found."

    def test_reports_every_bad_file(self, vault: Vault) -> None:
        for name in ("A.md", "B.md", "C.md"):
            (vault.root / "notes" / name).write_bytes(b"\xff\xfe\x00")
        report = diagnose_vault(vault.root)
        assert len(report.of(Problem.MALFORMED)) == 3

    def test_a_missing_area_is_skipped(self, vault: Vault) -> None:
        import shutil

        shutil.rmtree(vault.root / "timer")
        report = diagnose_vault(vault.root)
        assert report.is_clean

    def test_the_summary_counts_by_kind(self, vault: Vault) -> None:
        (vault.root / "notes" / "A.md").write_bytes(b"\xff\xfe\x00")
        (vault.root / "todos" / "B.md").write_bytes(b"\xff\xfe\x00")
        report = diagnose_vault(vault.root)
        assert "2 malformed" in report.summary()

    def test_blocking_and_warnings_are_separated(self, vault: Vault) -> None:
        report = VaultReport()
        report.add(Diagnosis(Problem.CONFLICT, Severity.BLOCKING, None, "changed"))
        report.add(Diagnosis(Problem.LOCKED, Severity.WARNING, None, "in use"))
        assert len(report.blocking) == 1
        assert len(report.warnings) == 1


class TestErrorClassification:
    def test_a_collision_is_blocking(self) -> None:
        """Proceeding would overwrite something, so it must stop."""
        diagnosis = classify_vault_error(CollisionError("taken"))
        assert diagnosis.problem is Problem.CONFLICT
        assert diagnosis.is_blocking

    def test_a_path_escape_is_blocking(self) -> None:
        diagnosis = classify_vault_error(PathOutsideVaultError("outside"))
        assert diagnosis.problem is Problem.OUTSIDE_VAULT
        assert diagnosis.is_blocking

    def test_a_format_error_is_a_warning(self) -> None:
        diagnosis = classify_vault_error(DocumentFormatError("bad yaml"))
        assert diagnosis.problem is Problem.MALFORMED
        assert not diagnosis.is_blocking

    def test_a_permission_error_is_a_warning(self) -> None:
        diagnosis = classify_vault_error(PermissionError("denied"))
        assert diagnosis.problem is Problem.LOCKED
        assert not diagnosis.is_blocking

    def test_an_unknown_error_is_a_warning(self) -> None:
        diagnosis = classify_vault_error(RuntimeError("something else"))
        assert diagnosis.problem is Problem.UNREADABLE
        assert not diagnosis.is_blocking

    def test_the_diagnosis_says_nothing_was_written(self) -> None:
        diagnosis = classify_vault_error(CollisionError("taken"))
        assert "Nothing was overwritten" in diagnosis.detail


class TestWriteGuard:
    def test_an_unchanged_file_is_not_a_conflict(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/A.md", "x")
        guard = WriteGuard()
        guard.remember(path)
        assert guard.check(path) is None

    def test_an_external_edit_is_a_conflict(self, vault: Vault) -> None:
        """The case that would otherwise lose the user's work.

        The user edits the file in Obsidian while Nodify has it open, then saves
        here. Without a guard, the Obsidian edit is gone.
        """
        path = broken_file(vault, "notes/A.md", "original\n")
        guard = WriteGuard()
        guard.remember(path)

        os.utime(path, (0, 0))
        path.write_text("edited in Obsidian\n", encoding="utf-8")

        diagnosis = guard.check(path)
        assert diagnosis is not None
        assert diagnosis.problem is Problem.CONFLICT
        assert diagnosis.is_blocking

    def test_a_deleted_file_is_a_conflict(self, vault: Vault) -> None:
        """Writing to a file the user deleted would resurrect it."""
        path = broken_file(vault, "notes/A.md", "x")
        guard = WriteGuard()
        guard.remember(path)
        path.unlink()
        assert guard.check(path) is not None

    def test_a_file_we_never_read_is_not_a_conflict(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/New.md", "x")
        guard = WriteGuard()
        assert guard.check(path) is None

    def test_refreshing_accepts_the_new_baseline(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/A.md", "x")
        guard = WriteGuard()
        guard.remember(path)
        os.utime(path, (0, 0))
        path.write_text("changed\n", encoding="utf-8")
        assert guard.check(path) is not None

        # After a deliberate save, the new state is the baseline.
        guard.refresh(path)
        assert guard.check(path) is None

    def test_forgetting_removes_the_tracking(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/A.md", "x")
        guard = WriteGuard()
        guard.remember(path)
        guard.forget(path)
        os.utime(path, (0, 0))
        path.write_text("changed\n", encoding="utf-8")
        assert guard.check(path) is None

    def test_many_files_are_tracked_independently(self, vault: Vault) -> None:
        first = broken_file(vault, "notes/A.md", "x")
        second = broken_file(vault, "notes/B.md", "x")
        guard = WriteGuard()
        guard.remember(first)
        guard.remember(second)

        os.utime(second, (0, 0))
        second.write_text("changed\n", encoding="utf-8")

        assert guard.check(first) is None
        assert guard.check(second) is not None


class TestFileStamp:
    def test_a_stamp_matches_an_untouched_file(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/A.md", "x")
        stamp = FileStamp.of(path)
        assert stamp is not None
        assert not stamp.conflicts_with(path)

    def test_a_stamp_of_a_missing_file_is_none(self, vault: Vault) -> None:
        assert FileStamp.of(vault.root / "notes" / "Gone.md") is None

    def test_a_size_change_is_detected(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/A.md", "x")
        stamp = FileStamp.of(path)
        path.write_text("much longer content", encoding="utf-8")
        assert stamp is not None
        assert stamp.conflicts_with(path)

    def test_a_deleted_file_conflicts(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/A.md", "x")
        stamp = FileStamp.of(path)
        path.unlink()
        assert stamp is not None
        assert stamp.conflicts_with(path)


class TestAtomicWrite:
    def test_available_in_a_normal_folder(self, vault: Vault) -> None:
        assert atomic_write_is_available(vault.root / "notes")

    def test_the_probe_leaves_nothing_behind(self, vault: Vault) -> None:
        directory = vault.root / "notes"
        before = sorted(p.name for p in directory.iterdir())
        atomic_write_is_available(directory)
        assert sorted(p.name for p in directory.iterdir()) == before

    def test_unavailable_where_writing_is_denied(
        self, vault: Vault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def deny(*_args: object, **_kwargs: object) -> None:
            raise OSError("read-only file system")

        monkeypatch.setattr(Path, "write_text", deny)
        assert not atomic_write_is_available(vault.root / "notes")

    def test_the_creates_a_missing_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "new" / "place"
        assert atomic_write_is_available(target)
        assert target.is_dir()


class TestLockDetection:
    def test_an_unlocked_file_is_not_locked(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/A.md", "x")
        assert not is_locked(path)

    def test_a_missing_file_is_not_locked(self, vault: Vault) -> None:
        assert not is_locked(vault.root / "notes" / "Gone.md")

    def test_a_directory_is_not_a_lock(self, vault: Vault) -> None:
        assert not is_locked(vault.root / "notes" / "Sub")

    def test_a_locked_file_is_reported_as_a_warning(self, vault: Vault) -> None:
        path = broken_file(vault, "notes/Locked.md", "x")
        real_open = os.open
        state = {"fail": False}

        def maybe_deny(path_arg: object, flags: int, *args: object) -> int:
            if state["fail"] and str(path_arg).endswith("Locked.md"):
                raise PermissionError("in use")
            return real_open(path_arg, flags, *args)  # type: ignore[arg-type]

        import nodify.services.diagnostics as module

        original = module.os.open
        module.os.open = maybe_deny  # type: ignore[assignment]
        try:
            state["fail"] = True
            assert is_locked(path)
            diagnosis = diagnose_path(path)
            assert diagnosis is not None
            assert diagnosis.problem is Problem.LOCKED
            assert not diagnosis.is_blocking
        finally:
            module.os.open = original  # type: ignore[assignment]


class TestScanForMalformed:
    def test_finds_every_unreadable_file(self, vault: Vault) -> None:
        broken_file(vault, "notes/Bad1.md", "---\nid: [x\n---\ntext\n")
        broken_file(vault, "notes/Bad2.md", "---\nid: [y\n---\ntext\n")
        broken_file(vault, "notes/Good.md", "---\nid: ok\ntype: note\ntitle: Good\n---\nB\n")

        failures = scan_for_malformed(vault.root, vault.documents, None)
        assert len(failures) == 2
        assert all(isinstance(exc, DocumentFormatError) for _path, exc in failures)

    def test_a_clean_vault_finds_nothing(self, vault: Vault) -> None:
        broken_file(vault, "notes/Good.md", "---\nid: ok\ntype: note\ntitle: G\n---\nB\n")
        assert scan_for_malformed(vault.root, vault.documents, None) == []

    def test_a_file_with_no_frontmatter_is_not_malformed(self, vault: Vault) -> None:
        """A plain note with no frontmatter parses; the id error is the mapper's."""
        broken_file(vault, "notes/Plain.md", "# Just markdown\n")
        assert scan_for_malformed(vault.root, vault.documents, None) == []


class TestPerformance:
    def test_a_large_vault_still_lists(self, tmp_path: Path) -> None:
        """A vault of a realistic size must stay responsive.

        The index deliberately does not parse Markdown, so listing cost is a
        directory walk. This guards against that changing.
        """
        from nodify.adapters.file_index import FileSystemFileIndex

        root = tmp_path / "big"
        (root / "notes" / "Work").mkdir(parents=True)
        for index in range(500):
            (root / "notes" / "Work" / f"note-{index:04d}.md").write_text(
                f"---\nid: note_{index}\ntype: note\ntitle: Note {index}\n---\nBody\n",
                encoding="utf-8",
            )

        import time

        started = time.perf_counter()
        entries = FileSystemFileIndex(root).refresh()
        elapsed = time.perf_counter() - started

        assert len(entries) == 500
        # Generous, because a loaded CI machine is slow. The point is to catch an
        # accidental per-file parse, which would be orders of magnitude worse.
        assert elapsed < 5.0

    def test_a_deep_tree_does_not_recurse_without_bound(self, tmp_path: Path) -> None:
        from nodify.adapters.file_index import FileSystemFileIndex

        root = tmp_path / "deep"
        deep = root / "notes"
        for level in range(12):
            deep = deep / f"level-{level}"
        deep.mkdir(parents=True)
        (deep / "A.md").write_text("x", encoding="utf-8")

        assert len(FileSystemFileIndex(root).refresh()) == 1
