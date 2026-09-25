"""Day files: one file holds many records.

The plan gives tasks and timers the same shape: ``todos/[YYYY-MM]/[DD-YYYY].md``
and ``timer/[YYYY-MM]/[DD-YYYY].md``, each holding a structured YAML sequence
plus an Obsidian-friendly body. That is one concept, so it is one class.

Four properties are deliberate.

**The YAML sequence is the record; the Markdown body is a rendering.** A user who
edits the body does not have that read back, because the body is regenerated on
every save. The alternative, parsing the body, makes the file ambiguous and
round-trips badly. The file stays a valid note either way.

**Records are stored as plain mappings and are only rewritten when edited.** A
record handed out by :meth:`RecordDayFile.documents` does not alias the stored
mapping, so saving after editing one record leaves the others byte-identical
instead of normalising them. This is what makes "editing one task does not corrupt
others in the same day file" true rather than merely likely.

**Unknown keys inside a record survive an edit.** A user or third-party tool that
adds ``estimate: 30m`` to one record does not lose it when Nodify saves that file.

**A malformed record is skipped, not fatal.** One bad entry must not cost the user
the records around it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from nodify.adapters.document_mapper import document_to_frontmatter, from_frontmatter
from nodify.domain.documents import Document, DocumentType, Task, TaskStatus, Timer
from nodify.domain.ports import VaultError

TASKS_KEY = "tasks"
TIMERS_KEY = "timers"
DAY_TYPE = "day"
DAY_TITLE_KEY = "day_title"


def day_folder(day: datetime) -> str:
    """``2026-09`` for the month containing ``day``."""
    return f"{day.year:04d}-{day.month:02d}"


def day_file_name(day: datetime) -> str:
    """``25-2026.md`` for the day of ``day``."""
    return f"{day.day:02d}-{day.year:04d}.md"


def normalise_day(day: datetime) -> datetime:
    """Midnight UTC on the given day, which is the file's identity."""
    if day.tzinfo is None:
        raise VaultError("a day must be timezone-aware")
    return day.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class RecordDayFile:
    """Many records of one type, stored together in a single day's file.

    Subclasses supply the area folder, the sequence key, the document type they
    hold, and how a body is rendered from the records.
    """

    #: Top-level vault area, e.g. ``todos``.
    AREA = "todos"
    #: Frontmatter key holding the record sequence, e.g. ``tasks``.
    SEQUENCE_KEY = TASKS_KEY
    #: The document type each record declares.
    DOCUMENT_TYPE = DocumentType.TASK

    def __init__(self, day: datetime, root: Path) -> None:
        self.day = normalise_day(day)
        self._root = root
        self._records: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []

    # ------------------------------------------------------------------ path

    @property
    def relative_path(self) -> Path:
        return Path(self.AREA) / day_folder(self.day) / day_file_name(self.day)

    @property
    def path(self) -> Path:
        return self._root / self.relative_path

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, documents: Any, root: Path, day: datetime) -> Self:
        """Read the day file, or start an empty one if it does not exist.

        ``Self`` so that ``TaskDayFile.load(...)`` is typed as a
        :class:`TaskDayFile` rather than the base class, which would erase the
        narrowing that ``tasks()`` and ``timers()`` provide.
        """
        day_file = cls(day, root)
        if not day_file.exists:
            return day_file

        frontmatter, _ = documents.read(day_file.path)
        raw_records = frontmatter.get(cls.SEQUENCE_KEY)
        if not isinstance(raw_records, list):
            return day_file

        for entry in raw_records:
            if not isinstance(entry, dict):
                # One malformed record must not lose the records around it.
                continue
            record_id = str(entry.get("id", "")).strip()
            if not record_id:
                continue
            day_file._records[record_id] = dict(entry)
            day_file._order.append(record_id)
        return day_file

    # --------------------------------------------------------------- records

    def __len__(self) -> int:
        return len(self._order)

    def has(self, record_id: str) -> bool:
        return record_id in self._records

    def record(self, record_id: str) -> dict[str, Any]:
        """A copy of the stored mapping for a record."""
        return dict(self._records[record_id])

    def record_by_title(self, title: str) -> dict[str, Any] | None:
        """The stored mapping for the record with this exact title.

        A convenience for tests and diagnostics, where addressing a record by its
        generated id would obscure what is being checked.
        """
        for record_id in self._order:
            record = self._records.get(record_id)
            if record is not None and str(record.get("title", "")) == title:
                return dict(record)
        return None

    def add(self, record: dict[str, Any]) -> None:
        """Append a record. Order is creation order within the day."""
        self.put(record)

    def put(self, record: dict[str, Any]) -> None:
        """Replace a record in place, keeping its position in the day.

        Editing a record must not move it to the end, so callers updating an
        existing record use this rather than ``remove`` then ``add``.
        """
        record_id = str(record["id"])
        if record_id not in self._records:
            self._order.append(record_id)
        self._records[record_id] = record

    def remove(self, record_id: str) -> None:
        self._records.pop(record_id, None)
        if record_id in self._order:
            self._order.remove(record_id)

    def _raw_records(self) -> list[dict[str, Any]]:
        return [self._records[i] for i in self._order if i in self._records]

    def documents(self) -> list[Document]:
        """Domain records for this day, in file order.

        Each is detached from the stored mapping on purpose. If it were not,
        writing the file would rewrite every record, adding default keys to
        records the user never touched.
        """
        result: list[Document] = []
        for record_id in self._order:
            record = self._records.get(record_id)
            if record is None:
                continue
            document = self._to_document(record)
            if document is not None:
                result.append(document)
        return result

    def _to_document(self, record: dict[str, Any]) -> Document | None:
        seeded = {**record, "type": str(self.DOCUMENT_TYPE)}
        try:
            document = from_frontmatter(seeded, "")
        except VaultError:
            return None
        # Detach: the document must not alias the stored mapping, and its unknown
        # keys now live in ``extra``, which is what a save writes back.
        document.frontmatter_source = None
        return document

    @staticmethod
    def record_for(document: Document) -> dict[str, Any]:
        """Render a document as a stored record, preserving its unknown keys.

        Owned keys are applied after ``extra`` so a stale key inside the unknown
        set can never shadow an authoritative value.
        """
        return document_to_frontmatter(document)

    # ----------------------------------------------------------------- write

    def frontmatter(self) -> dict[str, Any]:
        return {
            "id": f"day_{self.day.strftime('%Y%m%d')}",
            "type": DAY_TYPE,
            DAY_TITLE_KEY: self.day.strftime("%A, %d %B %Y"),
            self.SEQUENCE_KEY: self._raw_records(),
        }

    def body(self) -> str:
        """The Markdown rendering. Regenerated on every save."""
        raise NotImplementedError

    def _heading(self) -> str:
        return f"# {self.day.strftime('%A, %d %B %Y')}"

    def save(self, documents: Any) -> None:
        """Write the day file atomically."""
        documents.write(self.path, self.frontmatter(), self.body())


