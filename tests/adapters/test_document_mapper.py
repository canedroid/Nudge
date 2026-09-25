"""Frontmatter mapping: tolerance, defaults and preservation of unknown keys."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nodify.adapters.document_mapper import (
    document_to_frontmatter,
    from_frontmatter,
    split_known_keys,
)
from nodify.adapters.markdown_repo import MarkdownCodec
from nodify.domain.documents import (
    DocumentType,
    Note,
    Schedule,
    Task,
    TaskStatus,
    Timer,
    TimerKind,
    TimerStatus,
)
from nodify.domain.ports import DocumentFormatError


def test_note_round_trip_preserves_unknown_keys() -> None:
    original = {
        "id": "note_1",
        "type": "note",
        "title": "Ideas",
        "category": "Work",
        "obsidian": {"cssclass": "wide"},
        "custom": "keep",
    }
    note = from_frontmatter(original, "Body\n")
    assert isinstance(note, Note)
    assert note.category == "Work"
    assert note.extra == {"obsidian": {"cssclass": "wide"}, "custom": "keep"}

    rendered = document_to_frontmatter(note)
    assert rendered["custom"] == "keep"
    assert rendered["obsidian"] == {"cssclass": "wide"}


def test_extra_cannot_shadow_owned_keys() -> None:
    note = Note(
        id="note_1",
        title="Real Title",
        category="Work",
        extra={"title": "Stale", "id": "stale_id"},
    )
    rendered = document_to_frontmatter(note)
    assert rendered["title"] == "Real Title"
    assert rendered["id"] == "note_1"


def test_full_pipeline_via_codec() -> None:
    """The path a document actually takes: text -> mapping -> document -> text."""
    codec = MarkdownCodec()
    text = (
        "---\n"
        "id: note_1\n"
        "type: note\n"
        "title: Ideas\n"
        "category: Work\n"
        "tags:\n"
        "  - alpha\n"
        "created_at: 2026-09-25T10:00:00Z\n"
        "zebra: last\n"
        "# keep this comment\n"
        "alpha: first\n"
        "---\n"
        "# Heading\n\nBody.\n"
    )
    frontmatter, body = codec.parse_document(text)
    note = from_frontmatter(frontmatter, body)
    assert isinstance(note, Note)
    assert note.tags == ["alpha"]
    assert note.created_at == datetime(2026, 9, 25, 10, tzinfo=UTC)

    written = codec.serialise(document_to_frontmatter(note), note.body)
    assert "zebra: last" in written
    assert "# keep this comment" in written
    assert "# Heading" in written


def test_empty_tags_are_not_written() -> None:
    """A note with no tags must not gain a ``tags: []`` key on save.

    Writing the empty list would add a key the user never wrote, which shows up as
    a diff on every subsequent save of a note they are not even editing.
    """
    rendered = document_to_frontmatter(Note(id="n1", title="No tags", category="Notes"))
    assert "tags" not in rendered


def test_populated_tags_are_written() -> None:
    rendered = document_to_frontmatter(
        Note(id="n1", title="Tagged", category="Notes", tags=["a", "b"])
    )
    assert rendered["tags"] == ["a", "b"]


class TestRequiredFields:
    def test_missing_id_is_an_error(self) -> None:
        with pytest.raises(DocumentFormatError, match="missing a frontmatter 'id'"):
            from_frontmatter({"type": "note", "title": "x"}, "")

    def test_blank_id_is_an_error(self) -> None:
        with pytest.raises(DocumentFormatError, match="missing a frontmatter 'id'"):
            from_frontmatter({"id": "   ", "type": "note"}, "")

    def test_unknown_type_is_an_error(self) -> None:
        with pytest.raises(DocumentFormatError, match="unknown document type"):
            from_frontmatter({"id": "x", "type": "recipe"}, "")

    def test_missing_type_defaults_to_note(self) -> None:
        document = from_frontmatter({"id": "x", "title": "Loose"}, "")
        assert document.type is DocumentType.NOTE


class TestTolerance:
    """A bad optional field must not make a whole vault unreadable."""

    def test_unknown_enum_value_falls_back(self) -> None:
        task = from_frontmatter(
            {
                "id": "t1",
                "type": "task",
                "title": "x",
                "status": "quantum",
                "schedule": "eons",
            },
            "",
        )
        assert isinstance(task, Task)
        assert task.status is TaskStatus.OPEN
        assert task.schedule is Schedule.NOW

    def test_enum_is_case_insensitive(self) -> None:
        task = from_frontmatter({"id": "t1", "type": "task", "title": "x", "status": "DONE"}, "")
        assert isinstance(task, Task)
        assert task.status is TaskStatus.DONE

    def test_unparseable_timestamp_becomes_none(self) -> None:
        note = from_frontmatter(
            {"id": "n1", "type": "note", "title": "x", "created_at": "not a date"}, ""
        )
        assert note.created_at is None

    def test_naive_timestamp_is_rejected(self) -> None:
        note = from_frontmatter(
            {"id": "n1", "type": "note", "title": "x", "created_at": "2026-09-25T10:00:00"}, ""
        )
        assert note.created_at is None

    def test_timestamp_offsets_normalise_to_utc(self) -> None:
        note = from_frontmatter(
            {"id": "n1", "type": "note", "title": "x", "created_at": "2026-09-25T15:30:00+05:30"},
            "",
        )
        assert note.created_at == datetime(2026, 9, 25, 10, tzinfo=UTC)

    def test_tags_accepts_scalar_or_list(self) -> None:
        scalar = from_frontmatter({"id": "n1", "type": "note", "title": "x", "tags": "solo"}, "")
        assert scalar.tags == ["solo"]
        listed = from_frontmatter(
            {"id": "n1", "type": "note", "title": "x", "tags": ["a", "b"]}, ""
        )
        assert listed.tags == ["a", "b"]

    def test_garbage_tags_becomes_empty(self) -> None:
        note = from_frontmatter({"id": "n1", "type": "note", "title": "x", "tags": 42}, "")
        assert note.tags == []

    def test_numeric_title_is_coerced(self) -> None:
        note = from_frontmatter({"id": "n1", "type": "note", "title": 2026}, "")
        assert note.title == "2026"


class TestTaskMapping:
    def test_full_task_round_trip(self) -> None:
        task = Task(
            id="t1",
            type=DocumentType.TASK,
            title="Write report",
            body="Details\n",
            notes="internal",
            status=TaskStatus.DONE,
            priority="high",
            schedule=Schedule.SOON,
            due_at=datetime(2026, 9, 26, tzinfo=UTC),
            completed_at=datetime(2026, 9, 25, 12, tzinfo=UTC),
            completion_history=[datetime(2026, 9, 24, 12, tzinfo=UTC)],
        )
        rendered = document_to_frontmatter(task)
        restored = from_frontmatter(rendered, task.body)

        assert isinstance(restored, Task)
        assert restored.status is TaskStatus.DONE
        assert restored.priority == "high"
        assert restored.schedule is Schedule.SOON
        assert restored.due_at == task.due_at
        assert restored.completion_history == task.completion_history
        assert restored.is_done

    def test_optional_task_fields_omitted_when_unset(self) -> None:
        rendered = document_to_frontmatter(Task(id="t1", type=DocumentType.TASK, title="x"))
        assert "due_at" not in rendered
        assert "completed_at" not in rendered
        assert "notes" not in rendered

    def test_completion_history_skips_bad_entries(self) -> None:
        task = from_frontmatter(
            {
                "id": "t1",
                "type": "task",
                "title": "x",
                "completion_history": ["2026-09-24T00:00:00Z", "garbage", None],
            },
            "",
        )
        assert isinstance(task, Task)
        assert task.completion_history == [datetime(2026, 9, 24, tzinfo=UTC)]


class TestTimerMapping:
    def test_full_timer_round_trip(self) -> None:
        timer = Timer(
            id="tm1",
            type=DocumentType.TIMER,
            title="Call Sam",
            kind=TimerKind.COUNTDOWN,
            status=TimerStatus.SCHEDULED,
            starts_at=datetime(2026, 9, 25, 9, tzinfo=UTC),
            due_at=datetime(2026, 9, 25, 9, 30, tzinfo=UTC),
            duration_seconds=1800,
            notified_at=datetime(2026, 9, 25, 9, 30, tzinfo=UTC),
            notes="bring notes",
        )
        restored = from_frontmatter(document_to_frontmatter(timer), "")
        assert isinstance(restored, Timer)
        assert restored.kind is TimerKind.COUNTDOWN
        assert restored.duration_seconds == 1800
        assert restored.due_at == timer.due_at

    def test_string_duration_is_coerced(self) -> None:
        timer = from_frontmatter(
            {"id": "t1", "type": "timer", "title": "x", "duration_seconds": "600"}, ""
        )
        assert isinstance(timer, Timer)
        assert timer.duration_seconds == 600

    def test_garbage_duration_becomes_none(self) -> None:
        timer = from_frontmatter(
            {"id": "t1", "type": "timer", "title": "x", "duration_seconds": "soon"}, ""
        )
        assert isinstance(timer, Timer)
        assert timer.duration_seconds is None

    def test_bool_duration_rejected(self) -> None:
        timer = from_frontmatter(
            {"id": "t1", "type": "timer", "title": "x", "duration_seconds": True}, ""
        )
        assert isinstance(timer, Timer)
        assert timer.duration_seconds is None

    def test_unknown_kind_falls_back_to_countdown(self) -> None:
        timer = from_frontmatter({"id": "t1", "type": "timer", "title": "x", "kind": "sundial"}, "")
        assert isinstance(timer, Timer)
        assert timer.kind is TimerKind.COUNTDOWN


def test_split_known_keys_partitions() -> None:
    known, extra = split_known_keys(
        {"id": "x", "type": "note", "title": "t", "zzz": 1, "status": "open"}
    )
    assert set(known) == {"id", "type", "title", "status"}
    assert extra == {"zzz": 1}
