"""Files panel: browsing, categories, moves and error recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nodify.adapters.category_repo import FileSystemCategoryRepository
from nodify.adapters.note_repo import MarkdownNoteRepository
from nodify.adapters.vault import Vault
from nodify.domain.clock import FixedClock
from nodify.domain.ports import VaultError
from nodify.ui.tiles.files_panel import (
    ALL_FILES,
    KIND_CATEGORY,
    ROLE_KIND,
    FilesPanel,
    describe_age,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


@pytest.fixture
def notes(vault: Vault) -> MarkdownNoteRepository:
    return MarkdownNoteRepository(vault, FixedClock(NOW))


@pytest.fixture
def categories(vault: Vault) -> FileSystemCategoryRepository:
    return FileSystemCategoryRepository(vault)


@pytest.fixture
def prompts() -> dict[str, str | None]:
    """Answers the panel will get from its prompt callback."""
    return {"next": None}


@pytest.fixture
def confirmations() -> dict[str, bool]:
    return {"answer": True}


@pytest.fixture
def panel(
    qapp: object,
    vault: Vault,
    notes: MarkdownNoteRepository,
    categories: FileSystemCategoryRepository,
    prompts: dict[str, str | None],
    confirmations: dict[str, bool],
) -> FilesPanel:
    widget = FilesPanel(
        vault.index,
        categories,
        notes,
        confirm=lambda _title, _message: confirmations["answer"],
        prompt=lambda _title, _label, initial: (
            prompts["next"] if prompts["next"] is not None else initial
        ),
    )
    widget._now = lambda: NOW
    yield widget
    widget.deleteLater()


def scope_labels(panel: FilesPanel) -> list[str]:
    return [panel._scope.item(i).text() for i in range(panel._scope.count())]


def file_labels(panel: FilesPanel) -> list[str]:
    return [panel._list.item(i).text() for i in range(panel._list.count())]


def select_category(panel: FilesPanel, name: str) -> None:
    assert panel.select_category(name)


def select_file(panel: FilesPanel, fragment: str) -> None:
    for row in range(panel._list.count()):
        item = panel._list.item(row)
        if item is not None and fragment in item.text():
            panel._list.setCurrentItem(item)
            return
    raise AssertionError(f"no file row containing {fragment!r}")


class TestDescribeAge:
    def test_just_now(self) -> None:
        assert describe_age(NOW, now=NOW) == "just now"

    def test_minutes(self) -> None:
        assert describe_age(NOW - timedelta(minutes=5), now=NOW) == "5m ago"

    def test_hours(self) -> None:
        assert describe_age(NOW - timedelta(hours=3), now=NOW) == "3h ago"

    def test_days(self) -> None:
        assert describe_age(NOW - timedelta(days=2), now=NOW) == "2d ago"


class TestInitialState:
    def test_scopes_offer_the_three_areas(self, panel: FilesPanel) -> None:
        labels = scope_labels(panel)
        assert labels[0] == ALL_FILES
        assert "notes" in labels and "todos" in labels and "timer" in labels

    def test_starts_on_all_files(self, panel: FilesPanel) -> None:
        assert panel._scope.currentItem().text() == ALL_FILES

    def test_empty_vault(self, panel: FilesPanel) -> None:
        assert panel._list.count() == 0
        assert "No files here yet" in panel._status.text()

    def test_empty_state_suggests_a_next_action(self, panel: FilesPanel) -> None:
        """An empty state that only says "nothing here" leaves the user stuck.

        Each panel's empty state names the action that fills it, which is this
        package's acceptance criterion for empty states.
        """
        assert "Create a note or a task" in panel._status.text()


class TestBrowsing:
    def test_lists_files_across_areas(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("Idea", "Work")
        panel.reload()
        assert panel._list.count() == 1
        assert "notes" in file_labels(panel)[0]

    def test_scope_filters_by_area(self, panel: FilesPanel, vault: Vault) -> None:
        (vault.root / "notes" / "A.md").write_text("x", encoding="utf-8")
        (vault.root / "timer" / "2026-09" / "25-2026.md").parent.mkdir(parents=True)
        (vault.root / "timer" / "2026-09" / "25-2026.md").write_text("x", encoding="utf-8")
        panel.reload()

        panel._scope.setCurrentRow(scope_labels(panel).index("notes"))
        assert panel._list.count() == 1
        assert "A.md" in file_labels(panel)[0]

    def test_scope_filters_by_category(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("Work note", "Work")
        notes.create("Home note", "Home")
        panel.reload()
        select_category(panel, "Work")
        assert panel._list.count() == 1
        assert "Work note" in file_labels(panel)[0]

    def test_categories_appear_in_the_scope(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("A", "Work")
        panel.reload()
        assert "Work" in scope_labels(panel)

    def test_a_folder_made_outside_nodify_is_listed(self, panel: FilesPanel, vault: Vault) -> None:
        folder = vault.root / "notes" / "Made Elsewhere"
        folder.mkdir(parents=True)
        (folder / "A.md").write_text(
            "---\nid: note_x\ntype: note\ntitle: A\n---\nBody\n", encoding="utf-8"
        )
        panel.reload()
        assert "Made Elsewhere" in scope_labels(panel)

    def test_selection_shows_details(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("Detail", "Work", "content")
        panel.reload()
        select_file(panel, "Detail")
        assert "bytes" in panel._status.text()

    def test_day_files_show_their_date(self, panel: FilesPanel, vault: Vault) -> None:
        path = vault.root / "todos" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: day_x\n---\nbody\n", encoding="utf-8")
        panel.reload()
        select_file(panel, "25-2026.md")
        assert "25 Sep 2026" in panel._status.text()

    def test_a_malformed_file_is_still_visible(self, panel: FilesPanel, vault: Vault) -> None:
        """The index does not parse Markdown, so a broken file still shows."""
        (vault.root / "notes" / "Broken.md").write_text("---\nid: [oops\n", encoding="utf-8")
        panel.reload()
        assert "Broken.md" in file_labels(panel)[0]


class TestRefresh:
    def test_reports_additions(self, panel: FilesPanel, notes: MarkdownNoteRepository) -> None:
        notes.create("Added elsewhere", "Work")
        panel.refresh()
        assert "1 added" in panel._status.text()

    def test_reports_removals(self, panel: FilesPanel, vault: Vault) -> None:
        path = vault.root / "notes" / "Gone.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: n1\n---\nbody\n", encoding="utf-8")
        panel.reload()

        path.unlink()
        panel.refresh()
        assert "1 removed" in panel._status.text()

    def test_reports_nothing_when_unchanged(self, panel: FilesPanel) -> None:
        panel.refresh()
        assert "Up to date" in panel._status.text()

    def test_a_failed_refresh_reports_and_survives(self, panel: FilesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)

        def boom() -> list[object]:
            raise VaultError("drive gone")

        panel._index.refresh = boom  # type: ignore[method-assign]
        panel.refresh()
        assert errors


class TestCategories:
    def test_create(self, panel: FilesPanel, vault: Vault) -> None:
        assert panel.create_category("Fresh")
        assert (vault.root / "notes" / "Fresh").is_dir()

    def test_create_prompts_when_no_name_given(
        self, panel: FilesPanel, prompts: dict[str, str | None], vault: Vault
    ) -> None:
        prompts["next"] = "Prompted"
        assert panel.create_category()
        assert (vault.root / "notes" / "Prompted").is_dir()

    def test_a_cancelled_prompt_does_nothing(self, panel: FilesPanel) -> None:
        assert not panel.create_category()
        assert panel._list.count() == 0

    def test_create_reports_a_bad_name(self, panel: FilesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.create_category("bad/name")
        assert errors

    def test_rename(self, panel: FilesPanel, notes: MarkdownNoteRepository, vault: Vault) -> None:
        notes.create("A note", "Work")
        panel.reload()
        select_category(panel, "Work")
        assert panel.rename_category("Office")
        assert (vault.root / "notes" / "Office").is_dir()
        assert not (vault.root / "notes" / "Work").exists()

    def test_rename_keeps_the_notes(self, panel: FilesPanel, notes: MarkdownNoteRepository) -> None:
        note = notes.create("A note", "Work")
        panel.reload()
        select_category(panel, "Work")
        panel.rename_category("Office")
        assert notes.get(note.id).category == "Office"

    def test_rename_without_a_selection(self, panel: FilesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.rename_category("Whatever")
        assert errors

    def test_rename_onto_an_existing_category_reports(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("A", "Work")
        notes.create("B", "Home")
        panel.reload()
        select_category(panel, "Work")
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)

        assert not panel.rename_category("Home")
        assert errors

    def test_delete_empty(self, panel: FilesPanel, vault: Vault) -> None:
        panel.create_category("Empty")
        select_category(panel, "Empty")
        assert panel.delete_category()
        assert not (vault.root / "notes" / "Empty").exists()

    def test_delete_requires_confirmation(
        self,
        panel: FilesPanel,
        confirmations: dict[str, bool],
        vault: Vault,
    ) -> None:
        panel.create_category("Empty")
        select_category(panel, "Empty")
        confirmations["answer"] = False

        assert not panel.delete_category()
        assert (vault.root / "notes" / "Empty").is_dir()

    def test_delete_a_non_empty_category_is_refused(
        self, panel: FilesPanel, notes: MarkdownNoteRepository, vault: Vault
    ) -> None:
        notes.create("A note", "Work")
        panel.reload()
        select_category(panel, "Work")
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)

        assert not panel.delete_category()
        assert errors
        # The folder and its note are both untouched.
        assert (vault.root / "notes" / "Work").is_dir()
        assert notes.list_categories() == ["Work"]

    def test_delete_without_a_selection(self, panel: FilesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.delete_category()
        assert errors

    def test_a_non_category_scope_cannot_be_deleted(self, panel: FilesPanel) -> None:
        panel._scope.setCurrentRow(scope_labels(panel).index("notes"))
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.delete_category()
        assert errors


class TestMoveNote:
    def test_moves_through_the_notes_repository(
        self, panel: FilesPanel, notes: MarkdownNoteRepository, vault: Vault
    ) -> None:
        note = notes.create("Travelling", "Work", "content")
        panel.reload()
        select_file(panel, "Travelling")

        assert panel.move_selected_note("Home")
        assert (vault.root / "notes" / "Home" / "Travelling.md").is_file()
        assert notes.get(note.id).category == "Home"

    def test_the_move_preserves_the_body(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        note = notes.create("Travelling", "Work", "keep this")
        panel.reload()
        select_file(panel, "Travelling")
        panel.move_selected_note("Home")
        assert notes.get(note.id).body == "keep this"

    def test_moving_to_the_same_category_does_nothing(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("Same", "Work")
        panel.reload()
        select_file(panel, "Same")
        assert not panel.move_selected_note("Work")

    def test_a_non_note_cannot_be_moved(self, panel: FilesPanel, vault: Vault) -> None:
        path = vault.root / "timer" / "2026-09" / "25-2026.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: day_x\n---\nbody\n", encoding="utf-8")
        panel.reload()
        select_file(panel, "25-2026.md")

        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.move_selected_note("Work")
        assert errors

    def test_nothing_selected(self, panel: FilesPanel) -> None:
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.move_selected_note("Work")
        assert errors

    def test_emits_change(self, panel: FilesPanel, notes: MarkdownNoteRepository) -> None:
        seen: list[int] = []
        panel.files_changed.connect(lambda: seen.append(1))
        notes.create("Travelling", "Work")
        panel.reload()
        select_file(panel, "Travelling")
        panel.move_selected_note("Home")
        assert seen

    def test_a_failed_move_is_reported(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("Travelling", "Work")
        panel.reload()
        select_file(panel, "Travelling")

        def boom(_note_id: str, _category: str) -> object:
            raise VaultError("read only")

        panel._notes.move = boom  # type: ignore[method-assign]
        errors: list[str] = []
        panel.error_occurred.connect(errors.append)
        assert not panel.move_selected_note("Home")
        assert errors


class TestSelection:
    def test_selected_entry_is_none_without_a_selection(self, panel: FilesPanel) -> None:
        assert panel.selected_entry() is None

    def test_selected_entry_resolves(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("Findable", "Work")
        panel.reload()
        select_file(panel, "Findable")
        entry = panel.selected_entry()
        assert entry is not None
        assert entry.name == "Findable"
        assert entry.category == "Work"

    def test_selected_category_is_none_for_an_area_scope(self, panel: FilesPanel) -> None:
        assert panel.selected_category() is None

    def test_selected_category_for_a_category_scope(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("A", "Work")
        panel.reload()
        select_category(panel, "Work")
        assert panel.selected_category() == "Work"

    def test_category_rows_are_tagged(
        self, panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("A", "Work")
        panel.reload()
        for row in range(panel._scope.count()):
            item = panel._scope.item(row)
            if item is not None and item.text() == "Work":
                assert item.data(ROLE_KIND) == KIND_CATEGORY
                return
        raise AssertionError("Work not in the scope list")

    def test_clearing_the_selection(self, panel: FilesPanel, notes: MarkdownNoteRepository) -> None:
        notes.create("A", "Work")
        panel.reload()
        select_file(panel, "A")
        panel._list.setCurrentRow(-1)
        assert panel.selected_entry() is None
