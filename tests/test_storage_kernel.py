"""End-to-end storage kernel.

These tests drive the real pipeline the way the features will: read bytes from a
file on disk, parse, map to a domain document, mutate, write back, re-read. The
property they guard is that Nodify is a safe editor for a vault the user also
manages in Obsidian, so a save must not churn a file it did not meaningfully
change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodify.adapters.document_mapper import document_to_frontmatter, from_frontmatter
from nodify.adapters.vault import Vault
from nodify.domain.documents import DocumentType, Note, Schedule, Task, TaskStatus, Timer
from nodify.domain.ports import DocumentFormatError

HAND_WRITTEN_NOTE = """---
id: 01JABCDEF
type: note
title: Meeting notes
tags:
  - work
  - weekly
created_at: 2026-09-20T09:00:00Z
obsidian:
  cssclass: wide
  links:
    - name: Project
      url: https://example.com
reviewed: 2026-09-21
# keep an eye on this
follow_up: call the vendor
---

# Meeting notes

Discussed the roadmap and the migration plan.
"""


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(tmp_path / "vault")
    instance.open()
    return instance


def load(vault: Vault, path: Path) -> tuple[dict[str, object], str]:
    frontmatter, body = vault.documents.read(path)
    return frontmatter, body


def open_document(vault: Vault, path: Path) -> object:
    """Read and map a document the way a repository will.

    The path is passed through because a note's category comes from its folder.
    """
    frontmatter, body = load(vault, path)
    return from_frontmatter(frontmatter, body, vault.resolver.relative_to_vault(path))


def save(vault: Vault, path: Path, document: object) -> None:
    vault.documents.write(path, document_to_frontmatter(document), document.body)  # type: ignore[attr-defined]


class TestNoteLifecycle:
    def test_hand_written_note_loads(self, vault: Vault) -> None:
        path = vault.resolver.resolve("notes", "Work", "Meeting notes.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HAND_WRITTEN_NOTE, encoding="utf-8")

        frontmatter, body = load(vault, path)
        note = open_document(vault, path)

        assert isinstance(note, Note)
        assert note.id == "01JABCDEF"
        assert note.title == "Meeting notes"
        assert note.tags == ["work", "weekly"]
        assert note.category == "Work"
        assert note.created_at == datetime(2026, 9, 20, 9, tzinfo=UTC)
        assert "Discussed the roadmap" in note.body

    def test_editing_the_body_preserves_foreign_frontmatter(self, vault: Vault) -> None:
        path = vault.resolver.resolve("notes", "Work", "Meeting notes.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HAND_WRITTEN_NOTE, encoding="utf-8")

        frontmatter, body = load(vault, path)
        note = open_document(vault, path)
        note.body = body + "\nAdded a line.\n"
        save(vault, path, note)

        written = path.read_text(encoding="utf-8")
        assert "Added a line." in written
        assert "cssclass: wide" in written
        assert "reviewed: 2026-09-21" in written
        assert "follow_up: call the vendor" in written
        assert "keep an eye on this" in written
        assert "name: Project" in written

    def test_resave_is_byte_identical(self, vault: Vault) -> None:
        """A save that changes nothing must produce the same bytes.

        Otherwise every launch would dirty every file in the user's vault and
        fill their version history with meaningless diffs.
        """
        path = vault.resolver.resolve("notes", "Work", "Meeting notes.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HAND_WRITTEN_NOTE, encoding="utf-8")

        frontmatter, body = load(vault, path)
        save(
            vault, path, from_frontmatter(frontmatter, body, vault.resolver.relative_to_vault(path))
        )
        first = path.read_text(encoding="utf-8")

        frontmatter, body = load(vault, path)
        save(
            vault, path, from_frontmatter(frontmatter, body, vault.resolver.relative_to_vault(path))
        )
        second = path.read_text(encoding="utf-8")

        assert first == second

    def test_repeated_saves_never_drift(self, vault: Vault) -> None:
        path = vault.resolver.resolve("notes", "Work", "Meeting notes.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HAND_WRITTEN_NOTE, encoding="utf-8")

        seen: set[str] = set()
        for _ in range(5):
            frontmatter, body = load(vault, path)
            save(vault, path, from_frontmatter(frontmatter, body))
            seen.add(path.read_text(encoding="utf-8"))

        assert len(seen) == 1

    def test_renaming_updates_the_title_only(self, vault: Vault) -> None:
        path = vault.resolver.resolve("notes", "Work", "Meeting notes.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HAND_WRITTEN_NOTE, encoding="utf-8")

        frontmatter, body = load(vault, path)
        note = open_document(vault, path)
        note.title = "Meeting notes (revised)"
        save(vault, path, note)

        assert "title: Meeting notes (revised)" in path.read_text(encoding="utf-8")
        assert "cssclass: wide" in path.read_text(encoding="utf-8")


class TestTaskLifecycle:
    def test_completing_a_task_persists(self, vault: Vault) -> None:
        path = vault.resolver.resolve("todos", "2026-09", "25-2026.md")

        task = Task(
            id="t1",
            type=DocumentType.TASK,
            title="Send the invoice",
            body="- [ ] Send the invoice\n",
            schedule=Schedule.SOON,
            due_at=datetime(2026, 9, 26, 17, tzinfo=UTC),
        )
        save(vault, path, task)

        task.status = TaskStatus.DONE
        task.completed_at = datetime(2026, 9, 25, 18, tzinfo=UTC)
        task.completion_history = [task.completed_at]
        task.body = "- [x] Send the invoice\n"
        save(vault, path, task)

        frontmatter, body = load(vault, path)
        restored = from_frontmatter(frontmatter, body)
        assert isinstance(restored, Task)
        assert restored.is_done
        assert restored.due_at == task.due_at
        assert restored.completion_history == task.completion_history
        assert body == "- [x] Send the invoice\n"

    def test_a_save_replaces_the_day_file(self, vault: Vault) -> None:
        """A known seam, pinned so it cannot be forgotten.

        ``todos/2026-09/25-2026.md`` is meant to hold *many* tasks, but the
        document model is one document per file, so writing replaces the file
        wholesale. Phase D adds a day-file document that owns a list of tasks and
        merges into the existing file; until then a second write would drop the
        first task.

        This test pins the current behaviour so the change in Phase D is a
        deliberate, visible one.
        """
        path = vault.resolver.resolve("todos", "2026-09", "25-2026.md")
        save(vault, path, Task(id="t1", type=DocumentType.TASK, title="One"))
        save(vault, path, Task(id="t2", type=DocumentType.TASK, title="Two"))

        frontmatter, _ = load(vault, path)
        assert from_frontmatter(frontmatter, "").id == "t2"

    def test_day_file_appears_in_the_index(self, vault: Vault) -> None:
        path = vault.resolver.resolve("todos", "2026-09", "25-2026.md")
        save(vault, path, Task(id="t1", type=DocumentType.TASK, title="One"))

        entry = vault.index.refresh()[0]
        assert entry.area == "todos"
        assert entry.day == datetime(2026, 9, 25, tzinfo=UTC)


class TestTimerLifecycle:
    def test_timer_round_trips_through_disk(self, vault: Vault) -> None:
        path = vault.resolver.resolve("timer", "2026-09", "25-2026.md")
        timer = Timer(
            id="tm1",
            type=DocumentType.TIMER,
            title="Call Sam",
            due_at=datetime(2026, 9, 25, 15, 30, tzinfo=UTC),
            duration_seconds=1800,
        )
        save(vault, path, timer)

        frontmatter, body = load(vault, path)
        restored = from_frontmatter(frontmatter, body)
        assert isinstance(restored, Timer)
        assert restored.due_at == timer.due_at
        assert restored.duration_seconds == 1800

    def test_marking_notified_persists_for_deduplication(self, vault: Vault) -> None:
        path = vault.resolver.resolve("timer", "2026-09", "25-2026.md")
        timer = Timer(
            id="tm1",
            type=DocumentType.TIMER,
            title="Call Sam",
            due_at=datetime(2026, 9, 25, 15, 30, tzinfo=UTC),
        )
        save(vault, path, timer)

        frontmatter, body = load(vault, path)
        loaded = from_frontmatter(frontmatter, body)
        assert isinstance(loaded, Timer)
        loaded.notified_at = datetime(2026, 9, 25, 15, 30, 1, tzinfo=UTC)
        save(vault, path, loaded)

        frontmatter, body = load(vault, path)
        reloaded = from_frontmatter(frontmatter, body)
        assert isinstance(reloaded, Timer)
        assert reloaded.notified_at == loaded.notified_at


class TestInteroperability:
    def test_a_note_with_no_frontmatter_is_still_usable(self, vault: Vault) -> None:
        path = vault.resolver.resolve("notes", "Inbox.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Just a thought\n", encoding="utf-8")

        frontmatter, body = load(vault, path)
        # No frontmatter means no id, which cannot be addressed. The document is
        # surfaced as an error rather than being silently rewritten with a new id.
        with pytest.raises(DocumentFormatError, match="id"):
            from_frontmatter(frontmatter, body)

    def test_unicode_titles_survive_a_write(self, vault: Vault) -> None:
        path = vault.resolver.resolve("notes", "Notes", "日本語.md")
        note = Note(id="n1", title="Ünïcode 日本語", category="Notes")
        save(vault, path, note)

        restored = open_document(vault, path)
        assert isinstance(restored, Note)
        assert restored.title == "Ünïcode 日本語"
        # The category came from the folder, not from the frontmatter key.
        assert restored.category == "Notes"

    def test_markdown_in_the_body_is_never_escaped(self, vault: Vault) -> None:
        path = vault.resolver.resolve("notes", "Notes", "Code.md")
        body = "# Title\n\n```python\nprint('hi')\n```\n\n- a\n- b\n"
        save(vault, path, Note(id="n1", title="Code", category="Notes", body=body))

        _, restored_body = load(vault, path)
        assert restored_body == body
