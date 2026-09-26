"""File watching, single-instance detection, and the tray.

All three exist so the overlay can live in the background without being intrusive
or wasteful. Each is behind an interface so the application can be driven in a
test without a desktop session.

The behaviours that matter are the ones about *not* doing work. A hidden overlay
costs no polling and no running timer; a repeated startup does not register a
second watcher or a duplicate shortcut; and closing the window hides the overlay
rather than ending the process.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from nodify.services.notifications import TrayIconLike

if TYPE_CHECKING:
    from PyQt6.QtGui import QIcon

#: How long to wait before coalescing bursts of file events.
WATCH_DEBOUNCE_SECONDS = 0.4


class ChangeKind(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class VaultChange:
    """One observed change in the vault."""

    kind: ChangeKind
    path: Path

    def __str__(self) -> str:
        return f"{self.kind.value} {self.path.name}"


class VaultWatcher(Protocol):
    """What a watcher must provide."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def is_running(self) -> bool: ...


@dataclass
class CoalescingWatcher:
    """Debounces bursts of filesystem events into a single pass.

    An editor or a sync client can touch twenty files in a moment. Reacting to each
    one would re-read the vault twenty times and leave the UI flickering, so events
    within :data:`WATCH_DEBOUNCE_SECONDS` collapse into one callback.
    """

    root: Path
    on_change: Callable[[list[VaultChange]], None]
    #: The raw event source. A test supplies a fake; production uses watchdog.
    backend: object | None = None
    _pending: list[VaultChange] = field(default_factory=list)
    _running: bool = False
    _handle: object | None = None

    def start(self) -> None:
        """Start watching. Calling twice does not register a second observer."""
        if self._running:
            return
        self._running = True
        self._backend = self._create_backend()
        starter = getattr(self._backend, "start", None)
        if callable(starter):
            starter()

    def stop(self) -> None:
        """Stop watching and drop anything pending. Safe to call twice."""
        if not self._running:
            return
        self._running = False
        self._pending.clear()
        stopper = getattr(self._backend, "stop", None)
        if callable(stopper):
            stopper()
        self._backend = None
        self._handle = None

    def is_running(self) -> bool:
        return self._running

    def _create_backend(self) -> object | None:
        if self.backend is not None:
            return self.backend
        # The import is checked indirectly: the factory returns None when
        # watchdog is absent, which is the only thing this needs to know.
        from nodify.services.watchdog_backend import create_backend

        return create_backend(self.root, self.flush_backend)

    def queue_change(self, change: VaultChange) -> None:
        """Record a change. The backend decides when to flush the batch."""
        if not self._running:
            return
        self._pending.append(change)

    def flush_backend(self, batch: list[VaultChange]) -> None:
        """Entry point the backend calls with a coalesced batch."""
        if not self._running:
            return
        if batch:
            self.on_change(batch)

    def flush(self) -> None:
        """Deliver the accumulated changes and clear them."""
        if not self._pending:
            return
        batch = self._pending
        self._pending = []
        if self._running:
            self.on_change(batch)

    @property
    def pending_count(self) -> int:
        return len(self._pending)


