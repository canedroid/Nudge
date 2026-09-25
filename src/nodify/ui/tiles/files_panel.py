"""The Files tile.

A browser for the vault's own structure: the three top-level areas, note
categories beneath them, and the files in each. Categories come from the
filesystem, never from a second store, so a folder created in Obsidian appears
here without Nodify knowing anything about it.

Two behaviours the acceptance criteria name are worth stating.

**A move goes through the notes repository, not the filesystem.** Moving a note to
another category calls ``NoteRepository.move``, so the note keeps its id, body,
timestamps and unknown frontmatter. A raw file move would silently orphan them.

**Destructive actions confirm, and failures are recoverable.** Confirmation is an
injected callable rather than a dialog constructed inline, so it can be driven
from a test, and deleting a category that still holds notes is refused by the
repository and surfaced as a message rather than performed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nodify.domain.ports import CategoryRepository, FileEntry, FileIndex, NoteRepository
from nodify.styles import app_qss

_T = TypeVar("_T")

#: Item roles. One place, so a row can be classified without parsing its text.
ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_AREA = Qt.ItemDataRole.UserRole + 2

KIND_CATEGORY = "category"
KIND_FILE = "file"

ALL_FILES = "All files"

VAULT_AREAS = ("notes", "todos", "timer")

#: Injected so tests can answer. Both default to a modal dialog in production.
ConfirmFn = Callable[[str, str], bool]
PromptFn = Callable[[str, str, str], "str | None"]


def default_confirm(title: str, message: str) -> bool:
    """Ask the user, defaulting to No.

    Defaulting to No matters: a dialog defaulting to Yes turns a stray Return
    keypress into a deletion.
    """
    answer = QMessageBox.warning(
        None,
        title,
        message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def default_prompt(title: str, label: str, initial: str) -> str | None:
    """Ask for a line of text, or ``None`` when cancelled."""
    text, accepted = QInputDialog.getText(None, title, label, QLineEdit.EchoMode.Normal, initial)
    if not accepted:
        return None
    return text.strip() or None


class _Failure:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<repository call failed>"


FAILED = _Failure()


def describe_age(modified: datetime, *, now: datetime | None = None) -> str:
    """A short relative age such as ``3h ago``."""
    moment = now or datetime.now(UTC)
    seconds = int((moment - modified).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


class FilesPanel(QWidget):
    """Browse areas, categories and files; manage categories; move notes."""

    files_changed = pyqtSignal()
    error_occurred = pyqtSignal(str)
    status_message = pyqtSignal(str)

    def __init__(
        self,
        file_index: FileIndex,
        categories: CategoryRepository,
        notes: NoteRepository,
        confirm: ConfirmFn = default_confirm,
        prompt: PromptFn = default_prompt,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tile")
        self._index = file_index
        self._categories = categories
        self._notes = notes
        self._confirm = confirm
        self._prompt = prompt
        self._entries: list[FileEntry] = []
        self._now: Callable[[], datetime] = lambda: datetime.now(UTC)
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        self.setStyleSheet(app_qss.tile_qss() + app_qss.notes_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        title = QLabel("FILES")
        title.setObjectName("tileTitle")
        outer.addWidget(title)

        outer.addWidget(self._build_list(), 1)
        outer.addLayout(self._build_actions())

        self._status = QLabel("")
        self._status.setObjectName("tileHint")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

    def _build_list(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._scope = QListWidget()
        self._scope.setFixedWidth(126)
        self._scope.currentItemChanged.connect(self._on_scope_changed)
        layout.addWidget(self._scope)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

        return container

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._refresh_button = QPushButton("REFRESH")
        self._refresh_button.clicked.connect(self.refresh)

        self._new_category_button = QPushButton("NEW CATEGORY")
        self._new_category_button.clicked.connect(self.create_category)

        self._rename_category_button = QPushButton("RENAME")
        self._rename_category_button.clicked.connect(self.rename_category)

        self._delete_category_button = QPushButton("DELETE")
        self._delete_category_button.setObjectName("danger")
        self._delete_category_button.clicked.connect(self.delete_category)

        for button in (
            self._refresh_button,
            self._new_category_button,
            self._rename_category_button,
            self._delete_category_button,
        ):
            row.addWidget(button)
        return row

    def _call(self, action: Callable[[], _T], message: str) -> _T | _Failure:
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - a panel must not crash
            self.error_occurred.emit(f"{message}: {exc}")
            return FAILED

    # --------------------------------------------------------------- display

    def reload(self) -> None:
        """Full re-read of the index and the category list."""
        entries = self._call(self._index.refresh, "Cannot read the vault")
        self._entries = [] if isinstance(entries, _Failure) else list(entries)
        self._reload_scope()
        self._populate()

    def refresh(self) -> None:
        """Report what changed since the last listing, then re-read.

        A file watcher is only a prompt to re-scan, because its events are lossy
        under load, so the index is authoritative and the diff is only there to
        tell the user what happened.
        """
        previous = self._entries
        entries = self._call(self._index.refresh, "Cannot read the vault")
        if isinstance(entries, _Failure):
            return
        changes = self._call(
            lambda: self._index.changes_since(previous), "Cannot compare the vault"
        )
        self._entries = list(entries)
        self._reload_scope()
        self._populate()

        if isinstance(changes, _Failure):
            return
        parts: list[str] = []
        if changes.added:
            parts.append(f"{len(changes.added)} added")
        if changes.modified:
            parts.append(f"{len(changes.modified)} changed")
        if changes.removed:
            parts.append(f"{len(changes.removed)} removed")
        self._status.setText(", ".join(parts) if parts else "Up to date")

    def _reload_scope(self) -> None:
        previous = self._scope.currentItem()
        keep = previous.text() if previous is not None else ALL_FILES

        # The category repository reports the folders that actually exist,
        # including empty ones. That matters here: a folder just created, or one
        # emptied by deleting its last note, still has to be selectable so it can
        # be renamed or removed. The Notes panel deliberately hides empty
        # categories, because a category with no notes is not something to pick.
        listed = self._call(self._categories.list, "Cannot read categories")
        names = [] if isinstance(listed, _Failure) else list(listed)

        self._scope.blockSignals(True)
        self._scope.clear()
        self._scope.addItem(ALL_FILES)
        for area in VAULT_AREAS:
            self._scope.addItem(area)
        for name in names:
            item = QListWidgetItem(name)
            item.setData(ROLE_KIND, KIND_CATEGORY)
            self._scope.addItem(item)

        for row in range(self._scope.count()):
            candidate = self._scope.item(row)
            if candidate is not None and candidate.text() == keep:
                self._scope.setCurrentRow(row)
                break
        else:
            self._scope.setCurrentRow(0)
        self._scope.blockSignals(False)

    def _populate(self) -> None:
        """Rebuild the file list for the selected scope."""
        scope_item = self._scope.currentItem()
        scope = scope_item.text() if scope_item is not None else ALL_FILES

        self._list.blockSignals(True)
        self._list.clear()

        visible = sorted(
            (entry for entry in self._entries if _matches(entry, scope)),
            key=lambda e: str(e.relative_path).lower(),
        )
        for entry in visible:
            item = QListWidgetItem(self._label_for(entry))
            item.setData(ROLE_KIND, KIND_FILE)
            item.setData(ROLE_ID, str(entry.relative_path))
            item.setData(ROLE_AREA, entry.area or "")
            self._list.addItem(item)

        self._list.blockSignals(False)
        if not visible:
            self._status.setText("No files here yet. Create a note or a task to get started.")

    def _label_for(self, entry: FileEntry) -> str:
        age = describe_age(entry.modified_at, now=self._now())
        base = f"[{entry.area or '?'}] {entry.relative_path}"
        return f"{base}  ·  {age}" if age else base

    def _on_scope_changed(
        self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        self._populate()

    def _on_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        detail = f"{entry.relative_path} · {entry.size} bytes"
        if entry.day is not None:
            detail += f" · {entry.day.strftime('%d %b %Y')}"
        self._status.setText(detail)

    # ------------------------------------------------------------ selection

    def selected_entry(self) -> FileEntry | None:
        """The file the user has selected, if any."""
        item = self._list.currentItem()
        if item is None:
            return None
        relative = item.data(ROLE_ID)
        if not isinstance(relative, str):
            return None
        for entry in self._entries:
            if str(entry.relative_path) == relative:
                return entry
        return None

    def selected_category(self) -> str | None:
        """The category scope, if one is selected."""
        item = self._scope.currentItem()
        if item is None or item.data(ROLE_KIND) != KIND_CATEGORY:
            return None
        return item.text()

    def select_category(self, name: str) -> bool:
        """Select a category scope by name, for a caller driving the panel."""
        for row in range(self._scope.count()):
            item = self._scope.item(row)
            if item is not None and item.data(ROLE_KIND) == KIND_CATEGORY and item.text() == name:
                self._scope.setCurrentRow(row)
                return True
        return False

    # ------------------------------------------------------------ categories

    def create_category(self, name: str | None = None) -> bool:
        """Create a category, prompting for a name when one is not supplied."""
        label = name if name is not None else self._prompt("New category", "Category name:", "")
        if not label:
            return False

        created = self._call(lambda: self._categories.create(label), "Cannot create")
        if isinstance(created, _Failure):
            return False

        self.reload()
        self._status.setText(f"Created {created}.")
        self.status_message.emit(f"Category {created} created.")
        return True

    def rename_category(self, new_name: str | None = None) -> bool:
        """Rename the selected category, carrying its notes with the folder."""
        current = self.selected_category()
        if current is None:
            self.error_occurred.emit("Select a category to rename.")
            return False

        replacement = (
            new_name
            if new_name is not None
            else self._prompt("Rename category", f"Rename {current} to:", current)
        )
        if not replacement or replacement == current:
            return False

        outcome = self._call(lambda: self._categories.rename(current, replacement), "Cannot rename")
        if isinstance(outcome, _Failure):
            return False

        self.reload()
        self.select_category(replacement)
        self._status.setText(f"Renamed {current} to {replacement}.")
        self.status_message.emit(f"Category {current} renamed.")
        return True

    def delete_category(self, confirmed: bool | None = None) -> bool:
        """Remove the selected category, only when empty and confirmed.

        A non-empty category is refused by the repository. That refusal is a
        recoverable error, not a reason to delete notes.
        """
        current = self.selected_category()
        if current is None:
            self.error_occurred.emit("Select a category to delete.")
            return False

        if confirmed is None:
            confirmed = self._confirm(
                "Delete category",
                f"Delete the empty category '{current}'?\n\n"
                "A category that still contains notes cannot be deleted.",
            )
        if not confirmed:
            return False

        outcome = self._call(lambda: self._categories.remove(current), "Cannot delete the category")
        if isinstance(outcome, _Failure):
            return False

        self.reload()
        self._status.setText(f"Deleted {current}.")
        self.status_message.emit(f"Category {current} deleted.")
        return True

    # ------------------------------------------------------------------ move

    def move_selected_note(self, target_category: str) -> bool:
        """Move the selected note into ``target_category``.

        Delegated to the notes repository so the note's id, body, timestamps and
        unknown frontmatter are preserved. A raw file move would orphan them.
        """
        entry = self.selected_entry()
        if entry is None or entry.area != "notes":
            self.error_occurred.emit("Select a note first.")
            return False
        if entry.category == target_category:
            return False

        note_id = self._call(lambda: self._note_id_at(entry), "Cannot read the note")
        if note_id is None or isinstance(note_id, _Failure):
            return False

        outcome = self._call(
            lambda: self._notes.move(note_id, target_category), "Cannot move the note"
        )
        if isinstance(outcome, _Failure):
            return False

        self.reload()
        self._status.setText(f"Moved {entry.name} to {target_category}.")
        self.files_changed.emit()
        return True

    def _note_id_at(self, entry: FileEntry) -> str | None:
        """The id of a note, read through the notes repository.

        The file index deliberately does not parse Markdown, so the id has to come
        from the notes side. ``note_id_at`` is a repository capability rather than
        something the panel guesses at from the file name.
        """
        finder = getattr(self._notes, "note_id_at", None)
        if not callable(finder):
            self.error_occurred.emit("This vault cannot move notes from here.")
            return None
        found = self._call(lambda: finder(entry.relative_path), "Cannot read the note")
        return None if isinstance(found, _Failure) else found


def _matches(entry: FileEntry, scope: str) -> bool:
    """Whether an entry belongs in the given scope."""
    if scope == ALL_FILES:
        return True
    if scope in VAULT_AREAS:
        return entry.area == scope
    return entry.category == scope
