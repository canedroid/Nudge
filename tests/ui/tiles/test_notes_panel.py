"""Notes panel behaviour, driven against a real temporary vault.

The panel is tested with the real repository rather than a fake, because the
interesting failures are the ones where the two disagree: a title that cannot be
slugified, a duplicate that needs a suffixed file name, a note that moves folders.
A fake would pass all of those and prove nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodify.adapters.note_repo import MarkdownNoteRepository
from nodify.adapters.vault import Vault
from nodify.domain.clock import FixedClock
from nodify.domain.ports import DocumentNotFoundError, VaultError
from nodify.ui.tiles.notes_panel import ALL_CATEGORIES, NotesPanel

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


@pytest.fixture
def repository(vault: Vault) -> MarkdownNoteRepository:
    return MarkdownNoteRepository(vault, FixedClock(T0))


@pytest.fixture
def panel(qapp: object, repository: MarkdownNoteRepository) -> NotesPanel:
    widget = NotesPanel(repository)
    yield widget
    widget.deleteLater()


class TestInitialState:
    def test_starts_empty(self, panel: NotesPanel) -> None:
        assert panel._note_list.count() == 0
        assert panel.current_note_id is None

    def test_editor_is_disabled_with_no_note(self, panel: NotesPanel) -> None:
        assert not panel._editor.isEnabled()
        assert not panel._save_button.isEnabled()
        assert not panel._delete_button.isEnabled()

    def test_shows_the_all_category(self, panel: NotesPanel) -> None:
        assert panel._category_list.count() == 1
        assert panel._category_list.item(0).text() == ALL_CATEGORIES

    def test_empty_state_hint(self, panel: NotesPanel) -> None:
        assert "No notes yet" in panel._status.text()


class TestCreate:
    def test_creates_and_opens(self, panel: NotesPanel, repository: MarkdownNoteRepository) -> None:
        panel.create_note()
        assert panel.current_note_id is not None
        assert panel._title.text() == "Untitled"
        assert panel._editor.isEnabled()

    def test_new_note_is_listed(self, panel: NotesPanel) -> None:
        panel.create_note()
        assert panel._note_list.count() == 1

    def test_default_category_is_general(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        panel.create_note()
        assert repository.list_categories() == ["General"]

    def test_uses_the_selected_category(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        repository.create("Existing", "Work")
        panel.reload()
        panel._category_list.setCurrentRow(1)
        assert panel._category_list.currentItem().text() == "Work"
        panel.create_note()
        assert len(repository.list_notes("Work")) == 2

    def test_duplicate_titles_both_appear(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        panel.create_note()
        panel._title.setText("Untitled")
        panel.save_current_note()
        panel.create_note()
        panel._title.setText("Untitled")
        panel.save_current_note()
        assert len(repository.list_notes()) == 2

    def test_emits_change(self, panel: NotesPanel) -> None:
        seen: list[int] = []
        panel.notes_changed.connect(lambda: seen.append(1))
        panel.create_note()
        assert seen


class TestEditAndSave:
    def test_saves_title_and_body(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        panel.create_note()
        panel._title.setText("My note")
        panel._editor.setPlainText("Some content")
        assert panel.save_current_note()

        saved = repository.list_notes()[0]
        assert saved.title == "My note"
        assert saved.body == "Some content"

    def test_saving_renames_the_file(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        panel.create_note()
        panel._title.setText("Renamed")
        panel.save_current_note()
        assert repository.path_of(repository.list_notes()[0].id).name == "Renamed.md"

    def test_blank_title_is_refused(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        panel.create_note()
        note_id = panel.current_note_id
        panel._title.setText("   ")
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)

        assert not panel.save_current_note()
        assert errors
        assert panel.current_note_id == note_id

    def test_save_with_no_note_open_does_nothing(self, panel: NotesPanel) -> None:
        assert not panel.save_current_note()

    def test_dirty_hint_appears(self, panel: NotesPanel) -> None:
        panel.create_note()
        panel._editor.setPlainText("changed")
        assert "Unsaved" in panel._status.text()

    def test_no_dirty_hint_after_saving(self, panel: NotesPanel) -> None:
        panel.create_note()
        panel._editor.setPlainText("changed")
        panel.save_current_note()
        assert panel._status.text() == "Saved."

    def test_no_dirty_hint_while_loading(self, panel: NotesPanel) -> None:
        panel.create_note()
        # Loading a note must not look like an edit.
        note_id = panel.current_note_id
        assert note_id is not None
        panel.load_note(note_id)
        assert "Unsaved" not in panel._status.text()

    def test_editor_keeps_focus_through_a_save(self, panel: NotesPanel) -> None:
        """Saving reloads the list, which must not clear what the user is typing."""
        panel.create_note()
        panel._title.setText("Kept")
        panel._editor.setPlainText("Body")
        panel.save_current_note()
        assert panel._title.text() == "Kept"
        assert panel._editor.toPlainText() == "Body"
        assert panel.current_note_id is not None


class TestDelete:
    def test_deletes_the_open_note(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        panel.create_note()
        assert panel.delete_current_note()
        assert repository.list_notes() == []

    def test_clears_the_editor(self, panel: NotesPanel) -> None:
        panel.create_note()
        panel.delete_current_note()
        assert panel.current_note_id is None
        assert not panel._editor.isEnabled()

    def test_delete_with_no_note_does_nothing(self, panel: NotesPanel) -> None:
        assert not panel.delete_current_note()


class TestCategories:
    def test_lists_existing_categories(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        repository.create("A", "Work")
        repository.create("B", "Home")
        panel.reload()
        labels = [panel._category_list.item(i).text() for i in range(panel._category_list.count())]
        assert labels == [ALL_CATEGORIES, "Home", "Work"]

    def test_selecting_a_category_filters(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        repository.create("Work note", "Work")
        repository.create("Home note", "Home")
        panel.reload()

        panel._category_list.setCurrentRow(1)
        assert [panel._note_list.item(i).text() for i in range(panel._note_list.count())] == [
            "Home note"
        ]

    def test_unchecking_returns_to_all(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        repository.create("Work note", "Work")
        repository.create("Home note", "Home")
        panel.reload()
        panel._category_list.setCurrentRow(1)
        panel._category_list.setCurrentRow(0)
        assert panel._note_list.count() == 2

    def test_category_selection_survives_a_reload(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        repository.create("A", "Work")
        panel.reload()
        panel._category_list.setCurrentRow(1)
        panel.reload()
        assert panel._category_list.currentItem().text() == "Work"

    def test_switching_category_clears_the_editor(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        repository.create("A", "Work")
        repository.create("B", "Home")
        panel.reload()
        panel._category_list.setCurrentRow(1)
        panel._note_list.setCurrentRow(0)
        assert panel.current_note_id is not None
        panel._category_list.setCurrentRow(0)
        assert panel.current_note_id is None

    def test_category_with_no_matching_notes_shows_a_hint(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        """Selecting a category whose notes are all filtered out still explains itself."""
        repository.create("A", "Work")
        repository.create("B", "Home")
        panel.reload()

        # Both categories have notes, so a search that matches nothing is the
        # realistic way to land on an empty result set.
        panel._category_list.setCurrentRow(1)
        panel._search.setText("zzzz-no-match")
        assert "No notes match" in panel._status.text()

    def test_all_category_with_no_notes_shows_the_new_hint(self, panel: NotesPanel) -> None:
        panel._category_list.setCurrentRow(0)
        panel._search.clear()
        assert "No notes yet" in panel._status.text()


class TestSearch:
    def test_filters_the_list(self, panel: NotesPanel, repository: MarkdownNoteRepository) -> None:
        repository.create("Groceries", "Home")
        repository.create("Meeting", "Work")
        panel.reload()

        panel._search.setText("groc")
        assert [panel._note_list.item(i).text() for i in range(panel._note_list.count())] == [
            "Groceries"
        ]

    def test_searches_the_body(self, panel: NotesPanel, repository: MarkdownNoteRepository) -> None:
        repository.create("Untitled", "Home", "remember the milk")
        panel.reload()
        panel._search.setText("milk")
        assert panel._note_list.count() == 1

    def test_no_match_hint(self, panel: NotesPanel, repository: MarkdownNoteRepository) -> None:
        repository.create("Groceries", "Home")
        panel.reload()
        panel._search.setText("zzzz")
        assert "No notes match" in panel._status.text()

    def test_clearing_the_search_restores_the_list(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        repository.create("Groceries", "Home")
        repository.create("Meeting", "Work")
        panel.reload()
        panel._search.setText("groc")
        assert panel._note_list.count() == 1
        panel._search.clear()
        assert panel._note_list.count() == 2


class TestSelection:
    def test_selecting_a_row_loads_it(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        created = repository.create("Loaded", "Work", "body text")
        panel.reload()

        panel._note_list.setCurrentRow(0)
        assert panel.current_note_id == created.id
        assert panel._editor.toPlainText() == "body text"

    def test_clearing_the_selection_clears_the_editor(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        repository.create("Loaded", "Work", "body text")
        panel.reload()
        panel._note_list.setCurrentRow(0)
        panel._note_list.setCurrentRow(-1)
        assert panel.current_note_id is None


class TestErrorHandling:
    def test_repository_failure_is_reported_not_raised(self, panel: NotesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)

        def boom() -> list[str]:
            raise VaultError("disk on fire")

        # Both list calls fail; the panel must survive and show empty.
        panel._repository.list_categories = boom  # type: ignore[method-assign]
        panel._repository.list_notes = boom  # type: ignore[method-assign]
        panel.reload()
        assert len(errors) == 2
        assert panel._note_list.count() == 0

    def test_a_broken_note_does_not_break_the_panel(
        self, panel: NotesPanel, repository: MarkdownNoteRepository, vault: Vault
    ) -> None:
        repository.create("Good", "Work")
        (vault.root / "notes" / "Work" / "Broken.md").write_text(
            "---\nid: [unclosed\n---\nBody\n", encoding="utf-8"
        )
        panel.reload()
        assert panel._note_list.count() == 1

    def test_save_failure_is_reported(self, panel: NotesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        panel.create_note()

        def boom(*_args: object, **_kwargs: object) -> object:
            raise VaultError("read only filesystem")

        panel._repository.update = boom  # type: ignore[method-assign]
        assert not panel.save_current_note()
        assert errors

    def test_create_failure_is_reported(self, panel: NotesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)

        def boom(*_args: object, **_kwargs: object) -> object:
            raise VaultError("no space left")

        panel._repository.create = boom  # type: ignore[method-assign]
        panel.create_note()
        assert errors

    def test_delete_failure_is_reported(self, panel: NotesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        panel.create_note()

        def boom(_note_id: str) -> None:
            raise DocumentNotFoundError("already gone")

        panel._repository.delete = boom  # type: ignore[method-assign]
        assert not panel.delete_current_note()
        assert errors


class TestMove:
    def test_a_moved_note_follows_its_category(
        self, panel: NotesPanel, repository: MarkdownNoteRepository
    ) -> None:
        note = repository.create("Travelling", "Work")
        panel.reload()
        panel._note_list.setCurrentRow(0)
        assert panel.current_note_id == note.id

        repository.move(note.id, "Home")
        panel.reload()
        panel._category_list.setCurrentRow(0)
        assert panel._note_list.count() == 1
