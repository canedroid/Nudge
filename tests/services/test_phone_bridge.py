"""Phone bridge: payload validation, store state and the disabled default."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nodify.services.phone_bridge import (
    MAX_FIELD_LENGTH,
    MAX_STORED,
    DisabledPhoneSource,
    NotificationState,
    PhoneBridgeService,
    PhoneNotification,
    PhoneNotificationSnapshot,
    PhoneNotificationSource,
    PhoneNotificationStore,
    PhonePayload,
    PhonePayloadError,
    StaticPhoneSource,
    validate_payload,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def at(**kwargs: float) -> datetime:
    return NOW + timedelta(**kwargs)


def payload(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "sender": "Phone",
        "title": "Sam",
        "body": "Are we still on for tomorrow?",
        "received_at": NOW,
        "source_id": "sms-1",
    }
    fields.update(overrides)
    return fields


@pytest.fixture
def store() -> PhoneNotificationStore:
    return PhoneNotificationStore(_now=lambda: NOW)


class TestValidation:
    def test_accepts_a_complete_payload(self) -> None:
        result = validate_payload(payload(), now=NOW)
        assert result.sender == "Phone"
        assert result.title == "Sam"
        assert result.received_at == NOW

    def test_rejects_a_non_mapping(self) -> None:
        with pytest.raises(PhonePayloadError, match="mapping"):
            validate_payload("just a string", now=NOW)

    def test_rejects_unexpected_fields(self) -> None:
        with pytest.raises(PhonePayloadError, match="unexpected fields"):
            validate_payload({**payload(), "priority": "high"}, now=NOW)

    @pytest.mark.parametrize("field", ["sender", "title"])
    def test_requires_sender_and_title(self, field: str) -> None:
        candidate = payload()
        candidate[field] = "   "
        with pytest.raises(PhonePayloadError, match="must not be empty"):
            validate_payload(candidate, now=NOW)

    def test_rejects_a_missing_sender(self) -> None:
        candidate = payload()
        del candidate["sender"]
        with pytest.raises(PhonePayloadError, match="must be a string"):
            validate_payload(candidate, now=NOW)

    def test_allows_an_empty_body(self) -> None:
        assert validate_payload(payload(body=""), now=NOW).body == ""

    def test_rejects_an_overlong_title(self) -> None:
        with pytest.raises(PhonePayloadError, match="longer than"):
            validate_payload(payload(title="x" * (MAX_FIELD_LENGTH + 1)), now=NOW)

    def test_requires_a_timestamp(self) -> None:
        candidate = payload()
        del candidate["received_at"]
        with pytest.raises(PhonePayloadError, match="received_at is required"):
            validate_payload(candidate, now=NOW)

    def test_rejects_a_naive_timestamp(self) -> None:
        with pytest.raises(PhonePayloadError, match="timezone-aware"):
            validate_payload(payload(received_at=datetime(2026, 9, 25, 12)), now=NOW)

    def test_rejects_a_string_timestamp(self) -> None:
        with pytest.raises(PhonePayloadError, match="must be a datetime"):
            validate_payload(payload(received_at="2026-09-25T12:00:00Z"), now=NOW)

    def test_normalises_an_offset_to_utc(self) -> None:
        from datetime import timedelta as delta
        from datetime import timezone

        result = validate_payload(
            payload(
                received_at=datetime(
                    2026, 9, 25, 17, 30, tzinfo=timezone(delta(hours=5, minutes=30))
                )
            ),
            now=NOW,
        )
        assert result.received_at == NOW

    def test_clamps_a_future_timestamp(self) -> None:
        """A phone with a wrong clock must not hide a notification."""
        result = validate_payload(payload(received_at=at(hours=5)), now=NOW)
        assert result.received_at == NOW

    def test_source_id_may_be_absent(self) -> None:
        candidate = payload()
        del candidate["source_id"]
        assert validate_payload(candidate, now=NOW).source_id is None

    def test_rejects_a_blank_source_id(self) -> None:
        with pytest.raises(PhonePayloadError, match="source_id"):
            validate_payload(payload(source_id="   "), now=NOW)

    def test_rejects_a_non_string_source_id(self) -> None:
        with pytest.raises(PhonePayloadError, match="source_id"):
            validate_payload(payload(source_id=7), now=NOW)

    def test_trims_whitespace(self) -> None:
        result = validate_payload(payload(title="  Sam  "), now=NOW)
        assert result.title == "Sam"

    def test_accepts_an_typed_payload(self) -> None:
        """A typed source still goes through the same checks."""
        built = PhonePayload(sender="Phone", title="Sam", body="hi", received_at=NOW, source_id="x")
        assert validate_payload(built, now=NOW) == built

    def test_a_typed_payload_is_still_clamped(self) -> None:
        built = PhonePayload(sender="Phone", title="Sam", body="hi", received_at=at(hours=5))
        assert validate_payload(built, now=NOW).received_at == NOW

    def test_payloads_are_immutable(self) -> None:
        built = validate_payload(payload(), now=NOW)
        with pytest.raises(AttributeError):
            built.title = "changed"  # type: ignore[misc]

    def test_with_timestamp_returns_a_copy(self) -> None:
        built = validate_payload(payload(), now=NOW)
        moved = built.with_timestamp(at(hours=-1))
        assert moved.received_at == at(hours=-1)
        assert built.received_at == NOW


class TestStoreAdd:
    def test_stores_a_valid_payload(self, store: PhoneNotificationStore) -> None:
        stored = store.add(payload())
        assert isinstance(stored, PhoneNotification)
        assert stored.state is NotificationState.UNREAD
        assert stored.identifier.startswith("phone_")

    def test_identifiers_are_unique(self, store: PhoneNotificationStore) -> None:
        ids = {store.add(payload()).identifier for _ in range(10)}
        assert len(ids) == 10

    def test_a_rejected_payload_never_enters_the_store(self, store: PhoneNotificationStore) -> None:
        with pytest.raises(PhonePayloadError):
            store.add(payload(title=""))
        assert store.all() == []

    def test_the_store_is_bounded(self, store: PhoneNotificationStore) -> None:
        for index in range(MAX_STORED + 20):
            store.add(payload(title=f"Message {index}"))
        assert len(store.all()) == MAX_STORED

    def test_the_oldest_is_dropped_first(self, store: PhoneNotificationStore) -> None:
        for index in range(MAX_STORED + 5):
            store.add(payload(title=f"Message {index}"))
        titles = [n.payload.title for n in store.all()]
        assert titles[0] == "Message 5"
        assert "Message 0" not in titles

    def test_clear(self, store: PhoneNotificationStore) -> None:
        store.add(payload())
        store.clear()
        assert store.all() == []


class TestStoreState:
    def test_starts_unread(self, store: PhoneNotificationStore) -> None:
        stored = store.add(payload())
        assert store.unread_count() == 1
        assert store.by_state(NotificationState.UNREAD) == [stored]

    def test_acknowledge(self, store: PhoneNotificationStore) -> None:
        stored = store.add(payload())
        store.acknowledge(stored.identifier)
        assert stored.state is NotificationState.ACKNOWLEDGED
        assert store.unread_count() == 0

    def test_mark_read(self, store: PhoneNotificationStore) -> None:
        stored = store.add(payload())
        store.mark_read(stored.identifier)
        assert store.read == [stored]

    def test_dismiss(self, store: PhoneNotificationStore) -> None:
        stored = store.add(payload())
        store.dismiss(stored.identifier)
        assert store.dismissed == [stored]
        assert store.unread_count() == 0

    def test_dismissed_is_terminal(self, store: PhoneNotificationStore) -> None:
        """A dismissed notification must not reappear when marked read."""
        stored = store.add(payload())
        store.dismiss(stored.identifier)
        store.mark_read(stored.identifier)
        assert stored.state is NotificationState.DISMISSED

    def test_transitions_on_an_unknown_id(self, store: PhoneNotificationStore) -> None:
        assert store.acknowledge("phone_nope") is None
        assert store.mark_read("phone_nope") is None
        assert store.dismiss("phone_nope") is None

    def test_latest_prefers_unread(self, store: PhoneNotificationStore) -> None:
        first = store.add(payload(title="First"))
        second = store.add(payload(title="Second"))
        store.dismiss(second.identifier)
        assert store.latest() is first

    def test_latest_is_none_when_all_are_handled(self, store: PhoneNotificationStore) -> None:
        stored = store.add(payload())
        store.dismiss(stored.identifier)
        assert store.latest() is None

    def test_ordering_is_stable(self, store: PhoneNotificationStore) -> None:
        for index in range(4):
            store.add(payload(title=f"Message {index}"))
        assert [n.payload.title for n in store.all()] == [
            "Message 0",
            "Message 1",
            "Message 2",
            "Message 3",
        ]


class TestSnapshot:
    def test_empty_snapshot(self, store: PhoneNotificationStore) -> None:
        snapshot = store.snapshot()
        assert isinstance(snapshot, PhoneNotificationSnapshot)
        assert snapshot.is_empty
        assert snapshot.total == 0

    def test_partitions_by_state(self, store: PhoneNotificationStore) -> None:
        unread = store.add(payload(title="Unread"))
        acked = store.add(payload(title="Acked"))
        done = store.add(payload(title="Done"))
        gone = store.add(payload(title="Gone"))

        store.acknowledge(acked.identifier)
        store.mark_read(done.identifier)
        store.dismiss(gone.identifier)

        snapshot = store.snapshot()
        assert snapshot.unread == (unread,)
        assert snapshot.acknowledged == (acked,)
        assert snapshot.read == (done,)
        assert snapshot.dismissed == (gone,)
        assert snapshot.total == 4
        assert not snapshot.is_empty

    def test_snapshot_is_immutable(self, store: PhoneNotificationStore) -> None:
        store.add(payload())
        snapshot = store.snapshot()
        with pytest.raises(AttributeError):
            snapshot.unread = ()  # type: ignore[misc]

    def test_snapshot_is_a_copy_not_a_view(self, store: PhoneNotificationStore) -> None:
        """A snapshot must not change under the caller's feet."""
        store.add(payload())
        snapshot = store.snapshot()
        store.add(payload())
        assert snapshot.total == 1
        assert store.snapshot().total == 2

    def test_snapshot_is_deterministic(self, store: PhoneNotificationStore) -> None:
        for index in range(3):
            store.add(payload(title=f"Message {index}"))
        first = store.snapshot()
        second = store.snapshot()
        assert [n.identifier for n in first.unread] == [n.identifier for n in second.unread]