class PollingWatcher:
    """A fallback watcher that stats the tree on an interval.

    Used when watchdog is unavailable. It is more expensive, so it is only started
    when a change actually matters, and never while the overlay is hidden.
    """

    def __init__(self, root: Path, on_change: Callable[[list[VaultChange]], None]) -> None:
        self._root = Path(root)
        self._on_change = on_change
        self._snapshot: dict[Path, int] = {}
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._snapshot = self._scan()

    def stop(self) -> None:
        self._running = False
        self._snapshot = {}

    def is_running(self) -> bool:
        return self._running

    def poll(self) -> list[VaultChange]:
        """One comparison pass. Returns the changes and updates the baseline."""
        if not self._running:
            return []
        current = self._scan()
        changes: list[VaultChange] = []

        for path, mtime in current.items():
            previous = self._snapshot.get(path)
            if previous is None:
                changes.append(VaultChange(ChangeKind.CREATED, path))
            elif previous != mtime:
                changes.append(VaultChange(ChangeKind.MODIFIED, path))
        for path in self._snapshot:
            if path not in current:
                changes.append(VaultChange(ChangeKind.DELETED, path))

        self._snapshot = current
        if changes:
            self._on_change(changes)
        return changes

    def _scan(self) -> dict[Path, int]:
        found: dict[Path, int] = {}
        for area in ("notes", "todos", "timer"):
            base = self._root / area
            if not base.is_dir():
                continue
            for path in base.rglob("*.md"):
                try:
                    found[path] = path.stat().st_mtime_ns
                except OSError:
                    continue
        return found


class SingleInstanceGuard:
    """Ensures only one overlay process claims the application identity.

    A second instance must not open a second fullscreen always-on-top window, and
    must not register a second global hotkey. The guard hands the second process
    the first one's activation signal and then exits.
    """

    def __init__(self, key: str = "Nodify") -> None:
        self._key = key
        self._claimed = False

    def acquire(self) -> bool:
        """Try to become the primary instance.

        Returns ``True`` when this process owns the identity. A single-file lock is
        used rather than a named mutex so the behaviour is identical on every
        platform and testable without platform calls.
        """
        lock = self._lock_path()
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self._claimed = False
            return False
        except OSError:
            # Without a writable location the guard cannot work, so let the
            # process start rather than refusing to launch.
            self._claimed = True
            return True

        try:
            os.write(handle, str(os.getpid()).encode("ascii"))
        finally:
            os.close(handle)

        self._claimed = True
        return True

    def release(self) -> None:
        """Release the identity, so a later launch is not blocked by a stale lock."""
        if not self._claimed:
            return
        self._claimed = False
        self._lock_path().unlink(missing_ok=True)

    @property
    def is_primary(self) -> bool:
        return self._claimed

    def _lock_path(self) -> Path:
        import tempfile

        base = Path(tempfile.gettempdir()) / "nodify"
        return base / f"{self._key}.lock"

    def __enter__(self) -> SingleInstanceGuard:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class TrayAction(StrEnum):
    TOGGLE = "toggle"
    SETTINGS = "settings"
    QUIT = "quit"


@dataclass
class TrayService:
    """The tray icon and its menu.

    Owns Show/hide, Settings and Quit. Nothing else appears in the menu, because
    every additional item is another way for the user to lose the overlay behind a
    dialog they did not ask for.
    """

    application: object
    on_toggle: Callable[[], None] = lambda: None
    on_settings: Callable[[], None] = lambda: None
    on_quit: Callable[[], None] = lambda: None
    icon: TrayIconLike | None = None
    _visible: bool = False

    def show(self) -> None:
        if self.icon is not None:
            show = getattr(self.icon, "show", None)
            if callable(show):
                show()
        self._visible = True

    def hide(self) -> None:
        if self.icon is not None:
            hide = getattr(self.icon, "hide", None)
            if callable(hide):
                hide()
        self._visible = False

    def is_visible(self) -> bool:
        return self._visible

    def actions(self) -> tuple[TrayAction, ...]:
        """The actions the tray offers, in menu order."""
        return (TrayAction.TOGGLE, TrayAction.SETTINGS, TrayAction.QUIT)

    def trigger(self, action: TrayAction) -> None:
        """Invoke an action. The single place menu entries route through."""
        match action:
            case TrayAction.TOGGLE:
                self.on_toggle()
            case TrayAction.SETTINGS:
                self.on_settings()
            case TrayAction.QUIT:
                self.on_quit()

    def notify(self, title: str, body: str) -> bool:
        """Show a notification through the tray icon, if there is one."""
        from nodify.services.notifications import (
            NotificationRequest,
            QtNotificationTransport,
        )

        if self.icon is None:
            return False
        transport = QtNotificationTransport(self.icon)
        return transport.show(NotificationRequest(title=title, body=body))