class TaskDayFile(RecordDayFile):
    """``todos/[YYYY-MM]/[DD-YYYY].md`` holding a ``tasks`` sequence."""

    AREA = "todos"
    SEQUENCE_KEY = TASKS_KEY
    DOCUMENT_TYPE = DocumentType.TASK

    def tasks(self) -> list[Task]:
        """Domain tasks for this day, in file order.

        Named to shadow the base ``documents()`` with a narrower type, so a
        caller working with tasks never has to filter by type itself.
        """
        return [d for d in self.documents() if isinstance(d, Task)]

    def body(self) -> str:
        """An Obsidian checklist rendering of the same records.

        Regenerated on every save, so it is a view rather than an editable source.
        """
        lines = [self._heading(), ""]
        for record in self._raw_records():
            title = str(record.get("title", "Untitled"))
            done = str(record.get("status", "")) == str(TaskStatus.DONE)
            lines.append(f"- [{'x' if done else ' '}] {title}")
            notes = str(record.get("notes", "")).strip()
            if notes:
                for note_line in notes.splitlines():
                    lines.append(f"      - {note_line}")
        lines.append("")
        return "\n".join(lines)


class TimerDayFile(RecordDayFile):
    """``timer/[YYYY-MM]/[DD-YYYY].md`` holding a ``timers`` sequence."""

    AREA = "timer"
    SEQUENCE_KEY = TIMERS_KEY
    DOCUMENT_TYPE = DocumentType.TIMER

    def timers(self) -> list[Timer]:
        """Domain timers for this day, in file order."""
        return [d for d in self.documents() if isinstance(d, Timer)]

    def body(self) -> str:
        """A Markdown rendering of the day's timers.

        Timers are time-based, so each line carries the time it is due rather than
        a checkbox: there is nothing for the user to tick here, and the state
        lives in the frontmatter.
        """
        lines = [self._heading(), ""]
        for record in self._raw_records():
            title = str(record.get("title", "Untitled"))
            due = record.get("due_at")
            when = str(due)[11:16] if isinstance(due, str) and len(str(due)) >= 16 else "--:--"
            status = str(record.get("status", "scheduled"))
            notes = str(record.get("notes", "")).strip()
            suffix = f"  _{status}_" if status != "scheduled" else ""
            lines.append(f"- **{when}** {title}{suffix}")
            if notes:
                for note_line in notes.splitlines():
                    lines.append(f"      - {note_line}")
        lines.append("")
        return "\n".join(lines)