class TestDisabledSource:
    def test_is_disabled(self) -> None:
        assert DisabledPhoneSource().is_enabled() is False

    def test_polls_nothing(self) -> None:
        assert list(DisabledPhoneSource().poll()) == []

    def test_start_and_stop_do_nothing(self) -> None:
        source = DisabledPhoneSource()
        source.start()
        assert source.is_enabled() is False
        source.stop()
        assert list(source.poll()) == []

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(DisabledPhoneSource(), PhoneNotificationSource)


class TestStaticSource:
    def test_replays_in_order(self) -> None:
        payloads = [
            PhonePayload(sender="P", title="One", body="", received_at=NOW),
            PhonePayload(sender="P", title="Two", body="", received_at=NOW),
        ]
        source = StaticPhoneSource(payloads)
        assert [p.title for p in source.poll()] == ["One", "Two"]

    def test_drains_once(self) -> None:
        source = StaticPhoneSource(
            [PhonePayload(sender="P", title="One", body="", received_at=NOW)]
        )
        assert len(list(source.poll())) == 1
        assert list(source.poll()) == []

    def test_push(self) -> None:
        source = StaticPhoneSource()
        source.push(PhonePayload(sender="P", title="Later", body="", received_at=NOW))
        assert [p.title for p in source.poll()] == ["Later"]

    def test_enabled_toggle(self) -> None:
        source = StaticPhoneSource()
        assert source.is_enabled()
        source.stop()
        assert not source.is_enabled()
        source.start()
        assert source.is_enabled()


