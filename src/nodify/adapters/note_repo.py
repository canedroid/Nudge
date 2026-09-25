"""Note persistence.

Notes live at ``notes/[Category]/[Note-Title].md`` and the category of record is
the folder, matching what the user sees when they browse the vault in Obsidian.

Two decisions shape this module.

**The filename is the title, but a rename moves the file.** A note titled
``Ideas.md`` is a file called ``Ideas.md``. Editing the title therefore has to
touch the path, not just the frontmatter, or the two drift apart and the Files
view stops matching the notes view.

**Collisions are resolved, never silently overwritten.** Creating ``Ideas`` twice
in one category yields ``Ideas 2``, not a destroyed first note. A note with an id
is addressed by scanning, so a renamed or externally moved file is still found.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nodify.adapters.document_mapper import document_to_frontmatter, from_frontmatter
from nodify.adapters.vault_paths import InvalidNameError, slugify, validate_name
from nodify.domain.documents import Note
from nodify.domain.ports import (
    CollisionError,
    DocumentNotFoundError,
    VaultError,
)

ID_PREFIX_NOTE = "note_"
UNCATEGORISED = "Uncategorised"

#: Used to build sortable, human-readable ids without a ULID dependency.
_ID_COUNTER_MAX = 0xFFFFFFFF


class _IdGenerator:
    """Generates sortable ids from a clock reading and a counter.

    A ULID is not worth a dependency here. What the ids actually need to be is
    unique, filesystem-safe, and roughly ordered by creation time so that
    "recently added" is a stable sort rather than an accident.
    """

    def __init__(self) -> None:
        self._counter = 0

    def next_id(self, moment: datetime) -> str:
        self._counter = (self._counter + 1) % _ID_COUNTER_MAX
        return f"{ID_PREFIX_NOTE}{int(moment.timestamp()):010d}{self._counter:08x}"


class MarkdownNoteRepository:
    """A :class:`NoteRepository` backed by a vault on disk."""

    def __init__(self, vault: object, clock: object | None = None) -> None:
        # Typed loosely to avoid a circular import with adapters.vault; the
        # argument is a Vault in practice.
        self._vault = vault
        self._ids = _IdGenerator()
        if clock is None:
            from nodify.domain.clock import SystemClock

            self._clock: object = SystemClock()
        else:
            self._clock = clock

    def now(self) -> datetime:
        return self._clock.now()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ paths

    @property
    def _root(self) -> Path:
        return self._vault.root  # type: ignore[attr-defined]

    @property
    def _notes_dir(self) -> Path:
        return self._root / "notes"

    def _note_path(self, category: str, title: str) -> Path:
        folder = validate_name(category) if category else UNCATEGORISED
        stem = slugify(title)
        return self._root / "notes" / folder / f"{stem}.md"

    def _unique_path(self, category: str, title: str) -> Path:
        """A free path for ``title`` in ``category``, without overwriting.

        ``Ideas`` becomes ``Ideas 2``, then ``Ideas 3``. The id scan is not
        consulted here: two notes may legitimately share a title in different
        folders, and the on-disk name is what the user would collide on.
        """
        candidate = self._note_path(category, title)
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        for suffix in range(2, 10_000):
            alternative = candidate.with_name(f"{stem} {suffix}.md")
            if not alternative.exists():
                return alternative
        raise CollisionError(f"cannot find a free file name for {title!r}")

    def _iter_note_files(self) -> list[Path]:
        notes_dir = self._notes_dir
        if not notes_dir.is_dir():
            return []
        return sorted(notes_dir.rglob("*.md"), key=lambda p: str(p).lower())

    # ------------------------------------------------------------------- read

    def _read_note(self, path: Path) -> Note:
        frontmatter, body = self._vault.documents.read(path)  # type: ignore[attr-defined]
        relative = self._vault.resolver.relative_to_vault(path)  # type: ignore[attr-defined]
        document = from_frontmatter(frontmatter, body, relative)
        if not isinstance(document, Note):
            raise DocumentNotFoundError(f"not a note: {path.name}")
        return document

    def get(self, note_id: str) -> Note:
        """Find a note by id, wherever it lives.

        A full scan is acceptable at this scale: a personal notes vault holds
        hundreds of files, and this keeps a note findable even if it was renamed
        or moved outside Nodify.
        """
        for note, _ in self._scan_for(note_id):
            return note
        raise DocumentNotFoundError(f"no note with id {note_id!r}")

    def path_of(self, note_id: str) -> Path:
        """The on-disk path of a note, resolved by id."""
        for _, path in self._scan_for(note_id):
            return path
        raise DocumentNotFoundError(f"no note with id {note_id!r}")

    def _scan_for(self, note_id: str) -> list[tuple[Note, Path]]:
        """Every readable (note, path) pair matching ``note_id``.

        Unreadable files are skipped rather than aborting the scan, for the same
        reason ``list_notes`` skips them.
        """
        found: list[tuple[Note, Path]] = []
        for path in self._iter_note_files():
            try:
                note = self._read_note(path)
            except VaultError:
                continue
            if note.id == note_id:
                found.append((note, path))
        return found

    def list_notes(self, category: str | None = None) -> list[Note]:
        """Notes, newest first, optionally restricted to one category.

        A file that cannot be read is skipped. One malformed or half-written note
        must not make the whole notes list fail to load, which is the difference
        between a recoverable warning and an empty panel. The Files view is where
        an unreadable file gets surfaced.
        """
        notes: list[Note] = []
        for path in self._iter_note_files():
            try:
                notes.append(self._read_note(path))
            except VaultError:
                continue
        if category is not None:
            notes = [n for n in notes if n.category == category]
        return sorted(notes, key=self._sort_key, reverse=True)

    @staticmethod
    def _sort_key(note: Note) -> tuple[datetime, str]:
        """Newest first, with the id as a stable tie-break.

        A note with no ``created_at`` is treated as maximally old rather than
        sorting to one end unpredictably, so a hand-written note does not
        outrank a real one.
        """
        created = note.created_at
        if created is None:
            return datetime.min.replace(tzinfo=UTC), note.id
        return created, note.id

    def recent(self, limit: int = 10) -> list[Note]:
        """The most recently created notes."""
        if limit <= 0:
            return []
        return self.list_notes()[:limit]

    # ------------------------------------------------------------------ write

    def _write(self, note: Note, path: Path, *, created: bool = False) -> None:
        """Persist a note, stamping ``updated_at``.

        On creation ``updated_at`` is left equal to ``created_at``: a note that
        has never been edited has not been edited, and a clock that ticks between
        the two assignments must not make every new note look modified.
        """
        if not created:
            note.updated_at = self.now()
        self._vault.documents.write(  # type: ignore[attr-defined]
            path, document_to_frontmatter(note), note.body
        )

    def create(self, title: str, category: str, body: str = "") -> Note:
        """Create a note, choosing a non-colliding file name."""
        cleaned_title = title.strip()
        if not cleaned_title:
            raise InvalidNameError("a note needs a title")

        # Validate the category before creating anything, so a bad category
        # cannot leave a half-made note behind.
        folder = validate_name(category) if category else UNCATEGORISED

        path = self._unique_path(folder, cleaned_title)
        path.parent.mkdir(parents=True, exist_ok=True)

        created = self.now()
        note = Note(
            id=self._ids.next_id(created),
            title=cleaned_title,
            category=folder,
            body=body,
            created_at=created,
            updated_at=created,
        )
        self._write(note, path, created=True)
        return note

    def update(self, note_id: str, *, title: str | None = None, body: str | None = None) -> Note:
        """Edit a note's title and/or body.

        A changed title renames the file, keeping name and frontmatter in step.
        """
        note = self.get(note_id)
        path = self.path_of(note_id)

        if body is not None:
            note.body = body

        if title is None or title.strip() == note.title:
            self._write(note, path)
            return note

        new_title = title.strip()
        if not new_title:
            raise InvalidNameError("a note needs a title")

        note.title = new_title
        target = path.with_name(f"{slugify(new_title)}.md")
        if target != path and target.exists():
            raise CollisionError(f"a note already exists at {target.name}")

        self._write(note, target)
        if target != path:
            path.unlink()
        return note

    def rename(self, note_id: str, new_title: str) -> Note:
        return self.update(note_id, title=new_title)

    def move(self, note_id: str, new_category: str) -> Note:
        """Move a note to another category folder.

        Only the path changes. The note keeps its id, body, tags and timestamps,
        so a link to it from elsewhere stays valid.
        """
        folder = validate_name(new_category) if new_category else UNCATEGORISED
        note = self.get(note_id)
        source = self.path_of(note_id)

        if note.category == folder:
            return note

        target = self._notes_dir / folder / source.name
        if target.exists():
            raise CollisionError(f"a note already exists in {folder}")

        target.parent.mkdir(parents=True, exist_ok=True)
        note.category = folder
        self._write(note, target)
        source.unlink()
        return note

    def delete(self, note_id: str) -> None:
        """Delete a note and its file.

        The file is the source of truth: if the file is already gone, the note is
        already deleted and this must not raise.
        """
        path = self.path_of(note_id)
        path.unlink(missing_ok=True)
        self._prune_empty_category(path.parent)

    def _prune_empty_category(self, folder: Path) -> None:
        """Remove a category folder once its last note is gone.

        An empty folder is noise in the Files view, and the user did not ask to
        keep it. Only a directly empty category folder is removed, never the
        ``notes`` root.
        """
        if folder == self._notes_dir or folder.parent != self._notes_dir:
            return
        try:
            next(folder.iterdir())
        except StopIteration:
            folder.rmdir()
        except OSError:
            return

    # -------------------------------------------------------------- categories

    def list_categories(self) -> list[str]:
        """Category folders that exist, with at least one note.

        Folders that exist but hold nothing are excluded: they are an artefact of
        a delete, not something the user can select.
        """
        notes_dir = self._notes_dir
        if not notes_dir.is_dir():
            return []
        categories: list[str] = []
        for folder in sorted(notes_dir.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir():
                continue
            if any(folder.glob("*.md")):
                categories.append(folder.name)
        return categories

    # ----------------------------------------------------------------- search

    def search(self, query: str) -> list[Note]:
        """Case-insensitive substring search over titles, body and tags.

        A plain scan is the honest first implementation. The plan reserves a
        content index for the files feature, and building one now would mean
        keeping it in sync with every save.
        """
        needle = query.strip().lower()
        if not needle:
            return []
        return [
            note
            for note in self.list_notes()
            if needle in note.title.lower()
            or needle in note.body.lower()
            or any(needle in tag.lower() for tag in note.tags)
        ]
