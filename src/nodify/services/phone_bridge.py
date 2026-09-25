"""Phone notification bridge: payloads, validation, and an in-memory store.

This is a placeholder for a future phone integration, and the point of it is that
the *default* source does nothing. No network, no WebSocket, no native call. The
first release must run perfectly well with the bridge disabled, and adding a real
source later must not touch notes, tasks or timers.

The store is in memory on purpose. A notification that arrives while the app is
closed is not a thing the user is waiting on, and persisting them would mean a
migration to undo later. The boundary is what matters here, not the durability.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nodify.domain.ports import PhoneNotificationSource


class NotificationState(StrEnum):
    """Lifecycle of one inbound notification."""

    UNREAD = "unread"
    ACKNOWLEDGED = "acknowledged"
    READ = "read"
    DISMISSED = "dismissed"


class PhonePayloadError(ValueError):
    """A payload failed validation and must not enter the store."""


@dataclass(frozen=True, slots=True)
class PhonePayload:
    """A validated inbound notification.

    Frozen because a notification is a fact about something that happened; if a
    caller wants a different one it constructs a new payload rather than editing
    history.
    """

    sender: str
    title: str
    body: str
    received_at: datetime
    source_id: str | None = None

    def with_timestamp(self, moment: datetime) -> PhonePayload:
        return replace(self, received_at=moment)


#: Longest accepted field. A payload larger than this is a malfunction or an
#: attack, and either way the user cannot read it on a tile.
MAX_FIELD_LENGTH = 280

#: Cap on stored notifications, so a misbehaving source cannot exhaust memory.
MAX_STORED = 500


def validate_payload(candidate: object, *, now: datetime) -> PhonePayload:
    """Validate a raw payload and return an immutable :class:`PhonePayload`.

    Accepts either a mapping or an already-built :class:`PhonePayload`. A payload
    that arrived from a typed source still goes through the same checks, because
    "typed" only means the sender used our class, not that the data is sound.

    Every field is checked before the payload can reach the store, because a
    half-valid notification is worse than a rejected one: it would occupy a slot
    in the tile and show the user a blank card they cannot dismiss usefully.

    Timestamps are required to be timezone-aware. A naive timestamp would be read
    in the machine's local zone, so the same payload would sort differently on two
    machines, and a future timestamp is clamped to now because a phone with a
    wrong clock must not be able to hide a notification at the bottom of a list.
    """
    if isinstance(candidate, PhonePayload):
        fields: dict[str, Any] = {
            "sender": candidate.sender,
            "title": candidate.title,
            "body": candidate.body,
            "received_at": candidate.received_at,
            "source_id": candidate.source_id,
        }
    elif isinstance(candidate, dict):
        unknown = set(candidate) - {
            "sender",
            "title",
            "body",
            "received_at",
            "source_id",
        }
        if unknown:
            raise PhonePayloadError(f"unexpected fields: {sorted(unknown)}")
        fields = candidate
    else:
        raise PhonePayloadError("payload must be a mapping or a PhonePayload")

    sender = _require_text(fields, "sender")
    title = _require_text(fields, "title")
    body = _require_text(fields, "body", allow_empty=True)

    raw_time = fields.get("received_at")
    if raw_time is None:
        raise PhonePayloadError("received_at is required")
    if not isinstance(raw_time, datetime):
        raise PhonePayloadError("received_at must be a datetime")
    if raw_time.tzinfo is None:
        raise PhonePayloadError("received_at must be timezone-aware")
    received_at = raw_time.astimezone(UTC)
    if received_at > now:
        received_at = now

    source_id = fields.get("source_id")
    if source_id is not None:
        if not isinstance(source_id, str) or not source_id.strip():
            raise PhonePayloadError("source_id must be a non-empty string or null")
        source_id = source_id.strip()[:MAX_FIELD_LENGTH]

    return PhonePayload(
        sender=sender,
        title=title,
        body=body,
        received_at=received_at,
        source_id=source_id,
    )


def _require_text(candidate: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = candidate.get(key)
    if not isinstance(value, str):
        raise PhonePayloadError(f"{key} must be a string")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise PhonePayloadError(f"{key} must not be empty")
    if len(cleaned) > MAX_FIELD_LENGTH:
        raise PhonePayloadError(f"{key} is longer than {MAX_FIELD_LENGTH} characters")
    return cleaned


@dataclass(slots=True)
class PhoneNotification:
    """A stored notification and its state.

    The payload is frozen, because it is a record of something that happened. The
    state is not, because the store advances it as the user deals with the item.
    """

    payload: PhonePayload
    state: NotificationState = NotificationState.UNREAD
    identifier: str = ""


class DisabledPhoneSource:
    """The default source: no transport, no polling, no calls.

    Every method is a no-op that yields nothing. This is what ships, and it is
    the reason the plan's "no remote service is required for the first release"
    is true by construction rather than by configuration.
    """

    def poll(self) -> Iterator[PhonePayload]:
        return iter(())

    def is_enabled(self) -> bool:
        return False

    def start(self) -> None:
        """Present for interface symmetry; deliberately does nothing."""

    def stop(self) -> None:
        """Present for interface symmetry; deliberately does nothing."""


class StaticPhoneSource:
    """A source that replays a fixed list of payloads.

    For tests, and for wiring a real transport later without changing the store.
    """

    def __init__(self, payloads: Iterable[PhonePayload] | None = None) -> None:
        self._pending: list[PhonePayload] = list(payloads or [])
        self._enabled = True

    def push(self, payload: PhonePayload) -> None:
        self._pending.append(payload)

    def poll(self) -> Iterator[PhonePayload]:
        while self._pending:
            yield self._pending.pop(0)

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def start(self) -> None:
        self._enabled = True

    def stop(self) -> None:
        self._enabled = False


@dataclass
class PhoneNotificationStore:
    """In-memory store of inbound notifications.

    Bounded at :data:`MAX_STORED`. When full, the *oldest* is dropped, because a
    phone that has been offline for a week should still see what just arrived.
    """

    _items: list[PhoneNotification] = field(default_factory=list)
    _counter: int = 0
    _now: Any = None

    def now(self) -> datetime:
        if self._now is None:
            return datetime.now(UTC)
        return self._now()

    def add(self, candidate: object) -> PhoneNotification:
        """Validate and store a payload, returning the stored notification.

        A rejected payload never reaches the store, so a malformed message cannot
        leave a blank card in the tile.
        """
        payload = validate_payload(candidate, now=self.now())
        self._counter += 1
        notification = PhoneNotification(
            payload=payload,
            state=NotificationState.UNREAD,
            identifier=f"phone_{self._counter:06d}",
        )
        self._items.append(notification)
        self._trim()
        return notification

    def _trim(self) -> None:
        overflow = len(self._items) - MAX_STORED
        if overflow > 0:
            del self._items[:overflow]

    def _find(self, identifier: str) -> PhoneNotification | None:
        return next((n for n in self._items if n.identifier == identifier), None)

    def _transition(self, identifier: str, target: NotificationState) -> PhoneNotification | None:
        """Move a notification to a new state.

        Dismissed is terminal: a dismissed notification stays dismissed even if a
        caller later tries to mark it read, because the user has already dealt
        with it and it must not reappear.
        """
        found = self._find(identifier)
        if found is None:
            return None
        if found.state is NotificationState.DISMISSED:
            return found
        found.state = target
        return found

    def acknowledge(self, identifier: str) -> PhoneNotification | None:
        return self._transition(identifier, NotificationState.ACKNOWLEDGED)

    def mark_read(self, identifier: str) -> PhoneNotification | None:
        return self._transition(identifier, NotificationState.READ)

    def dismiss(self, identifier: str) -> PhoneNotification | None:
        return self._transition(identifier, NotificationState.DISMISSED)

    def clear(self) -> None:
        self._items.clear()

    # ------------------------------------------------------------- snapshots

    def all(self) -> list[PhoneNotification]:
        """Every notification, oldest first."""
        return list(self._items)

    def by_state(self, state: NotificationState) -> list[PhoneNotification]:
        return [n for n in self._items if n.state is state]

    @property
    def unread(self) -> list[PhoneNotification]:
        return self.by_state(NotificationState.UNREAD)

    @property
    def acknowledged(self) -> list[PhoneNotification]:
        return self.by_state(NotificationState.ACKNOWLEDGED)

    @property
    def read(self) -> list[PhoneNotification]:
        return self.by_state(NotificationState.READ)

    @property
    def dismissed(self) -> list[PhoneNotification]:
        return self.by_state(NotificationState.DISMISSED)

    def unread_count(self) -> int:
        return len(self.unread)

    def latest(self) -> PhoneNotification | None:
        """The newest notification that still wants the user's attention."""
        for notification in reversed(self._items):
            if notification.state in (NotificationState.UNREAD, NotificationState.ACKNOWLEDGED):
                return notification
        return None

    def snapshot(self) -> PhoneNotificationSnapshot:
        """A deterministic view for a panel to render."""
        return PhoneNotificationSnapshot(
            unread=tuple(self.unread),
            acknowledged=tuple(self.acknowledged),
            read=tuple(self.read),
            dismissed=tuple(self.dismissed),
        )


