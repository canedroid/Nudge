"""Note persistence: create, edit, rename, move, delete, search, categories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nodify.adapters.note_repo import UNCATEGORISED, MarkdownNoteRepository
from nodify.adapters.vault import Vault
from nodify.adapters.vault_paths import InvalidNameError
from nodify.domain.clock import FixedClock
from nodify.domain.documents import Note
from nodify.domain.ports import (
    CollisionError,
    DocumentNotFoundError,
)

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class SteppingClock:
    """A clock that advances by a fixed step on every reading.

    Timestamps are read once per write, so a frozen clock would make
    ``created_at`` and ``updated_at`` identical and hide ordering bugs.
    """

    def __init__(self, start: datetime = T0, step_seconds: int = 60) -> None:
        self._current = start
        self._step = step_seconds

    def now(self) -> datetime:
        current = self._current
        self._current += timedelta(seconds=self._step)
        return current


@pytest.fixture
def clock() -> SteppingClock:
    return SteppingClock()


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


@pytest.fixture
def repo(vault: Vault, clock: SteppingClock) -> MarkdownNoteRepository:
    return MarkdownNoteRepository(vault, clock)


def body_of(vault: Vault, repo: MarkdownNoteRepository, note: Note) -> str:
    """Read a note back off disk, proving it was really written."""
    frontmatter, body = vault.documents.read(repo.path_of(note.id))
    return body


class TestCreate:
    def test_creates_the_expected_path(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        note = repo.create("Ideas", "Work")
        assert vault.resolver.resolve("notes", "Work", "Ideas.md").is_file()
        assert note.title == "Ideas"
        assert note.category == "Work"

    def test_writes_frontmatter_and_body(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work", "Some content\n")
        assert note.body == "Some content\n"
        assert note.id.startswith("note_")
        assert note.created_at == note.updated_at

    def test_body_is_persisted(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        note = repo.create("Ideas", "Work", "Some content\n")
        assert body_of(vault, repo, note) == "Some content\n"

    def test_creates_missing_category_folder(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        repo.create("Ideas", "Brand New")
        assert (vault.root / "notes" / "Brand New").is_dir()

    def test_ids_are_unique_within_one_instant(self, vault: Vault) -> None:
        frozen = FixedClock(T0)
        repo = MarkdownNoteRepository(vault, frozen)
        ids = {repo.create(f"Note {i}", "Work").id for i in range(20)}
        assert len(ids) == 20

    def test_ids_are_unique_across_instants(self, repo: MarkdownNoteRepository) -> None:
        ids = {repo.create(f"Note {i}", "Work").id for i in range(20)}
        assert len(ids) == 20

    def test_title_with_illegal_characters_is_slugified(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        note = repo.create('Report: Q1/Q2 "final"', "Work")
        # The colon and slashes are not legal in a Windows file name, so the
        # path uses a slug while the frontmatter keeps the title the user typed.
        assert repo.path_of(note.id).name == "Report- Q1-Q2 -final-.md"
        assert repo.get(note.id).title == 'Report: Q1/Q2 "final"'

    def test_empty_category_goes_to_uncategorised(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        note = repo.create("Loose", "")
        assert note.category == UNCATEGORISED
        assert (vault.root / "notes" / UNCATEGORISED / "Loose.md").is_file()

    def test_blank_title_is_rejected(self, repo: MarkdownNoteRepository) -> None:
        with pytest.raises(InvalidNameError):
            repo.create("   ", "Work")

    def test_invalid_category_is_rejected_before_writing(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        with pytest.raises(InvalidNameError):
            repo.create("Ideas", "bad/category")
        assert not (vault.root / "notes" / "bad").exists()

    def test_duplicate_titles_do_not_overwrite(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        first = repo.create("Ideas", "Work", "first\n")
        second = repo.create("Ideas", "Work", "second\n")

        assert first.id != second.id
        assert (vault.root / "notes" / "Work" / "Ideas.md").is_file()
        assert (vault.root / "notes" / "Work" / "Ideas 2.md").is_file()
        assert body_of(vault, repo, first) == "first\n"
        assert body_of(vault, repo, second) == "second\n"

    def test_duplicate_suggestion_keeps_counting(self, repo: MarkdownNoteRepository) -> None:
        names = [
            repo.path_of(repo.create("Ideas", "Work", f"body {i}\n").id).name for i in range(3)
        ]
        assert names == ["Ideas.md", "Ideas 2.md", "Ideas 3.md"]

    def test_same_title_in_different_categories_is_fine(self, repo: MarkdownNoteRepository) -> None:
        first = repo.create("Ideas", "Work")
        second = repo.create("Ideas", "Home")
        assert first.id != second.id
        assert repo.path_of(first.id).parent.name == "Work"
        assert repo.path_of(second.id).parent.name == "Home"


class TestRead:
    def test_get_by_id(self, repo: MarkdownNoteRepository) -> None:
        created = repo.create("Ideas", "Work", "content\n")
        fetched = repo.get(created.id)
        assert fetched.title == "Ideas"
        assert fetched.body == "content\n"

    def test_get_unknown_id_raises(self, repo: MarkdownNoteRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.get("note_nope")

    def test_path_of(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        note = repo.create("Ideas", "Work")
        assert repo.path_of(note.id) == vault.root / "notes" / "Work" / "Ideas.md"

    def test_list_is_newest_first(self, repo: MarkdownNoteRepository) -> None:
        first = repo.create("First", "Work")
        second = repo.create("Second", "Work")
        third = repo.create("Third", "Work")
        assert [n.id for n in repo.list_notes()] == [third.id, second.id, first.id]

    def test_list_filters_by_category(self, repo: MarkdownNoteRepository) -> None:
        repo.create("Work note", "Work")
        home = repo.create("Home note", "Home")
        assert [n.id for n in repo.list_notes("Home")] == [home.id]

    def test_list_on_empty_vault(self, repo: MarkdownNoteRepository) -> None:
        assert repo.list_notes() == []

    def test_a_broken_file_does_not_hide_the_vault(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        repo.create("Good", "Work")
        (vault.root / "notes" / "Work" / "Broken.md").write_text(
            "---\ntype: note\n---\nNo id here\n", encoding="utf-8"
        )
        assert [n.title for n in repo.list_notes()] == ["Good"]

    def test_recent_limits(self, repo: MarkdownNoteRepository) -> None:
        for index in range(5):
            repo.create(f"Note {index}", "Work")
        assert len(repo.recent(limit=3)) == 3

    def test_recent_zero_or_negative_is_empty(self, repo: MarkdownNoteRepository) -> None:
        repo.create("Note", "Work")
        assert repo.recent(0) == []
        assert repo.recent(-1) == []

    def test_note_without_created_at_sorts_last(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        repo.create("Dated", "Work")
        (vault.root / "notes" / "Work" / "Undated.md").write_text(
            "---\nid: note_undated\ntype: note\ntitle: Undated\n---\n", encoding="utf-8"
        )
        assert repo.list_notes()[-1].title == "Undated"


class TestUpdate:
    def test_edits_the_body(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        note = repo.create("Ideas", "Work", "old\n")
        repo.update(note.id, body="new\n")
        assert repo.get(note.id).body == "new\n"
        assert body_of(vault, repo, note) == "new\n"

    def test_edits_only_what_was_passed(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work", "content\n")
        repo.update(note.id, body="new body\n")
        fetched = repo.get(note.id)
        assert fetched.title == "Ideas"
        assert fetched.category == "Work"
        assert fetched.body == "new body\n"

    def test_update_bumps_updated_at(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        original_created = note.created_at
        assert original_created is not None
        updated = repo.update(note.id, body="changed\n")
        assert updated.updated_at is not None
        assert updated.updated_at > original_created
        assert updated.created_at == original_created

    def test_renaming_moves_the_file(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        note = repo.create("Ideas", "Work")
        repo.rename(note.id, "Plans")
        assert not (vault.root / "notes" / "Work" / "Ideas.md").exists()
        assert (vault.root / "notes" / "Work" / "Plans.md").is_file()
        assert repo.get(note.id).title == "Plans"

    def test_rename_preserves_body_and_id(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work", "content\n")
        renamed = repo.rename(note.id, "Plans")
        assert renamed.id == note.id
        assert renamed.body == "content\n"

    def test_rename_to_the_same_title_is_a_no_op(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        repo.rename(note.id, "Ideas")
        assert (repo.path_of(note.id)).name == "Ideas.md"

    def test_rename_onto_an_existing_file_is_refused(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        first = repo.create("Ideas", "Work", "first\n")
        repo.create("Plans", "Work", "second\n")

        with pytest.raises(CollisionError):
            repo.rename(first.id, "Plans")

        # Both notes are untouched.
        assert repo.get(first.id).body == "first\n"
        assert (vault.root / "notes" / "Work" / "Ideas.md").is_file()
        assert (vault.root / "notes" / "Work" / "Plans.md").is_file()

    def test_blank_rename_is_rejected(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        with pytest.raises(InvalidNameError):
            repo.rename(note.id, "   ")
        assert (repo.path_of(note.id)).name == "Ideas.md"

    def test_rename_with_illegal_characters(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        repo.rename(note.id, "A/B")
        assert repo.path_of(note.id).name == "A-B.md"
        assert repo.get(note.id).title == "A/B"

    def test_title_and_body_together(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work", "old\n")
        repo.update(note.id, title="Plans", body="new\n")
        fetched = repo.get(note.id)
        assert fetched.title == "Plans"
        assert fetched.body == "new\n"


class TestMove:
    def test_move_changes_the_folder(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        note = repo.create("Ideas", "Work")
        repo.move(note.id, "Home")
        assert not (vault.root / "notes" / "Work" / "Ideas.md").exists()
        assert (vault.root / "notes" / "Home" / "Ideas.md").is_file()

    def test_move_preserves_everything_else(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work", "content\n")
        before = repo.get(note.id)
        after = repo.move(note.id, "Home")
        assert after.id == before.id
        assert after.body == before.body
        assert after.title == before.title
        assert after.created_at == before.created_at

    def test_move_into_an_existing_note_is_refused(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        source = repo.create("Ideas", "Work")
        repo.create("Ideas", "Home", "existing\n")

        with pytest.raises(CollisionError):
            repo.move(source.id, "Home")

        assert (vault.root / "notes" / "Work" / "Ideas.md").is_file()
        assert repo.get(source.id).category == "Work"

    def test_move_to_the_same_category_is_a_no_op(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        moved = repo.move(note.id, "Work")
        assert moved.category == "Work"
        assert repo.path_of(note.id).parent.name == "Work"

    def test_move_to_an_invalid_category_is_rejected(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        with pytest.raises(InvalidNameError):
            repo.move(note.id, "bad/category")
        assert (repo.path_of(note.id)).parent.name == "Work"

    def test_move_to_uncategorised(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        repo.move(note.id, "")
        assert repo.get(note.id).category == UNCATEGORISED

    def test_moved_note_is_still_found_by_id(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        repo.move(note.id, "Home")
        assert repo.get(note.id).title == "Ideas"


class TestDelete:
    def test_removes_the_file(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        note = repo.create("Ideas", "Work")
        path = vault.root / "notes" / "Work" / "Ideas.md"
        repo.delete(note.id)
        assert not path.exists()

    def test_deleted_note_is_gone(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("Ideas", "Work")
        repo.delete(note.id)
        with pytest.raises(DocumentNotFoundError):
            repo.get(note.id)

    def test_delete_unknown_id_raises(self, repo: MarkdownNoteRepository) -> None:
        with pytest.raises(DocumentNotFoundError):
            repo.delete("note_nope")

    def test_delete_prunes_the_empty_category(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        note = repo.create("Only", "Ephemeral")
        repo.delete(note.id)
        assert not (vault.root / "notes" / "Ephemeral").exists()

    def test_category_with_remaining_notes_is_kept(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        first = repo.create("One", "Work")
        repo.create("Two", "Work")
        repo.delete(first.id)
        assert (vault.root / "notes" / "Work").is_dir()

    def test_notes_root_is_never_removed(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        note = repo.create("Only", "Work")
        repo.delete(note.id)
        assert (vault.root / "notes").is_dir()

    def test_delete_is_idempotent_for_an_already_gone_file(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        note = repo.create("Ideas", "Work")
        repo.delete(note.id)
        # The note is gone, so this is a lookup failure, not a silent success.
        with pytest.raises(DocumentNotFoundError):
            repo.delete(note.id)


class TestCategories:
    def test_lists_categories_with_notes(self, repo: MarkdownNoteRepository) -> None:
        repo.create("A", "Work")
        repo.create("B", "Home")
        assert repo.list_categories() == ["Home", "Work"]

    def test_empty_category_is_not_listed(self, repo: MarkdownNoteRepository, vault: Vault) -> None:
        repo.create("A", "Work")
        (vault.root / "notes" / "Empty").mkdir()
        assert repo.list_categories() == ["Work"]

    def test_no_categories_on_an_empty_vault(self, repo: MarkdownNoteRepository) -> None:
        assert repo.list_categories() == []


class TestSearch:
    def test_matches_the_title(self, repo: MarkdownNoteRepository) -> None:
        repo.create("Groceries", "Home")
        assert [n.title for n in repo.search("groc")] == ["Groceries"]

    def test_matches_the_body(self, repo: MarkdownNoteRepository) -> None:
        repo.create("Untitled", "Home", "remember the milk\n")
        assert [n.title for n in repo.search("MILK")] == ["Untitled"]

    def test_is_case_insensitive(self, repo: MarkdownNoteRepository) -> None:
        repo.create("Groceries", "Home")
        assert repo.search("GROCERIES")

    def test_empty_query_returns_nothing(self, repo: MarkdownNoteRepository) -> None:
        repo.create("Groceries", "Home")
        assert repo.search("") == []
        assert repo.search("   ") == []

    def test_no_match_returns_nothing(self, repo: MarkdownNoteRepository) -> None:
        repo.create("Groceries", "Home")
        assert repo.search("zzzz") == []


class TestInterop:
    def test_foreign_frontmatter_survives_an_edit(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        path = vault.root / "notes" / "Work" / "Ideas.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: note_x\ntype: note\ntitle: Ideas\n"
            "obsidian:\n  cssclass: wide\ncustom: keep\n# a comment\n---\n\nBody\n",
            encoding="utf-8",
        )
        repo.update("note_x", body="Edited\n")
        text = path.read_text(encoding="utf-8")
        assert "cssclass: wide" in text
        assert "custom: keep" in text
        assert "# a comment" in text
        assert "Edited" in text

    def test_unicode_titles_round_trip(self, repo: MarkdownNoteRepository) -> None:
        note = repo.create("日本語のノート", "Study")
        assert repo.get(note.id).title == "日本語のノート"

    def test_markdown_is_not_escaped(self, repo: MarkdownNoteRepository) -> None:
        body = "# Heading\n\n```python\nprint('x')\n```\n"
        note = repo.create("Code", "Work", body)
        assert repo.get(note.id).body == body

    def test_repeated_edits_stay_byte_identical(self, vault: Vault) -> None:
        """An edit that changes nothing must not change the bytes.

        This is the property that stops the user's version history filling with
        diffs for files they never touched. It needs a frozen clock, because
        ``updated_at`` is *supposed* to move on a real edit.
        """
        repo = MarkdownNoteRepository(vault, FixedClock(T0))
        note = repo.create("Ideas", "Work", "Body\n")
        path = repo.path_of(note.id)

        repo.update(note.id, body="Body\n")
        first = path.read_text(encoding="utf-8")
        repo.update(note.id, body="Body\n")
        assert path.read_text(encoding="utf-8") == first

    def test_unreadable_file_does_not_raise_from_a_scan(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        """A malformed file is skipped, not crashed on.

        It surfaces as "not found" for a direct lookup, which is the honest
        answer: Nodify cannot address a file it cannot parse. The Files view is
        where the user sees the file exists.
        """
        path = vault.root / "notes" / "Work" / "Broken.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: [unclosed\n---\nBody\n", encoding="utf-8")

        with pytest.raises(DocumentNotFoundError):
            repo.get("note_x")
        assert repo.list_notes() == []

    def test_a_malformed_file_does_not_block_its_neighbours(
        self, repo: MarkdownNoteRepository, vault: Vault
    ) -> None:
        repo.create("Good", "Work")
        (vault.root / "notes" / "Work" / "Broken.md").write_text(
            "---\nid: [unclosed\n---\nBody\n", encoding="utf-8"
        )
        assert [n.title for n in repo.list_notes()] == ["Good"]
        assert repo.get(repo.list_notes()[0].id).title == "Good"