class TestBridgeService:
    def test_defaults_to_the_disabled_source(self) -> None:
        service = PhoneBridgeService()
        assert isinstance(service.source, DisabledPhoneSource)
        assert service.is_enabled() is False

    def test_pump_with_the_default_source_accepts_nothing(self) -> None:
        service = PhoneBridgeService(now=lambda: NOW)
        assert service.pump() == []
        assert service.store.all() == []

    def test_pump_moves_payloads_into_the_store(self) -> None:
        source = StaticPhoneSource(
            [PhonePayload(sender="P", title="Sam", body="hi", received_at=NOW)]
        )
        service = PhoneBridgeService(source=source, now=lambda: NOW)
        accepted = service.pump()
        assert len(accepted) == 1
        assert service.store.unread_count() == 1

    def test_a_bad_payload_does_not_stop_the_ones_behind_it(self) -> None:
        class MixedSource:
            def poll(self):  # type: ignore[no-untyped-def]
                yield PhonePayload(sender="", title="Broken", body="", received_at=NOW)
                yield PhonePayload(sender="P", title="Good", body="", received_at=NOW)

            def is_enabled(self) -> bool:
                return True

        service = PhoneBridgeService(source=MixedSource(), now=lambda: NOW)
        accepted = service.pump()
        assert len(accepted) == 1
        assert service.rejected
        assert service.store.unread_count() == 1

    def test_the_source_can_be_replaced_without_touching_the_store(self) -> None:
        """This is what lets a real transport arrive later."""
        store = PhoneNotificationStore(_now=lambda: NOW)
        service = PhoneBridgeService(store=store)
        service.store.add(payload(title="Already here"))

        service.set_source(
            StaticPhoneSource([PhonePayload(sender="P", title="New", body="", received_at=NOW)])
        )
        service.pump()

        titles = [n.payload.title for n in service.store.all()]
        assert titles == ["Already here", "New"]

    def test_snapshot_delegates_to_the_store(self) -> None:
        service = PhoneBridgeService(now=lambda: NOW)
        service.store.add(payload())
        assert service.snapshot().total == 1


class TestIsolationFromOtherDomains:
    def test_the_bridge_module_imports_no_transport(self) -> None:
        """The first release must not require a remote service.

        Checked by reading the module rather than trusting a comment: no
        networking module may be imported by the bridge itself.
        """
        from pathlib import Path as _Path

        import nodify.services.phone_bridge as module

        source = _Path(module.__file__ or "")
        text = source.read_text(encoding="utf-8")
        for forbidden in (
            "import socket",
            "import http",
            "import ssl",
            "import websockets",
            "import requests",
            "urllib",
        ):
            assert forbidden not in text

    def test_the_bridge_does_not_touch_the_vault(self) -> None:
        """A future phone source must not be able to reach into a domain."""
        from pathlib import Path as _Path

        import nodify.services.phone_bridge as module

        text = _Path(module.__file__ or "").read_text(encoding="utf-8")
        for forbidden in ("nodify.adapters", "nodify.ui", "vault"):
            assert forbidden not in text
