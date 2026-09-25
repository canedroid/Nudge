"""Native notification delivery.

One rule governs this module, and it is the plan's acceptance criterion for
notification failure: **a failed notification must not lose the reminder record.**

The ordering is therefore the opposite of the obvious one. ``notified_at`` is
written *before* delivery is attempted, not after it succeeds, because the failure
modes are not symmetric. A notification that fires as the process exits is lost
silently, and one that fires just before a crash is re-announced on every
subsequent launch. Both are far worse for the user than missing a single alert, so
the record is marked first and delivery is best-effort afterwards.

The alternative, marking only on success, is not offered at all: it would let a
system that always fails re-notify on every launch, forever.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

#: Longest a notification's text may be. A toast cannot show more than this, and
#: a longer string is truncated by the platform anyway, inconsistently.
MAX_BODY_LENGTH = 250


class DeliveryOutcome(StrEnum):
    SENT = "sent"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    """A notification to deliver."""

    title: str
    body: str
    timeout_ms: int = 5000

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", str(self.title).strip()[:120])
        body = str(self.body).strip()
        if len(body) > MAX_BODY_LENGTH:
            body = body[: MAX_BODY_LENGTH - 1].rstrip() + "…"
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "timeout_ms", max(1000, int(self.timeout_ms)))


class NotificationTransport(Protocol):
    """What a platform must provide to show a notification."""

    def show(self, request: NotificationRequest) -> bool: ...


class NullNotificationTransport:
    """A transport that shows nothing and always succeeds.

    The default, so the application runs on a system with no notification support
    rather than treating the absence as a failure the user has to read about.
    """

    def show(self, request: NotificationRequest) -> bool:
        return True


@dataclass
class NotificationService:
    """Delivers notifications and reports what happened."""

    transport: NotificationTransport = field(default_factory=NullNotificationTransport)
    #: Every request that was refused or failed, for the UI to surface.
    failures: list[NotificationRequest] = field(default_factory=list)

    def notify(self, title: str, body: str, *, timeout_ms: int = 5000) -> bool:
        """Attempt delivery. Returns whether it was accepted."""
        request = NotificationRequest(title=title, body=body, timeout_ms=timeout_ms)
        try:
            accepted = bool(self.transport.show(request))
        except Exception:  # noqa: BLE001 - a broken transport must not propagate
            # The reminder record has already been written by the caller, so a
            # transport that raises costs the user the alert and nothing else.
            accepted = False

        if not accepted:
            self.failures.append(request)
        return accepted

    @property
    def last_failure(self) -> NotificationRequest | None:
        return self.failures[-1] if self.failures else None

    def clear_failures(self) -> None:
        self.failures.clear()


class TrayIconLike(Protocol):
    """The part of a tray icon the transport needs."""

    def showMessage(  # noqa: N802 - Qt's own spelling
        self,
        title: str,
        body: str,
        icon: object,
        msecs: int,
    ) -> None: ...


class QtNotificationTransport:
    """Shows notifications through the tray icon.

    A tray icon is required: a message shown with no icon is silently discarded by
    Windows, so the transport reports failure rather than pretending to have
    delivered it.
    """

    def __init__(self, tray_icon: TrayIconLike | None) -> None:
        self._tray = tray_icon

    def show(self, request: NotificationRequest) -> bool:
        from PyQt6.QtWidgets import QSystemTrayIcon

        if self._tray is None:
            return False
        # PyQt6's showMessage takes plain strings rather than a message object,
        # and the icon argument is mandatory: Windows silently discards a message
        # with no icon, so omitting it would mean silently losing the reminder.
        self._tray.showMessage(
            request.title,
            request.body,
            QSystemTrayIcon.MessageIcon.Information,
            request.timeout_ms,
        )
        return True


@dataclass(frozen=True, slots=True)
class ReminderAnnouncement:
    """A due reminder, ready to be announced."""

    timer_id: str
    title: str
    body: str = ""


class NotifiableRepository(Protocol):
    """The part of the timer repository the notifier needs."""

    def mark_notified(self, timer_id: str, at: datetime) -> object: ...


@dataclass
class ReminderNotifier:
    """Records the notification intent, then delivers it.

    This ordering is the whole point of the class. ``announce`` marks the timer as
    notified *before* asking the transport to do anything, so a crash between the
    two cannot cause the same alert on every subsequent launch.
    """

    repository: NotifiableRepository
    notifications: NotificationService
    clock: Callable[[], datetime]

    def announce(self, timer_id: str, title: str, body: str = "") -> bool:
        """Record, then deliver. Returns whether delivery was accepted."""
        try:
            self.repository.mark_notified(timer_id, self.clock())
        except Exception:  # noqa: BLE001 - see module docstring
            # The record could not be written, so the timer is not marked and the
            # next pass will try again. Announcing anyway risks a duplicate.
            return False

        return self.notifications.notify(title, body)

    def announce_all(self, due: list[tuple[str, str, str]]) -> list[str]:
        """Announce several reminders, returning the ones that failed delivery."""
        failed: list[str] = []
        for timer_id, title, body in due:
            if not self.announce(timer_id, title, body):
                failed.append(timer_id)
        return failed


__all__ = [
    "MAX_BODY_LENGTH",
    "DeliveryOutcome",
    "NotificationRequest",
    "NotificationService",
    "NotificationTransport",
    "NullNotificationTransport",
    "QtNotificationTransport",
    "ReminderAnnouncement",
    "ReminderNotifier",
]
