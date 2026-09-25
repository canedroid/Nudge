"""Vault creation, selection and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from nodify.adapters.vault import (
    VAULT_MARKER,
    NotAVaultError,
    Vault,
    VaultAlreadyExistsError,
    create,
    describe,
    is_vault,
)


class TestCreate:
    def test_creates_marker_and_areas(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        info = create(root)

        assert info.exists
        assert info.format_version == 1
        assert is_vault(root)
        for area in ("notes", "todos", "timer"):
            assert (root / area).is_dir()

    def test_seeds_year_month_folders(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        create(root, initial_year_month="2026-09")
        assert (root / "todos" / "2026-09").is_dir()
        assert (root / "timer" / "2026-09").is_dir()

    def test_refuses_existing_vault(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        create(root)
        with pytest.raises(VaultAlreadyExistsError):
            create(root)

    def test_refuses_non_empty_folder(self, tmp_path: Path) -> None:
        root = tmp_path / "existing"
        root.mkdir()
        (root / "important.txt").write_text("do not reorganise me", encoding="utf-8")

        with pytest.raises(NotAVaultError, match="not empty"):
            create(root)
        assert (root / "important.txt").is_file()

    def test_refuses_a_file_target(self, tmp_path: Path) -> None:
        target = tmp_path / "afile.txt"
        target.write_text("x", encoding="utf-8")
        with pytest.raises(NotAVaultError, match="not a folder"):
            create(target)

    def test_accepts_folder_holding_only_a_marker_like_file(self, tmp_path: Path) -> None:
        # A half-removed vault leaves the marker; this is not a real vault, so it
        # must still be rejected rather than silently adopted.
        root = tmp_path / "half"
        root.mkdir()
        (root / VAULT_MARKER).write_text("format_version: 1\n", encoding="utf-8")
        with pytest.raises(VaultAlreadyExistsError):
            create(root)

    def test_marker_explains_itself(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        create(root)
        text = (root / VAULT_MARKER).read_text(encoding="utf-8")
        assert "format_version: 1" in text
        assert "Nodify vault marker" in text


class TestDescribe:
    def test_missing_folder(self, tmp_path: Path) -> None:
        info = describe(tmp_path / "nope")
        assert not info.exists
        assert info.format_version == 0

    def test_reads_format_version(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        root.mkdir()
        (root / VAULT_MARKER).write_text("format_version: 7\n", encoding="utf-8")
        assert describe(root).format_version == 7

    def test_tolerates_unparsable_marker(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        root.mkdir()
        (root / VAULT_MARKER).write_text("garbage\n", encoding="utf-8")
        assert describe(root).format_version == 1


class TestVault:
    def test_open_creates_structure_for_a_new_folder(self, tmp_path: Path) -> None:
        vault = Vault(tmp_path / "fresh")
        vault.open()
        assert vault.info.exists
        assert (vault.root / "notes").is_dir()

    def test_open_refuses_a_foreign_folder(self, tmp_path: Path) -> None:
        root = tmp_path / "documents"
        root.mkdir()
        (root / "notes").mkdir()
        (root / "notes" / "A.md").write_text("plain", encoding="utf-8")

        with pytest.raises(NotAVaultError):
            Vault(root).open()

    def test_exposes_consistent_adapters(self, tmp_path: Path) -> None:
        vault = Vault(tmp_path / "vault")
        vault.open()
        assert vault.documents.root == vault.root
        assert vault.index.root == vault.root
        assert vault.resolver.root == vault.root

    def test_write_through_vault_is_readable(self, tmp_path: Path) -> None:
        vault = Vault(tmp_path / "vault")
        vault.open()
        path = vault.resolver.resolve("notes", "A.md")
        vault.documents.write(path, {"id": "n1", "type": "note"}, "Body\n")
        assert vault.documents.read(path) == ({"id": "n1", "type": "note"}, "Body\n")

    def test_ensure_area_creates_missing_area(self, tmp_path: Path) -> None:
        from nodify.domain.ports import TIMERS

        vault = Vault(tmp_path / "vault")
        vault.open()
        (vault.root / "timer").rmdir()
        assert vault.area(TIMERS).is_dir()

    def test_index_sees_written_files(self, tmp_path: Path) -> None:
        vault = Vault(tmp_path / "vault")
        vault.open()
        vault.documents.write(vault.resolver.resolve("notes", "A.md"), {"id": "n1"}, "Body\n")
        assert [e.name for e in vault.index.refresh()] == ["A"]
