"""Hotkey registration: idempotency, rollback and failure reporting."""

from __future__ import annotations

import pytest

from nodify.services.accelerator import Accelerator
from nodify.services.hotkey import HotkeyService, RecordingRegistrar


@pytest.fixture
def registrar() -> RecordingRegistrar:
    return RecordingRegistrar()


@pytest.fixture
def service(registrar: RecordingRegistrar) -> HotkeyService:
    return HotkeyService(registrar.register, registrar.unregister)


class TestRegistration:
    def test_registers_a_valid_accelerator(self, service: HotkeyService) -> None:
        assert service.register("Ctrl+Shift+K")
        assert service.current == "Ctrl+Shift+K"
        assert service.is_registered

    def test_canonicalises_before_registering(self, service: HotkeyService) -> None:
        service.register("ctrl+shift+k")
        assert service.current == "Ctrl+Shift+K"

    def test_a_bare_key_is_refused(self, service: HotkeyService) -> None:
        """A bare key would be swallowed by the whole desktop while registered."""
        assert not service.register("K")
        assert not service.is_registered
        assert service.last_error

    def test_an_empty_accelerator_is_refused(self, service: HotkeyService) -> None:
        assert not service.register("")
        assert service.last_error

    def test_the_error_is_suitable_for_showing_in_app(self, service: HotkeyService) -> None:
        """The plan requires shortcut failure to produce a message the user sees."""
        service.register("K")
        message = service.last_error
        assert isinstance(message, str)
        assert "modifier" in message


class TestIdempotency:
    def test_registering_the_same_accelerator_twice_is_a_no_op(
        self, service: HotkeyService, registrar: RecordingRegistrar
    ) -> None:
        """A repeated startup must not leave two claims on one combination."""
        service.register("Ctrl+K")
        service.register("Ctrl+K")
        assert registrar.registered == ["Ctrl+K"]

    def test_an_equivalent_spelling_is_the_same_accelerator(
        self, service: HotkeyService, registrar: RecordingRegistrar
    ) -> None:
        service.register("ctrl+k")
        service.register("CTRL+K")
        assert registrar.registered == ["Ctrl+K"]

    def test_a_successful_rebind_registers_once_more(
        self, service: HotkeyService, registrar: RecordingRegistrar
    ) -> None:
        service.register("Ctrl+K")
        service.register("Ctrl+J")
        # The old one was released first, so only the new one is held.
        assert registrar.registered == ["Ctrl+J"]
        assert registrar.history == ["Ctrl+K", "Ctrl+J"]


class TestRollback:
    def test_a_failed_rebind_keeps_the_previous_hotkey(
        self, service: HotkeyService, registrar: RecordingRegistrar
    ) -> None:
        """The overlay must never end up unreachable.

        If a rebind fails and the old hotkey were dropped, the user would have no
        way to summon the window.
        """
        service.register("Ctrl+K")
        registrar.refuse.add("Ctrl+J")

        assert not service.register("Ctrl+J")
        assert service.current == "Ctrl+K"
        assert service.is_registered

    def test_the_restored_hotkey_really_is_registered(
        self, service: HotkeyService, registrar: RecordingRegistrar
    ) -> None:
        service.register("Ctrl+K")
        registrar.refuse.add("Ctrl+J")
        service.register("Ctrl+J")
        assert registrar.registered[-1] == "Ctrl+K"

    def test_a_failed_rebind_reports_the_new_combination(
        self, service: HotkeyService, registrar: RecordingRegistrar
    ) -> None:
        service.register("Ctrl+K")
        registrar.refuse.add("Ctrl+J")
        service.register("Ctrl+J")
        assert "Ctrl+J" in (service.last_error or "")

    def test_the_error_is_cleared_on_a_later_success(self, service: HotkeyService) -> None:
        service.register("K")
        assert service.last_error is not None
        service.register("Ctrl+K")
        assert service.last_error is None

    def test_an_unavailable_backend_is_reported_honestly(
        self, registrar: RecordingRegistrar
    ) -> None:
        """If the old hotkey cannot be restored either, say so.

        Claiming a hotkey that is not registered would leave the user pressing a
        key that does nothing and blaming the application.
        """
        service = HotkeyService(registrar.register, registrar.unregister)
        service.register("Ctrl+K")
        registrar.available = False

        assert not service.register("Ctrl+J")
        assert service.current is None
        assert "restored" in (service.last_error or "")

    def test_a_first_registration_failure_leaves_nothing(
        self, service: HotkeyService, registrar: RecordingRegistrar
    ) -> None:
        registrar.refuse.add("Ctrl+K")
        assert not service.register("Ctrl+K")
        assert service.current is None


