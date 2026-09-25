"""The Notes tile.

Three panes: a category rail, the note list for the selected category, and the
editor. The editor is the source of truth while it holds focus, so a save is
explicit and a half-typed note is never written to disk behind the user's back.

Nothing here talks to the filesystem. The panel depends on the
:class:`~nodify.domain.ports.NoteRepository` protocol, so it can be driven in a
test with a fake and re-pointed at another backend later.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nodify.domain.documents import Note
from nodify.domain.ports import NoteRepository
from nodify.styles import app_qss

_T = TypeVar("_T")

ALL_CATEGORIES = "All"
UNCATEGORISED_LABEL = "Uncategorised"


class _Failure:
    """Sentinel returned when a repository call raised.

    A distinct sentinel is required because several repository methods return
    ``None`` on success, so ``None`` cannot double as a failure signal. It is a
    class rather than a bare ``object()`` so that the type is a real type and
    ``is`` comparisons narrow correctly.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<repository call failed>"


FAILED = _Failure()


class NotesPanel(QWidget):
    """Note browsing and editing for one vault."""

    #: Emitted after any mutation, so other panels can refresh.
    notes_changed = pyqtSignal()
    #: A recoverable problem worth showing the user. Never fatal.
    error_occurred = pyqtSignal(str)

    def __init__(self, repository: NoteRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tile")
        self._repository = repository
        self._current_id: str | None = None
        self._loading = False
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        self.setStyleSheet(app_qss.tile_qss() + app_qss.notes_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(8)
        body.addWidget(self._build_category_rail(), 0)
        body.addWidget(self._build_note_list(), 1)
        body.addWidget(self._build_editor(), 2)
        outer.addLayout(body, 1)

    def _build_header(self) -> QWidget:
        header = QLabel("NOTES")
        header.setObjectName("tileTitle")
        return header

    def _build_category_rail(self) -> QWidget:
        self._category_list = QListWidget()
        self._category_list.setFixedWidth(104)
        self._category_list.setObjectName("categoryRail")
        self._category_list.currentTextChanged.connect(self._on_category_changed)
        return self._category_list

    def _build_note_list(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search)

        self._note_list = QListWidget()
        self._note_list.currentItemChanged.connect(self._on_note_selected)
        layout.addWidget(self._note_list, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._new_button = QPushButton("NEW")
        self._new_button.setObjectName("primary")
        self._new_button.clicked.connect(self.create_note)
        self._delete_button = QPushButton("DELETE")
        self._delete_button.setObjectName("danger")
        self._delete_button.clicked.connect(self.delete_current_note)
        row.addWidget(self._new_button)
        row.addWidget(self._delete_button)
        layout.addLayout(row)

        return container

    def _build_editor(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._title = QLineEdit()
        self._title.setPlaceholderText("Title")
        self._title.textChanged.connect(self._on_title_changed)
        layout.addWidget(self._title)

        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText("Write something.")
        self._editor.textChanged.connect(self._on_body_changed)
        layout.addWidget(self._editor, 1)

        self._status = QLabel("")
        self._status.setObjectName("tileHint")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._save_button = QPushButton("SAVE")
        self._save_button.setObjectName("primary")
        self._save_button.clicked.connect(self.save_current_note)
        layout.addWidget(self._save_button)

        self._set_editor_enabled(False)
        return container

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (self._title, self._editor, self._save_button):
            widget.setEnabled(enabled)
        self._delete_button.setEnabled(enabled)

    def _call(self, action: Callable[[], _T], message: str) -> _T | _Failure:
        """Run a repository call, turning any failure into a user-visible message.

        A panel must not die because a file is malformed, a folder is locked or
        the drive is full. The protocol promises ``VaultError``, but a panel that
        hard-crashes on any other exception gives the user a blank window and no
        explanation, so the catch is deliberately wider here than at the domain
        boundary.

        Returns :data:`FAILED` on error, because a legitimate ``None`` return
        cannot be distinguished from one.
        """
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - see docstring
            self.error_occurred.emit(f"{message}: {exc}")
            return FAILED

    # ---------------------------------------------------------------- reload

    def reload(self) -> None:
        """Re-read everything from the repository."""
        self._loading = True
        try:
            self._reload_categories()
            self._reload_notes()
        finally:
            self._loading = False

    def _reload_categories(self) -> None:
        previous = self._category_list.currentItem()
        keep = previous.text() if previous is not None else ALL_CATEGORIES

        categories = self._call(self._repository.list_categories, "Cannot read categories")
        names: list[str] = [] if isinstance(categories, _Failure) else list(categories)

        self._category_list.blockSignals(True)
        self._category_list.clear()
        self._category_list.addItem(ALL_CATEGORIES)
        for category in names:
            self._category_list.addItem(category)

        for row in range(self._category_list.count()):
            item = self._category_list.item(row)
            if item is not None and item.text() == keep:
                self._category_list.setCurrentRow(row)
                break
        else:
            self._category_list.setCurrentRow(0)
        self._category_list.blockSignals(False)

    def _reload_notes(self) -> None:
        self._note_list.blockSignals(True)
        self._note_list.clear()
        for note in self._visible_notes():
            item = QListWidgetItem(note.title or "Untitled")
            item.setData(Qt.ItemDataRole.UserRole, note.id)
            self._note_list.addItem(item)
        self._note_list.blockSignals(False)
        self._update_empty_state()

    def _visible_notes(self) -> list[Note]:
        """The notes for the current category and search text."""
        query = self._search.text().strip()
        category = self._selected_category()

        if query:
            result = self._call(lambda: self._repository.search(query), "Cannot search notes")
        elif category is None:
            result = self._call(lambda: self._repository.list_notes(), "Cannot read notes")
        else:
            result = self._call(lambda: self._repository.list_notes(category), "Cannot read notes")
        if isinstance(result, _Failure):
            return []
        return list(result)

    def _selected_category(self) -> str | None:
        item = self._category_list.currentItem()
        if item is None or item.text() == ALL_CATEGORIES:
            return None
        return item.text()

    def _update_empty_state(self) -> None:
        if self._note_list.count() == 0:
            if self._search.text().strip():
                self._status.setText("No notes match that search.")
            elif self._selected_category() is None:
                self._status.setText("No notes yet. Press NEW to write one.")
            else:
                self._status.setText("This category is empty.")

    # ---------------------------------------------------------------- events

    def _on_category_changed(self, _text: str) -> None:
        if self._loading:
            return
        self.clear_editor()
        self._reload_notes()
        # Reloading the list repopulates the status for the new category, so the
        # empty-state hint has to be recomputed after, not before.
        self._update_empty_state()

    def _on_search_changed(self, _text: str) -> None:
        if self._loading:
            return
        self._reload_notes()

    def _on_note_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if self._loading:
            return
        if current is None:
            self.clear_editor()
            return

        note_id = current.data(Qt.ItemDataRole.UserRole)
        if note_id is None:
            return
        self.load_note(note_id)

    def _on_title_changed(self, _text: str) -> None:
        self._mark_dirty()

    def _on_body_changed(self) -> None:
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        if self._loading or self._current_id is None:
            return
        self._status.setText("Unsaved changes.")

    # ------------------------------------------------------------ operations

    @property
    def current_note_id(self) -> str | None:
        return self._current_id

    def load_note(self, note_id: str) -> None:
        """Show a note in the editor."""
        note = self._call(lambda: self._repository.get(note_id), "Cannot open that note")
        if isinstance(note, _Failure):
            return

        self._loading = True
        try:
            self._current_id = note.id
            self._title.setText(note.title)
            self._editor.setPlainText(note.body)
            self._status.setText(self._format_timestamp(note.updated_at))
        finally:
            self._loading = False
        self._set_editor_enabled(True)

    def clear_editor(self) -> None:
        self._loading = True
        try:
            self._current_id = None
            self._title.clear()
            self._editor.clear()
            self._status.setText("")
        finally:
            self._loading = False
        self._set_editor_enabled(False)
        self._update_empty_state()

    def create_note(self) -> None:
        """Create a note in the selected category and open it."""
        category = self._selected_category() or "General"
        note = self._call(
            lambda: self._repository.create("Untitled", category), "Cannot create a note"
        )
        if isinstance(note, _Failure):
            return

        self.reload()
        self._select_in_list(note.id)
        self.notes_changed.emit()

    def save_current_note(self) -> bool:
        """Write the editor's contents back to the vault."""
        if self._current_id is None:
            return False

        title = self._title.text()
        if not title.strip():
            self.error_occurred.emit("A note needs a title.")
            return False

        body = self._editor.toPlainText()
        note_id = self._current_id
        saved = self._call(
            lambda: self._repository.update(note_id, title=title, body=body), "Cannot save"
        )
        if isinstance(saved, _Failure):
            return False

        self._current_id = saved.id
        self.reload()
        self._select_in_list(saved.id)
        self._status.setText("Saved.")
        self.notes_changed.emit()
        return True

    def delete_current_note(self) -> bool:
        """Delete the open note and clear the editor."""
        if self._current_id is None:
            return False
        note_id = self._current_id
        outcome = self._call(lambda: self._repository.delete(note_id), "Cannot delete")
        if isinstance(outcome, _Failure):
            return False

        self.clear_editor()
        self.reload()
        self.notes_changed.emit()
        return True

    def _select_in_list(self, note_id: str) -> None:
        for row in range(self._note_list.count()):
            item = self._note_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == note_id:
                self._note_list.setCurrentItem(item)
                return

    @staticmethod
    def _format_timestamp(moment: object) -> str:
        if moment is None:
            return ""
        from datetime import datetime

        if not isinstance(moment, datetime):
            return ""
        return moment.astimezone().strftime("%d %b %Y, %H:%M")
