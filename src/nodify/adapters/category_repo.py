"""Note category management.

Categories are plain folders under ``notes/``. There is no category registry file,
deliberately: the plan's vault contract is that the folder structure *is* the
data, so a user can create a category by dragging a folder into their vault from
anywhere, including Obsidian, and Nodify will list it.

That has one consequence worth stating. Because the folder is the record,
"renaming a category" means renaming a folder, and a category that already exists
is not an error to create but a no-op.
"""

from __future__ import annotations

from pathlib import Path

from nodify.adapters.vault_paths import InvalidNameError, validate_name
from nodify.domain.ports import CollisionError, DocumentNotFoundError


class FileSystemCategoryRepository:
    """Create, rename and remove note category folders."""

    def __init__(self, vault: object) -> None:
        # Loosely typed to avoid a circular import with adapters.vault.
        self._vault = vault

    @property
    def _notes_dir(self) -> Path:
        return self._vault.root / "notes"  # type: ignore[attr-defined]

    def _documents(self) -> object:
        return self._vault.documents  # type: ignore[attr-defined]

    def _path(self, name: str) -> Path:
        return self._notes_dir / validate_name(name)

    def exists(self, name: str) -> bool:
        return self._path(name).is_dir()

    def create(self, name: str) -> str:
        """Create a category folder and return its cleaned name.

        Creating an existing category is a no-op rather than an error: the user
        clicking "new category" on a name they already typed should get the
        category they asked for, not an error dialog.
        """
        folder = validate_name(name)
        path = self._path(folder)
        path.mkdir(parents=True, exist_ok=True)
        return folder

    def rename(self, old: str, new: str) -> None:
        """Rename a category folder, refusing to merge into an existing one.

        Merging would mean moving files from one folder into another, which can
        collide on file names. Refusing is recoverable; silently merging two
        categories is not.
        """
        source = self._path(old)
        if not source.is_dir():
            raise DocumentNotFoundError(f"no such category: {old}")

        target = self._path(new)
        if target == source:
            return
        if target.exists():
            raise CollisionError(f"a category named {validate_name(new)!r} already exists")

        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)

    def remove(self, name: str) -> None:
        """Remove an empty category folder.

        A folder holding notes is not removed. Deleting notes is the note
        feature's decision to make, with a confirmation, not something a folder
        operation should do behind the user's back.
        """
        path = self._path(name)
        if not path.is_dir():
            raise DocumentNotFoundError(f"no such category: {name}")

        if any(path.iterdir()):
            raise CollisionError(f"category {name!r} still contains notes")

        path.rmdir()

    def ensure_note_root(self) -> None:
        """Make sure ``notes/`` exists before any category operation."""
        try:
            self._notes_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # pragma: no cover - depends on the filesystem
            raise InvalidNameError(f"cannot create the notes folder: {exc}") from exc
