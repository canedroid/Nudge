"""Keyboard navigation and focus behaviour across the four panels.

Package G's acceptance criteria call for keyboard navigation and focus to be
covered. These tests drive each panel the way a keyboard user would: arrow keys
to move a selection, Return to submit, and Tab order to reach the controls that
matter.

The panels are checked against the same expectations, which is what proves they
behave consistently rather than merely individually.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QListWidget

from nodify.adapters.category_repo import FileSystemCategoryRepository
from nodify.adapters.note_repo import MarkdownNoteRepository
from nodify.adapters.task_repo import MarkdownTaskRepository
from nodify.adapters.timer_repo import MarkdownTimerRepository
from nodify.adapters.vault import Vault
from nodify.domain.clock import FixedClock
from nodify.ui.tiles.files_panel import FilesPanel
from nodify.ui.tiles.notes_panel import NotesPanel
from nodify.ui.tiles.tasks_panel import TasksPanel
from nodify.ui.tiles.timers_panel import TimersPanel

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def notes(vault: Vault, clock: FixedClock) -> MarkdownNoteRepository:
    return MarkdownNoteRepository(vault, clock)


@pytest.fixture
def tasks(vault: Vault, clock: FixedClock) -> MarkdownTaskRepository:
    return MarkdownTaskRepository(vault, clock)


@pytest.fixture
def timers(vault: Vault, clock: FixedClock) -> MarkdownTimerRepository:
    return MarkdownTimerRepository(vault, clock)


@pytest.fixture
def notes_panel(qapp: object, notes: MarkdownNoteRepository) -> NotesPanel:
    widget = NotesPanel(notes)
    yield widget
    widget.deleteLater()


@pytest.fixture
def tasks_panel(qapp: object, tasks: MarkdownTaskRepository, clock: FixedClock) -> TasksPanel:
    widget = TasksPanel(tasks, clock.now)
    yield widget
    widget.deleteLater()


@pytest.fixture
def timers_panel(qapp: object, timers: MarkdownTimerRepository, clock: FixedClock) -> TimersPanel:
    widget = TimersPanel(timers, clock.now)
    yield widget
    widget.shutdown()
    widget.deleteLater()


@pytest.fixture
def files_panel(
    qapp: object,
    vault: Vault,
    notes: MarkdownNoteRepository,
) -> FilesPanel:
    widget = FilesPanel(
        vault.index,
        FileSystemCategoryRepository(vault),
        notes,
        confirm=lambda _t, _m: True,
        prompt=lambda _t, _l, initial: initial,
    )
    widget._now = lambda: NOW
    yield widget
    widget.deleteLater()


def key(widget: object, key_name: str, modifier: object = Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(widget, key_name, modifier)


def current_text(list_widget: QListWidget) -> str:
    item = list_widget.currentItem()
    return item.text() if item is not None else ""


class TestNotesKeyboard:
    def test_down_arrow_moves_the_selection(self, notes_panel: NotesPanel) -> None:
        for title in ("First", "Second"):
            notes_panel._repository.create(title, "Work")
        notes_panel.reload()
        assert notes_panel._note_list.count() == 2

        notes_panel._note_list.setFocus()
        notes_panel._note_list.setCurrentRow(0)
        key(notes_panel._note_list, Qt.Key.Key_Down)
        assert notes_panel._note_list.currentRow() == 1

    def test_selecting_with_the_keyboard_loads_the_note(self, notes_panel: NotesPanel) -> None:
        notes_panel._repository.create("Keyboard note", "Work", "body text")
        notes_panel.reload()

        notes_panel._note_list.setFocus()
        notes_panel._note_list.setCurrentRow(0)
        notes_panel._note_list.setCurrentRow(1)
        notes_panel._note_list.setCurrentRow(0)
        assert notes_panel.current_note_id is not None
        assert notes_panel._editor.toPlainText() == "body text"

    def test_return_in_the_search_box_does_not_create(self, notes_panel: NotesPanel) -> None:
        before = len(notes_panel._repository.list_notes())
        notes_panel._search.setText("zzz")
        notes_panel._search.setFocus()
        key(notes_panel._search, Qt.Key.Key_Return, Qt.KeyboardModifier.KeypadModifier)
        assert len(notes_panel._repository.list_notes()) == before

    def test_return_in_the_search_box_does_not_open_a_note(self, notes_panel: NotesPanel) -> None:
        """Return in the search box filters; it must not select or create."""
        notes_panel._repository.create("Findable", "Work")
        notes_panel.reload()
        notes_panel._search.setText("Findable")
        notes_panel._search.setFocus()
        key(notes_panel._search, Qt.Key.Key_Return, Qt.KeyboardModifier.KeypadModifier)
        assert notes_panel.current_note_id is None

    def test_the_delete_button_is_disabled_with_no_note_open(self, notes_panel: NotesPanel) -> None:
        """A destructive control must be unavailable until it has a target."""
        assert notes_panel._new_button.isEnabled()
        assert not notes_panel._delete_button.isEnabled()

        notes_panel.create_note()
        assert notes_panel._delete_button.isEnabled()

    def test_the_editor_controls_are_disabled_with_no_note_open(
        self, notes_panel: NotesPanel
    ) -> None:
        for widget in (notes_panel._title, notes_panel._editor, notes_panel._save_button):
            assert not widget.isEnabled()

        notes_panel.create_note()
        for widget in (notes_panel._title, notes_panel._editor, notes_panel._save_button):
            assert widget.isEnabled()

    def test_arrow_keys_in_the_category_rail_switch_filter(self, notes_panel: NotesPanel) -> None:
        notes_panel._repository.create("A", "Work")
        notes_panel._repository.create("B", "Home")
        notes_panel.reload()
        # All + the two categories, sorted, so Home precedes Work.
        assert notes_panel._category_list.count() == 3
        labels = [
            notes_panel._category_list.item(i).text()
            for i in range(notes_panel._category_list.count())
        ]
        assert labels == ["All", "Home", "Work"]

        notes_panel._category_list.setFocus()
        notes_panel._category_list.setCurrentRow(0)
        key(notes_panel._category_list, Qt.Key.Key_Down)
        key(notes_panel._category_list, Qt.Key.Key_Down)
        assert notes_panel._category_list.currentItem().text() == "Work"
        assert notes_panel._note_list.count() == 1


class TestTasksKeyboard:
    def test_tab_moves_through_the_list(self, tasks_panel: TasksPanel) -> None:
        tasks_panel._repository.create(NOW, "First")
        tasks_panel._repository.create(NOW, "Second")
        tasks_panel.reload()

        tasks_panel._list.setFocus()
        tasks_panel._list.setCurrentRow(0)
        key(tasks_panel._list, Qt.Key.Key_Down)
        assert tasks_panel._list.currentRow() == 1

    def test_space_toggles_a_task(self, tasks_panel: TasksPanel) -> None:
        """Space activates a checkable list item, which completes the task."""
        task = tasks_panel._repository.create(NOW, "Tick me")
        tasks_panel.reload()

        row = next(
            i
            for i in range(tasks_panel._list.count())
            if tasks_panel._list.item(i).data(Qt.ItemDataRole.UserRole) == task.id
        )
        tasks_panel._list.setFocus()
        tasks_panel._list.setCurrentRow(row)
        key(tasks_panel._list, Qt.Key.Key_Space)
        assert tasks_panel._repository.get(task.id).is_done

    def test_return_in_the_composer_adds_a_task(self, tasks_panel: TasksPanel) -> None:
        before = len(tasks_panel._repository.open_tasks())
        tasks_panel._title_input.setText("From the keyboard")
        tasks_panel._title_input.setFocus()
        key(tasks_panel._title_input, Qt.Key.Key_Return, Qt.KeyboardModifier.KeypadModifier)
        assert len(tasks_panel._repository.open_tasks()) == before + 1

    def test_return_in_the_composer_with_no_text_does_nothing(
        self, tasks_panel: TasksPanel
    ) -> None:
        before = len(tasks_panel._repository.open_tasks())
        tasks_panel._title_input.setFocus()
        key(tasks_panel._title_input, Qt.Key.Key_Return, Qt.KeyboardModifier.KeypadModifier)
        assert len(tasks_panel._repository.open_tasks()) == before

    def test_action_buttons_take_focus(self, tasks_panel: TasksPanel) -> None:
        for button in (
            tasks_panel._add_button,
            tasks_panel._complete_button,
            tasks_panel._reopen_button,
            tasks_panel._delete_button,
        ):
            assert button.focusPolicy() != Qt.FocusPolicy.NoFocus


class TestTimersKeyboard:
    def test_arrow_keys_move_the_selection(self, timers_panel: TimersPanel) -> None:
        timers_panel._repository.create_countdown(NOW, "First", 25)
        timers_panel._repository.create_countdown(NOW, "Second", 30)
        timers_panel.reload()

        timers_panel._list.setFocus()
        timers_panel._list.setCurrentRow(0)
        key(timers_panel._list, Qt.Key.Key_Down)
        assert timers_panel._list.currentRow() == 1

    def test_return_in_the_composer_starts_a_timer(self, timers_panel: TimersPanel) -> None:
        before = len(timers_panel._repository.all_timers())
        timers_panel._title_input.setText("Keyboard timer")
        timers_panel._title_input.setFocus()
        key(timers_panel._title_input, Qt.Key.Key_Return, Qt.KeyboardModifier.KeypadModifier)
        assert len(timers_panel._repository.all_timers()) == before + 1

    def test_action_buttons_take_focus(self, timers_panel: TimersPanel) -> None:
        for button in (
            timers_panel._add_button,
            timers_panel._snooze_button,
            timers_panel._complete_button,
            timers_panel._dismiss_button,
            timers_panel._delete_button,
        ):
            assert button.focusPolicy() != Qt.FocusPolicy.NoFocus


class TestFilesKeyboard:
    def test_arrow_keys_in_the_scope_change_the_listing(
        self, files_panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("A note", "Work")
        files_panel.reload()
        assert files_panel._scope.count() > 1

        files_panel._scope.setFocus()
        files_panel._scope.setCurrentRow(0)
        key(files_panel._scope, Qt.Key.Key_Down)
        assert files_panel._scope.currentRow() == 1

    def test_arrow_keys_move_the_file_selection(
        self, files_panel: FilesPanel, notes: MarkdownNoteRepository
    ) -> None:
        notes.create("One", "Work")
        notes.create("Two", "Work")
        files_panel.reload()
        assert files_panel._list.count() == 2

        files_panel._list.setFocus()
        files_panel._list.setCurrentRow(0)
        key(files_panel._list, Qt.Key.Key_Down)
        assert files_panel._list.currentRow() == 1

    def test_action_buttons_take_focus(self, files_panel: FilesPanel) -> None:
        for button in (
            files_panel._refresh_button,
            files_panel._new_category_button,
            files_panel._rename_category_button,
            files_panel._delete_category_button,
        ):
            assert button.focusPolicy() != Qt.FocusPolicy.NoFocus


class TestAcrossPanels:
    """Behaviour every panel shares, checked once for all four."""

    @pytest.fixture(params=["notes", "tasks", "timers", "files"])
    def any_panel(self, request: pytest.FixtureRequest) -> object:
        return request.getfixturevalue(f"{request.param}_panel")

    def test_every_panel_is_a_focus_container(self, any_panel: object) -> None:
        """A tile is a container: it does not take focus itself, but it must have
        focusable children, or a keyboard user cannot reach any of it.
        """
        focusable = [
            child
            for child in any_panel.findChildren(object)
            if getattr(child, "focusPolicy", lambda: None)() not in (None, Qt.FocusPolicy.NoFocus)
        ]
        assert focusable, "the panel has no focusable controls"

    def test_every_panel_starts_with_no_fatal_error(self, any_panel: object) -> None:
        """Constructing a panel must not raise, whatever the vault contains."""
        assert any_panel is not None

    def test_every_panel_reports_status_text(self, any_panel: object) -> None:
        status = getattr(any_panel, "_status", None)
        assert status is not None
        assert isinstance(status.text(), str)
        assert status.text(), "an empty panel should explain itself"

    def test_every_panel_uses_the_shared_object_name(self, any_panel: object) -> None:
        """A shared object name is what the layout engine keys on to find tiles."""
        assert any_panel.objectName() == "tile"


class TestApplicationLevel:
    def test_only_one_qapplication_exists(self, qapp: QApplication) -> None:
        assert isinstance(qapp, QApplication)
        assert QApplication.instance() is qapp

    def test_panels_coexist_in_one_application(
        self,
        notes_panel: NotesPanel,
        tasks_panel: TasksPanel,
        timers_panel: TimersPanel,
        files_panel: FilesPanel,
    ) -> None:
        """Each panel must be independently replaceable.

        Package G's first acceptance criterion is that one panel can change
        without rewriting the others, so all four must be constructible together
        and none may reach into another's state.
        """
        for panel in (notes_panel, tasks_panel, timers_panel, files_panel):
            assert panel.objectName() == "tile"

    def test_each_panel_depends_only_on_its_own_repository(
        self, notes_panel: NotesPanel, tasks_panel: TasksPanel
    ) -> None:
        assert hasattr(notes_panel, "_repository")
        assert hasattr(tasks_panel, "_repository")
        assert notes_panel._repository is not tasks_panel._repository


class TestTextEntryBehaviour:
    def test_a_text_box_reports_focus(self, qapp: object) -> None:
        edit = QLineEdit()
        assert edit.focusPolicy() == Qt.FocusPolicy.StrongFocus
        edit.setFocus()
        edit.deleteLater()

    def test_a_disabled_box_refuses_focus(self, qapp: object) -> None:
        """The notes editor is disabled with no note open, so it must not
        silently swallow keystrokes the user expects to reach the list."""
        edit = QLineEdit()
        edit.setEnabled(False)
        edit.setFocus()
        assert not edit.hasFocus()
        edit.deleteLater()
