"""Day files: one file holds many tasks.

The plan's contract is ``todos/[YYYY-MM]/[DD-YYYY].md`` holding a structured
``tasks`` sequence, with an Obsidian checklist body generated from the same
records. That makes the day file a *container* document rather than a single task,
which is the seam Package B pinned with a test.

Three properties are deliberate.

**The YAML sequence is the record; the checklist is a rendering.** If a user
edits the checklist body in Obsidian the change is not read back, because the
body is regenerated on every save. The alternative, parsing the checklist, makes
the file ambiguous and round-trips badly. Obsidian still shows the tasks, which is
what the body is for, and the file stays a valid note for the user.

**Records are stored as plain mappings and are only rewritten when edited.** A
task handed out by :meth:`DayFile.tasks` does not alias the stored mapping, so
saving the file after editing one task leaves the other records byte-identical
instead of normalising them. This is what makes "editing one task does not corrupt
others in the same day file" true rather than merely likely.

**Unknown keys inside a task record survive an edit.** A user or third-party tool
that adds ``estimate: 30m`` to one record does not lose it when Nodify saves that
file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodify.adapters.document_mapper import document_to_frontmatter, from_frontmatter
from nodify.domain.documents import DocumentType, Task, TaskStatus
from nodify.domain.ports import VaultError

TASKS_KEY = "tasks"
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


class DayFile:
    """The tasks for one UTC calendar day, and the file they live in."""

    def __init__(self, day: datetime, root: Path) -> None:
        self.day = normalise_day(day)
        self._root = root
        self._records: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []

    # ------------------------------------------------------------------ path

    @property
    def relative_path(self) -> Path:
        return Path("todos") / day_folder(self.day) / day_file_name(self.day)

    @property
    def path(self) -> Path:
        return self._root / self.relative_path

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, documents: Any, root: Path, day: datetime) -> DayFile:
        """Read the day file, or start an empty one if it does not exist."""
        day_file = cls(day, root)
        if not day_file.exists:
            return day_file

        frontmatter, _ = documents.read(day_file.path)
        raw_tasks = frontmatter.get(TASKS_KEY)
        if not isinstance(raw_tasks, list):
            return day_file

        for entry in raw_tasks:
            if not isinstance(entry, dict):
                # One malformed record must not lose the records around it.
                continue
            task_id = str(entry.get("id", "")).strip()
            if not task_id:
                continue
            day_file._records[task_id] = dict(entry)
            day_file._order.append(task_id)
        return day_file

    # ----------------------------------------------------------------- tasks

    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self) -> Any:
        return iter(self.tasks())

    def has(self, task_id: str) -> bool:
        return task_id in self._records

    def record(self, task_id: str) -> dict[str, Any]:
        """A copy of the stored mapping for a task."""
        return dict(self._records[task_id])

    def record_by_title(self, title: str) -> dict[str, Any] | None:
        """The stored mapping for the task with this exact title.

        A convenience for tests and diagnostics, where addressing a task by its
        generated id would obscure what is being checked.
        """
        for task_id in self._order:
            record = self._records.get(task_id)
            if record is not None and str(record.get("title", "")) == title:
                return dict(record)
        return None

    def add(self, record: dict[str, Any]) -> None:
        """Append a task record. Order is creation order within the day."""
        task_id = str(record["id"])
        if task_id not in self._records:
            self._order.append(task_id)
        self._records[task_id] = record

    def put(self, record: dict[str, Any]) -> None:
        """Replace a record in place, keeping its position in the day.

        Editing a task must not move it to the bottom of the list, so callers
        updating an existing task use this rather than ``remove`` then ``add``.
        """
        task_id = str(record["id"])
        if task_id not in self._records:
            self._order.append(task_id)
        self._records[task_id] = record

    def remove(self, task_id: str) -> None:
        self._records.pop(task_id, None)
        if task_id in self._order:
            self._order.remove(task_id)

    def tasks(self) -> list[Task]:
        """Domain tasks for this day, in file order.

        Each task is detached from the stored mapping on purpose. If it were not,
        writing the file would rewrite every record, adding default keys to tasks
        the user never touched.
        """
        result: list[Task] = []
        for task_id in self._order:
            record = self._records.get(task_id)
            if record is None:
                continue
            task = self._to_task(record)
            if task is not None:
                result.append(task)
        return result

    @staticmethod
    def _to_task(record: dict[str, Any]) -> Task | None:
        """Map one stored record to a detached domain task."""
        seeded = {**record, "type": str(DocumentType.TASK)}
        try:
            task = from_frontmatter(seeded, "")
        except VaultError:
            # A record with no usable id is skipped; the scan above already
            # filters those, so this only guards a defensive edge.
            return None
        if not isinstance(task, Task):
            return None
        # Detach: the task must not alias the stored mapping, and its unknown
        # keys now live in ``extra``, which is what a save writes back.
        task.frontmatter_source = None
        return task

    @staticmethod
    def record_for(task: Task) -> dict[str, Any]:
        """Render a task as a stored record, preserving its unknown keys.

        Owned keys are applied after ``extra`` so a stale key inside the unknown
        set can never shadow an authoritative value.
        """
        return document_to_frontmatter(task)

    # ----------------------------------------------------------------- write

    def frontmatter(self) -> dict[str, Any]:
        """The frontmatter for this day file."""
        return {
            "id": f"day_{self.day.strftime('%Y%m%d')}",
            "type": DAY_TYPE,
            DAY_TITLE_KEY: self.day.strftime("%A, %d %B %Y"),
            TASKS_KEY: [
                self._records[task_id] for task_id in self._order if task_id in self._records
            ],
        }

    def body(self) -> str:
        """An Obsidian checklist rendering of the same records.

        Regenerated on every save, so it is a view rather than an editable source.
        """
        lines = [f"# {self.day.strftime('%A, %d %B %Y')}", ""]
        for task_id in self._order:
            record = self._records.get(task_id)
            if record is None:
                continue
            title = str(record.get("title", "Untitled"))
            done = str(record.get("status", "")) == str(TaskStatus.DONE)
            lines.append(f"- [{'x' if done else ' '}] {title}")
            notes = str(record.get("notes", "")).strip()
            if notes:
                for note_line in notes.splitlines():
                    lines.append(f"      - {note_line}")
        lines.append("")
        return "\n".join(lines)

    def save(self, documents: Any) -> None:
        """Write the day file atomically."""
        documents.write(self.path, self.frontmatter(), self.body())
