"""Category folder management."""

from __future__ import annotations

from pathlib import Path

import pytest

from nodify.adapters.category_repo import FileSystemCategoryRepository
from nodify.adapters.note_repo import MarkdownNoteRepository
from nodify.adapters.vault import Vault
from nodify.adapters.vault_paths import InvalidNameError
from nodify.domain.clock import FixedClock
from nodify.domain.ports import CollisionError, DocumentNotFoundError

T0 = __import__("datetime").datetime(2026, 9, 25, 12, 0, tzinfo=__import__("datetime").UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


@pytest.fixture
def repo(vault: Vault) -> FileSystemCategoryRepository:
    return FileSystemCategoryRepository(vault)


@pytest.fixture
def notes(vault: Vault) -> MarkdownNoteRepository:
    return MarkdownNoteRepository(vault, FixedClock(T0))


class TestCreate:
    def test_creates_the_folder(self, repo: FileSystemCategoryRepository, vault: Vault) -> None:
        assert repo.create("Work") == "Work"
        assert (vault.root / "notes" / "Work").is_dir()

    def test_creates_parents(self, repo: FileSystemCategoryRepository, vault: Vault) -> None:
        (vault.root / "notes").rmdir()
        repo.create("Work")
        assert (vault.root / "notes" / "Work").is_dir()

    def test_existing_category_is_a_no_op(self, repo: FileSystemCategoryRepository) -> None:
        repo.create("Work")
        assert repo.create("Work") == "Work"

    def test_strips_surrounding_whitespace(self, repo: FileSystemCategoryRepository) -> None:
        assert repo.create("  Work  ") == "Work"

    @pytest.mark.parametrize("name", ["", "   ", "a/b", "a\\b", "a:b", "CON", "trailing."])
    def test_rejects_invalid_names(self, repo: FileSystemCategoryRepository, name: str) -> None:
        with pytest.raises(InvalidNameError):
            repo.create(name)

    def test_invalid_name_creates_nothing(
        self, repo: FileSystemCategoryRepository, vault: Vault
    ) -> None:
        with pytest.raises(InvalidNameError):
            repo.create("bad/name")
        assert not (vault.root / "notes" / "bad").exists()

    def test_unicode_category(self, repo: FileSystemCategoryRepository, vault: Vault) -> None:
        repo.create("日本語")
        assert (vault.root / "notes" / "日本語").is_dir()


class TestList:
    def test_empty_vault(self, repo: FileSystemCategoryRepository) -> None:
        assert repo.list() == []

    def test_lists_every_folder(self, repo: FileSystemCategoryRepository) -> None:
        repo.create("Work")
        repo.create("Home")
        assert repo.list() == ["Home", "Work"]

    def test_includes_empty_folders(self, repo: FileSystemCategoryRepository) -> None:
        """Unlike the notes view, an empty folder is still a real category here.

        The Files panel has to be able to select a folder it just created, or one
        left empty by deleting its last note, in order to rename or remove it.
        """
        repo.create("Empty")
        assert repo.list() == ["Empty"]

    def test_includes_a_folder_with_an_unreadable_note(
        self, repo: FileSystemCategoryRepository, notes: MarkdownNoteRepository, vault: Vault
    ) -> None:
        (vault.root / "notes" / "Broken").mkdir(parents=True, exist_ok=True)
        (vault.root / "notes" / "Broken" / "A.md").write_text("junk", encoding="utf-8")
        assert repo.list() == ["Broken"]

    def test_ignores_loose_files(self, repo: FileSystemCategoryRepository, vault: Vault) -> None:
        (vault.root / "notes" / "A.md").write_text("x", encoding="utf-8")
        assert repo.list() == []

    def test_creates_the_notes_root_if_missing(
        self, repo: FileSystemCategoryRepository, vault: Vault
    ) -> None:
        (vault.root / "notes").rmdir()
        assert repo.list() == []
        assert (vault.root / "notes").is_dir()

    def test_sorted_case_insensitively(self, repo: FileSystemCategoryRepository) -> None:
        repo.create("zeta")
        repo.create("Alpha")
        assert repo.list() == ["Alpha", "zeta"]


class TestRename:
    def test_renames_the_folder(self, repo: FileSystemCategoryRepository, vault: Vault) -> None:
        repo.create("Work")
        repo.rename("Work", "Office")
        assert (vault.root / "notes" / "Office").is_dir()
        assert not (vault.root / "notes" / "Work").exists()

    def test_notes_travel_with_the_folder(
        self, repo: FileSystemCategoryRepository, notes: MarkdownNoteRepository
    ) -> None:
        repo.create("Work")
        note = notes.create("Ideas", "Work")
        repo.rename("Work", "Office")

        assert repo.exists("Office")
        assert notes.get(note.id).category == "Office"

    def test_missing_category_raises(self, repo: FileSystemCategoryRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.rename("Nope", "Other")

    def test_merging_into_an_existing_category_is_refused(
        self, repo: FileSystemCategoryRepository
    ) -> None:
        repo.create("Work")
        repo.create("Home")
        with pytest.raises(CollisionError):
            repo.rename("Work", "Home")
        assert repo.exists("Work")
        assert repo.exists("Home")

    def test_renaming_to_itself_is_a_no_op(self, repo: FileSystemCategoryRepository) -> None:
        repo.create("Work")
        repo.rename("Work", "Work")
        assert repo.exists("Work")


class TestRemove:
    def test_removes_an_empty_folder(
        self, repo: FileSystemCategoryRepository, vault: Vault
    ) -> None:
        repo.create("Empty")
        repo.remove("Empty")
        assert not (vault.root / "notes" / "Empty").exists()

    def test_refuses_to_remove_a_category_holding_notes(
        self, repo: FileSystemCategoryRepository, notes: MarkdownNoteRepository
    ) -> None:
        repo.create("Work")
        notes.create("Ideas", "Work")
        with pytest.raises(CollisionError):
            repo.remove("Work")

    def test_missing_category_raises(self, repo: FileSystemCategoryRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.remove("Nope")

    def test_notes_root_cannot_be_removed(self, repo: FileSystemCategoryRepository) -> None:
        with pytest.raises((CollisionError, DocumentNotFoundError, InvalidNameError)):
            repo.remove("..")


class TestExists:
    def test_true_for_a_folder(self, repo: FileSystemCategoryRepository) -> None:
        repo.create("Work")
        assert repo.exists("Work")

    def test_false_for_a_missing_folder(self, repo: FileSystemCategoryRepository) -> None:
        assert not repo.exists("Work")

    def test_false_for_a_file(self, repo: FileSystemCategoryRepository, vault: Vault) -> None:
        (vault.root / "notes" / "Thing.md").write_text("x", encoding="utf-8")
        assert not repo.exists("Thing.md")


class TestExternallyCreatedCategories:
    def test_a_folder_made_outside_nodify_is_listed(
        self, vault: Vault, notes: MarkdownNoteRepository
    ) -> None:
        """The folder is the record, so a folder is a category.

        A user who creates one in Obsidian, or drops a folder into the vault,
        expects Nodify to show it.
        """
        folder = vault.root / "notes" / "Made Elsewhere"
        folder.mkdir(parents=True)
        (folder / "A.md").write_text(
            "---\nid: note_x\ntype: note\ntitle: A\n---\nBody\n", encoding="utf-8"
        )
        assert notes.list_categories() == ["Made Elsewhere"]