class TestUnregister:
    def test_releases_the_accelerator(self, service: HotkeyService) -> None:
        service.register("Ctrl+K")
        service.unregister()
        assert not service.is_registered
        assert service.current is None

    def test_unregistering_twice_is_safe(self, service: HotkeyService) -> None:
        service.register("Ctrl+K")
        service.unregister()
        service.unregister()
        assert not service.is_registered

    def test_unregistering_when_nothing_is_registered_is_safe(self, service: HotkeyService) -> None:
        service.unregister()
        assert not service.is_registered


class TestDisplay:
    def test_shows_the_current_hotkey(self, service: HotkeyService) -> None:
        service.register("ctrl+shift+k")
        assert service.display() == "Ctrl + Shift + K"

    def test_shows_a_placeholder_when_unregistered(self, service: HotkeyService) -> None:
        assert service.display() == "—"

    def test_trigger_invokes_the_callback(self) -> None:
        pressed: list[int] = []
        registrar = RecordingRegistrar()
        service = HotkeyService(
            registrar.register, registrar.unregister, on_press=lambda: pressed.append(1)
        )
        service.trigger()
        assert pressed == [1]

    def test_trigger_without_a_callback_is_safe(self, service: HotkeyService) -> None:
        service.trigger()


class TestRecordingRegistrar:
    def test_refuses_a_configured_combination(self) -> None:
        registrar = RecordingRegistrar()
        registrar.refuse.add("Ctrl+K")
        assert not registrar.register(Accelerator.parse("Ctrl+K"))

    def test_records_canonical_forms(self) -> None:
        registrar = RecordingRegistrar()
        registrar.register(Accelerator.parse("ctrl+k"))
        assert registrar.registered == ["Ctrl+K"]

    def test_unavailable_refuses_everything(self) -> None:
        registrar = RecordingRegistrar()
        registrar.available = False
        assert not registrar.register(Accelerator.parse("Ctrl+K"))


class TestQtShortcutRegistrar:
    """The registrar the running application actually uses.

    It was written against a ``str`` while the service passes a parsed
    ``Accelerator``, and nothing exercised it, so the mismatch survived. These
    drive it through ``HotkeyService`` rather than calling it directly, because
    the type contract is with the service and not with the caller.
    """

    def test_registers_what_the_service_hands_it(self, qapp: object) -> None:
        from nodify.services.hotkey import QtShortcutRegistrar

        registrar = QtShortcutRegistrar(qapp)
        service = HotkeyService(registrar.register, registrar.unregister)

        assert service.register("Ctrl+Space")
        assert service.is_registered
        assert service.current == "Ctrl+Space"
        service.unregister()

    def test_a_parsed_accelerator_does_not_lose_its_modifiers(self, qapp: object) -> None:
        """The bug: an Accelerator was passed straight into a parser expecting text."""
        from PyQt6.QtGui import QKeySequence
        from PyQt6.QtWidgets import QApplication

        from nodify.services.hotkey import QtShortcutRegistrar

        registrar = QtShortcutRegistrar(qapp)
        # Both forms must produce the same Qt sequence, or the hotkey the user
        # sees in the header is not the hotkey that fires.
        from_object = registrar.register(Accelerator.parse("Ctrl+Shift+K"))
        first = registrar._shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)

        registrar.unregister()
        from_text = registrar.register("Ctrl+Shift+K")
        second = registrar._shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)

        assert from_object and from_text
        assert first == second
        assert "Ctrl" in first and "Shift" in first
        assert isinstance(QApplication.instance(), QApplication)

    def test_rebinding_replaces_rather_than_stacks(self, qapp: object) -> None:
        from nodify.services.hotkey import QtShortcutRegistrar

        registrar = QtShortcutRegistrar(qapp)
        service = HotkeyService(registrar.register, registrar.unregister)

        service.register("Ctrl+Space")
        service.register("Ctrl+Shift+K")

        assert service.current == "Ctrl+Shift+K"
        service.unregister()

    def test_unregistering_twice_is_safe(self, qapp: object) -> None:
        from nodify.services.hotkey import QtShortcutRegistrar

        registrar = QtShortcutRegistrar(qapp)
        service = HotkeyService(registrar.register, registrar.unregister)
        service.register("Ctrl+Space")

        service.unregister()
        service.unregister()

        assert not service.is_registered

    def test_activating_the_shortcut_calls_back(self, qapp: object) -> None:
        from nodify.services.hotkey import QtShortcutRegistrar

        pressed: list[int] = []
        registrar = QtShortcutRegistrar(qapp, lambda: pressed.append(1))
        service = HotkeyService(registrar.register, registrar.unregister, on_press=lambda: None)
        service.register("Ctrl+Space")

        # Activating directly is the only way to exercise the wiring without a
        # real keypress, which the offscreen platform will not deliver.
        registrar._shortcut.activated.emit()

        assert pressed == [1]
        service.unregister()