def iter_vault_areas(root: Path) -> Iterator[Path]:
    """The three vault areas that exist below ``root``."""
    for area in ("notes", "todos", "timer"):
        path = root / area
        if path.is_dir():
            yield path


class QtTrayIcon:
    """A real system tray icon with a menu, for the running application.

    :class:`TrayService` above is a routing abstraction with no Qt in it, which is
    what made it testable. This is the platform half: it owns a
    ``QSystemTrayIcon`` and a menu, paints an icon at runtime rather than shipping
    a binary asset, and reports menu choices back through one callback so the
    service remains the only place an action is dispatched from.
    """

    def __init__(self, on_action: Callable[[TrayAction], None], *, tooltip: str = "Nodify") -> None:
        from PyQt6.QtGui import QAction
        from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

        self._on_action = on_action
        self._menu = QMenu()
        self._system = QSystemTrayIcon(_nodify_icon())
        self._system.setToolTip(tooltip)
        self._system.setContextMenu(self._menu)

        labels = (
            (TrayAction.TOGGLE, "Show / hide"),
            (TrayAction.SETTINGS, "Settings"),
            (TrayAction.QUIT, "Quit"),
        )
        for action, label in labels:
            entry = QAction(label, self._menu)
            # The default argument binds the action now; without it every entry
            # would fire whichever one Qt happened to be holding.
            entry.triggered.connect(lambda _checked=False, a=action: self._on_action(a))
            self._menu.addAction(entry)

        self._system.activated.connect(self._on_activated)

    def _on_activated(self, reason: object) -> None:
        """A click on the icon itself toggles, matching what users expect."""
        from PyQt6.QtWidgets import QSystemTrayIcon

        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_action(TrayAction.TOGGLE)

    def showMessage(  # noqa: N802 - matching the Qt and protocol spelling
        self, title: str, body: str, icon: object = None, msecs: int = 5000
    ) -> None:
        """Show a balloon notification, satisfying :class:`TrayIconLike`."""
        from PyQt6.QtWidgets import QSystemTrayIcon

        self._system.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, int(msecs))

    def show(self) -> None:
        self._system.show()

    def hide(self) -> None:
        self._system.hide()

    def is_visible(self) -> bool:
        return bool(self._system.isVisible())

    def menu_entries(self) -> tuple[object, ...]:
        """The menu's actions, so a caller can inspect or trigger them."""
        return tuple(self._menu.actions())

    def menu_actions(self) -> tuple[str, ...]:
        """The menu entry labels, for a caller that wants to describe the menu."""
        return tuple(action.text() for action in self._menu.actions())


def _nodify_icon() -> QIcon:
    """Paint the tray icon at runtime.

    Drawn rather than shipped so there is no binary asset to keep in sync with the
    palette or to lose in packaging.
    """
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap

    from nodify.ui.kit import colors

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors.PURPLE))
    painter.drawRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    painter.end()
    return QIcon(pixmap)


def tray_is_available() -> bool:
    """Whether this desktop has a system tray at all.

    Windows can run without one, and a session on a server or in a container may
    not. The application has to start either way.
    """
    try:
        from PyQt6.QtWidgets import QSystemTrayIcon

        return bool(QSystemTrayIcon.isSystemTrayAvailable())
    except Exception:  # noqa: BLE001 - absence of a tray is not a failure
        return False


__all__ = [
    "WATCH_DEBOUNCE_SECONDS",
    "ChangeKind",
    "CoalescingWatcher",
    "PollingWatcher",
    "QtTrayIcon",
    "SingleInstanceGuard",
    "TrayAction",
    "TrayService",
    "VaultChange",
    "VaultWatcher",
    "iter_vault_areas",
    "tray_is_available",
]
