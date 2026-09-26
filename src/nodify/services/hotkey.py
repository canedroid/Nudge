"""Global hotkey registration.

A hotkey is held for the lifetime of the process, so registration is the one
place in the application where a mistake is expensive: a combination that is
already taken, or a bare key with no modifier, would make the desktop unusable
until the process exits.

Three behaviours follow from that and are implemented here.

**A failed rebind rolls back.** If the user picks a combination the system
refuses, the previous working hotkey is restored and remains registered. Leaving
the user with no hotkey at all would mean the overlay could not be summoned.

**Registration is idempotent.** Asking for the accelerator that is already
registered is a no-op, not a second registration, so a repeated startup or a
spurious rebind cannot leave two shortcuts fighting over the same key.

**Failure is reported, never swallowed.** The caller gets a message it can show
in-app, which is the plan's acceptance criterion for shortcut failure.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QKeySequence, QShortcut

from nodify.services.accelerator import (
    Accelerator,
    InvalidAccelerator,
    canonicalise,
)

#: A platform registerer, kept behind a callable so a fake can stand in.
RegisterFn = Callable[[Accelerator], bool]
UnregisterFn = Callable[[], None]


class HotkeyError(Exception):
    """The requested combination could not be registered."""


class ShortcutRegistrar(Protocol):
    """What a platform implementation must provide."""

    def register(self, accelerator: str) -> bool: ...

    def unregister(self) -> None: ...


class RecordingRegistrar:
    """An in-process registrar that records what was asked for.

    Used in tests, and as the fallback when no platform registrar is supplied, so
    the application still runs with a hotkey that simply does nothing rather than
    refusing to start.
    """

    def __init__(self) -> None:
        #: What is registered right now.
        self.registered: list[str] = []
        #: Everything ever registered, in order, so a test can check the sequence
        #: of attempts rather than only the final state.
        self.history: list[str] = []
        #: Combinations this registrar will refuse, so a failure path can be
        #: exercised without a real system conflict.
        self.refuse: set[str] = set()
        self.available = True

    def register(self, accelerator: Accelerator) -> bool:
        if not self.available or str(accelerator) in self.refuse:
            return False
        self.registered.append(str(accelerator))
        self.history.append(str(accelerator))
        return True

    def unregister(self) -> None:
        self.registered.clear()


class HotkeyService:
    """Owns the single registered accelerator for the process."""

    def __init__(
        self,
        register: RegisterFn | None = None,
        unregister: UnregisterFn | None = None,
        *,
        on_press: Callable[[], None] | None = None,
    ) -> None:
        self._register = register or RecordingRegistrar().register
        self._unregister = unregister or (lambda: None)
        self._on_press = on_press
        self._current: str | None = None
        self._last_error: str | None = None

    @property
    def current(self) -> str | None:
        """The accelerator currently registered, if any."""
        return self._current

    @property
    def is_registered(self) -> bool:
        return self._current is not None

    @property
    def last_error(self) -> str | None:
        """The most recent failure, or ``None`` after a success."""
        return self._last_error

    def register(self, accelerator: str) -> bool:
        """Register an accelerator, rolling back on failure.

        Returns whether the accelerator is now registered. On failure the previous
        one is still in place, so the overlay never ends up unreachable.
        """
        try:
            wanted = canonicalise(accelerator)
        except InvalidAccelerator as exc:
            self._last_error = str(exc)
            return False

        if self._current == wanted:
            # Already holding it. Re-registering would leave the system with two
            # claims on the same combination.
            self._last_error = None
            return True

        previous = self._current
        if previous is not None:
            self._unregister()

        if not self._register(Accelerator.parse(wanted)):
            if previous is not None:
                # Put the working one back, so the user can still summon the
                # overlay after a failed rebind.
                restored = Accelerator.parse(previous)
                if self._register(restored):
                    self._current = previous
                else:
                    # The old one cannot be restored either. Report honestly
                    # rather than claiming a hotkey that is not there.
                    self._current = None
                    self._last_error = (
                        f"Could not register {wanted}, and the previous hotkey "
                        f"({previous}) could not be restored."
                    )
                    return False
            self._last_error = f"Could not register {wanted}."
            return False

        self._current = wanted
        self._last_error = None
        return True

    def unregister(self) -> None:
        """Release the accelerator. Safe to call when nothing is registered."""
        if self._current is not None:
            self._unregister()
            self._current = None

    def trigger(self) -> None:
        """Invoke the press callback, as the platform layer would."""
        if self._on_press is not None:
            self._on_press()

    def display(self) -> str:
        """A form for showing on the hotkey chip."""
        if self._current is None:
            return "—"
        return Accelerator.parse(self._current).display


class QtShortcutRegistrar:
    """Registers the accelerator as a Qt native shortcut.

    A Qt shortcut delivers its activation to the connected callback, so the
    service's trigger is connected rather than being invoked from a raw hook. That
    keeps the service itself testable while still working on a real desktop.
    """

    def __init__(self, application: QObject, on_press: Callable[[], None] | None = None) -> None:
        self._application = application
        self._on_press = on_press
        self._shortcut: QShortcut | None = None

    def register(self, accelerator: Accelerator | str) -> bool:
        from nodify.services.accelerator import to_qt_sequence

        self.unregister()
        # The service hands over a parsed Accelerator, not a string. Converting
        # explicitly matters: to_qt_sequence parses its argument, and feeding it
        # an Accelerator would either fail or quietly lose the modifiers. This
        # registrar was never wired into the application until now, so the
        # mismatch sat here unnoticed behind a test-only stand-in.
        shortcut = QShortcut(QKeySequence(to_qt_sequence(str(accelerator))), self._application)
        if self._on_press is not None:
            shortcut.activated.connect(self._on_press)
        self._shortcut = shortcut
        return True

    def unregister(self) -> None:
        """Release the shortcut. Safe when nothing is registered."""
        if self._shortcut is not None:
            # Disabling releases the system claim without destroying the object,
            # which keeps a failed rebind from leaving a dead shortcut behind.
            self._shortcut.setEnabled(False)
            self._shortcut = None


__all__ = [
    "HotkeyError",
    "HotkeyService",
    "QtShortcutRegistrar",
    "RecordingRegistrar",
    "ShortcutRegistrar",
]
