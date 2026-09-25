"""Watchdog backend for the vault watcher.

Kept in its own module so :mod:`nodify.services.platform` does not import
watchdog at all, which keeps the polling fallback and the test doubles free of a
hard dependency on an event-loop integration.

The debounce lives here because this is where the event loop is. Bursts are
coalesced on a timer rather than delivered one by one, because a sync client or an
editor save can touch many files at once and each event would otherwise cause a
full re-read of the vault.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from nodify.services.platform import WATCH_DEBOUNCE_SECONDS, ChangeKind, VaultChange

if TYPE_CHECKING:
    from watchdog.events import FileSystemEventHandler

#: Only Markdown is watched. A sync client writes temp files constantly, and
#: reacting to those would cause work for nothing.
WATCHED_SUFFIXES = frozenset({".md", ".markdown"})


def _classify(kind: object, _path: Path) -> ChangeKind:
    """Map a watchdog event kind onto our own."""
    name = str(getattr(kind, "name", kind))
    if name in ("CREATED", "MOVED"):
        return ChangeKind.CREATED
    if name == "DELETED":
        return ChangeKind.DELETED
    return ChangeKind.MODIFIED


def create_backend(root: Path, on_change: Callable[[list[VaultChange]], None]) -> object | None:
    """Build a watchdog observer that batches events and calls ``on_change``.

    Returns ``None`` when watchdog is unavailable or the folder is missing, so the
    caller can fall back to polling rather than failing to watch at all.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer  # noqa: F401 - availability probe
    except ImportError:  # pragma: no cover - watchdog is a hard dependency
        return None

    root = Path(root)
    if not root.is_dir():
        return None

    class _Handler(FileSystemEventHandler):
        """Collects events; the pump thread drains them in batches."""

        def __init__(self) -> None:
            self.pending: list[VaultChange] = []
            self.dirty = False

        def _queue(self, kind: object, path_str: str) -> None:
            path = Path(path_str)
            if path.suffix.lower() not in WATCHED_SUFFIXES:
                return
            self.pending.append(VaultChange(_classify(kind, path), path))
            self.dirty = True

        def drain(self) -> list[VaultChange]:
            """Take everything queued so far, leaving the queue empty."""
            batch = self.pending
            self.pending = []
            self.dirty = False
            return batch

        def on_any_event(self, event: object) -> None:
            if getattr(event, "is_directory", False):
                return
            self._queue(getattr(event, "event_type", ""), str(getattr(event, "src_path", "")))

    return _BatchingObserver(root, _Handler(), on_change)


class _BatchingObserver:
    """A watchdog observer that coalesces bursts before reporting them.

    Wrapping rather than subclassing keeps watchdog out of the type of anything the
    rest of the application touches, and lets the pump thread's stop event be a
    real attribute rather than something attached to a third-party object.
    """

    def __init__(
        self,
        root: Path,
        handler: FileSystemEventHandler,
        on_change: Callable[[list[VaultChange]], None],
    ) -> None:
        from watchdog.observers import Observer

        self._on_change = on_change
        self._handler = handler
        self._observer = Observer()
        self._observer.schedule(handler, str(root), recursive=True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start observing. Calling twice does not register a second thread."""
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._observer.start()

        # The coalescing loop runs on its own thread. A blocking sleep on the
        # observer's thread would occupy the one thread meant to be dispatching
        # events, so it would stall the very events it is waiting for.
        handler = self._handler
        stop_event = self._stop

        def pump() -> None:
            while not stop_event.wait(WATCH_DEBOUNCE_SECONDS):
                if not handler.dirty:  # type: ignore[attr-defined]
                    continue
                batch = handler.drain()  # type: ignore[attr-defined]
                if batch:
                    self._on_change(batch)

        self._thread = threading.Thread(target=pump, name="nodify-vault-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop observing. Safe to call when not started."""
        self._stop.set()
        self._observer.stop()
        self._observer.join(timeout=2.0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