@dataclass(frozen=True, slots=True)
class PhoneNotificationSnapshot:
    """An immutable view of the store, safe to hand to a panel.

    The snapshot is a *partition*, not a deep copy: it fixes which notifications
    belong in which state at the moment it was taken, so a panel rendering from
    it stays internally consistent. A later state change does not reorder it, and
    a new :meth:`PhoneNotificationStore.snapshot` reflects the new state. The
    contained notifications are shared, so a panel must treat them as read-only,
    which the frozen payload already enforces for everything that matters.
    """

    unread: tuple[PhoneNotification, ...] = ()
    acknowledged: tuple[PhoneNotification, ...] = ()
    read: tuple[PhoneNotification, ...] = ()
    dismissed: tuple[PhoneNotification, ...] = ()

    @property
    def total(self) -> int:
        return len(self.unread) + len(self.acknowledged) + len(self.read) + len(self.dismissed)

    @property
    def is_empty(self) -> bool:
        return self.total == 0


class PhoneBridgeService:
    """Ties a source to a store, so a future transport only replaces the source.

    The store and the panel know nothing about where a payload came from, which is
    what lets a WebSocket, a local socket or a network call be dropped in later
    without touching them.
    """

    def __init__(
        self,
        store: PhoneNotificationStore | None = None,
        source: PhoneNotificationSource | None = None,
        *,
        now: Any = None,
    ) -> None:
        self._store = store or PhoneNotificationStore(_now=now)
        self._source: PhoneNotificationSource = source or DisabledPhoneSource()
        #: Rejected payloads, kept for diagnostics rather than discarded silently.
        self.rejected: list[str] = []

    @property
    def store(self) -> PhoneNotificationStore:
        return self._store

    @property
    def source(self) -> PhoneNotificationSource:
        return self._source

    def set_source(self, source: PhoneNotificationSource) -> None:
        """Replace the transport without touching the store."""
        self._source = source

    def is_enabled(self) -> bool:
        return self._source.is_enabled()

    def pump(self) -> list[PhoneNotification]:
        """Drain the source into the store.

        Returns what was accepted. A payload that fails validation is recorded in
        :attr:`rejected` and skipped, because one bad message must not stop the
        ones behind it.
        """
        accepted: list[PhoneNotification] = []
        for payload in self._source.poll():
            try:
                accepted.append(self._store.add(payload))
            except PhonePayloadError as exc:
                self.rejected.append(str(exc))
        return accepted

    def snapshot(self) -> PhoneNotificationSnapshot:
        return self._store.snapshot()
