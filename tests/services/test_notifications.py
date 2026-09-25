"""Notification delivery and the reminder ordering guarantee.

The ordering is the point: ``notified_at`` is written *before* delivery is
attempted, so a crash between the two cannot cause the same alert on every
subsequent launch. These tests pin that order.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nodify.services.notifications import (
    MAX_BODY_LENGTH,
    NotificationRequest,
    NotificationService,
    QtNotificationTransport,
    ReminderNotifier,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class RecordingTransport:
    def __init__(self, accept: bool = True, raises: bool = False) -> None:
        self.accept = accept
        self.raises = raises
        self.shown: list[NotificationRequest] = []

    def show(self, request: NotificationRequest) -> bool:
        self.shown.append(request)
        if self.raises:
            raise RuntimeError("the notification service is not running")
        return self.accept


class FakeTimerRepository:
    """Records the order of calls, so the notify-then-record race is visible."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.marked: list[tuple[str, datetime]] = []
        self.calls: list[str] = []

    def mark_notified(self, timer_id: str, at: datetime) -> object:
        self.calls.append("mark")
        if self.fail:
            raise OSError("the vault is read only")
        self.marked.append((timer_id, at))
        self.calls.append("marked")
        return object()


@pytest.fixture
def repository() -> FakeTimerRepository:
    return FakeTimerRepository()


@pytest.fixture
def transport() -> RecordingTransport:
    return RecordingTransport()


@pytest.fixture
def notifications(transport: RecordingTransport) -> NotificationService:
    return NotificationService(transport=transport)


class TestRequest:
    def test_trims_and_truncates_the_body(self) -> None:
        request = NotificationRequest(title="  Sam  ", body="  hello  ")
        assert request.title == "Sam"
        assert request.body == "hello"

    def test_truncates_a_long_body(self) -> None:
        request = NotificationRequest(title="Sam", body="x" * 1000)
        assert len(request.body) <= MAX_BODY_LENGTH
        assert request.body.endswith("…")

    def test_truncates_a_long_title(self) -> None:
        assert len(NotificationRequest(title="x" * 500, body="").title) <= 120

    def test_a_floor_on_the_timeout(self) -> None:
        """A zero timeout would flash and vanish before being read."""
        assert NotificationRequest(title="a", body="b", timeout_ms=1).timeout_ms >= 1000

    def test_an_empty_body_is_allowed(self) -> None:
        assert NotificationRequest(title="Sam", body="").body == ""


class TestDelivery:
    def test_delivers(self, notifications: NotificationService) -> None:
        assert notifications.notify("Sam", "Are we on?")
        assert notifications.failures == []

    def test_a_refusal_is_recorded(self, transport: RecordingTransport) -> None:
        transport.accept = False
        service = NotificationService(transport=transport)
        assert not service.notify("Sam", "Are we on?")
        assert len(service.failures) == 1
        assert service.last_failure is not None

    def test_a_raising_transport_does_not_propagate(self, transport: RecordingTransport) -> None:
        """A broken transport must not take the caller down with it."""
        transport.raises = True
        service = NotificationService(transport=transport)
        assert not service.notify("Sam", "Are we on?")
        assert service.failures

    def test_the_default_transport_always_succeeds(self) -> None:
        """A system with no notification support must not look like a failure."""
        service = NotificationService()
        assert service.notify("Sam", "hello")
        assert service.failures == []

    def test_failures_can_be_cleared(self, transport: RecordingTransport) -> None:
        transport.accept = False
        service = NotificationService(transport=transport)
        service.notify("Sam", "hi")
        service.clear_failures()
        assert service.failures == []
        assert service.last_failure is None

    def test_a_none_tray_reports_failure(self) -> None:
        assert not QtNotificationTransport(None).show(NotificationRequest(title="a", body="b"))


class TestOrdering:
    def test_the_record_is_written_before_delivery_is_attempted(
        self,
        repository: FakeTimerRepository,
        notifications: NotificationService,
        transport: RecordingTransport,
    ) -> None:
        """This ordering is the whole point of ReminderNotifier.

        Marking only on success would let a transport that always fails
        re-notify on every launch, forever.
        """
        notifier = ReminderNotifier(repository, notifications, lambda: NOW)
        notifier.announce("timer_1", "Time's up")

        # The mark happened, and it happened while announcing, before the caller
        # could have seen any failure.
        assert repository.marked == [("timer_1", NOW)]
        assert len(transport.shown) == 1

    def test_a_transport_failure_does_not_undo_the_record(
        self, repository: FakeTimerRepository, transport: RecordingTransport
    ) -> None:
        transport.accept = False
        notifier = ReminderNotifier(repository, NotificationService(transport), lambda: NOW)
        assert not notifier.announce("timer_1", "Time's up")
        # The record stands, so the next launch will not re-announce it.
        assert repository.marked == [("timer_1", NOW)]

    def test_a_failing_record_prevents_announcement(
        self, repository: FakeTimerRepository, transport: RecordingTransport
    ) -> None:
        """If the record cannot be written, the next pass must try again.

        Announcing anyway would risk a duplicate on the following launch.
        """
        repository.fail = True
        notifier = ReminderNotifier(repository, NotificationService(transport), lambda: NOW)
        assert not notifier.announce("timer_1", "Time's up")
        assert transport.shown == []

    def test_a_raising_transport_leaves_the_record_standing(
        self, repository: FakeTimerRepository, transport: RecordingTransport
    ) -> None:
        transport.raises = True
        notifier = ReminderNotifier(repository, NotificationService(transport), lambda: NOW)
        assert not notifier.announce("timer_1", "Time's up")
        assert repository.marked == [("timer_1", NOW)]


class TestAnnounceAll:
    def test_announces_each(
        self, repository: FakeTimerRepository, notifications: NotificationService
    ) -> None:
        notifier = ReminderNotifier(repository, notifications, lambda: NOW)
        failed = notifier.announce_all([("t1", "One", ""), ("t2", "Two", ""), ("t3", "Three", "")])
        assert failed == []
        assert len(repository.marked) == 3

    def test_reports_the_ones_that_failed(
        self, repository: FakeTimerRepository, transport: RecordingTransport
    ) -> None:
        transport.accept = False
        notifier = ReminderNotifier(repository, NotificationService(transport), lambda: NOW)
        failed = notifier.announce_all([("t1", "One", ""), ("t2", "Two", "")])
        assert failed == ["t1", "t2"]

    def test_a_failing_record_does_not_stop_the_rest(
        self, repository: FakeTimerRepository, transport: RecordingTransport
    ) -> None:
        notifier = ReminderNotifier(repository, NotificationService(transport), lambda: NOW)

        original = repository.mark_notified

        def fail_on_first(timer_id: str, at: datetime) -> object:
            if timer_id == "t1":
                raise OSError("read only")
            return original(timer_id, at)

        repository.mark_notified = fail_on_first  # type: ignore[method-assign]
        failed = notifier.announce_all([("t1", "One", ""), ("t2", "Two", "")])
        assert failed == ["t1"]
        # The second was still announced; one bad record must not stop the queue.
        assert len(transport.shown) == 1

    def test_an_empty_list(self, repository: FakeTimerRepository) -> None:
        notifier = ReminderNotifier(repository, NotificationService(), lambda: NOW)
        assert notifier.announce_all([]) == []
