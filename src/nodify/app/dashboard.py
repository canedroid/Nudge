"""The dashboard: four tiles, wired to one vault.

This is where the independently built packages meet. Its job is composition and
nothing else — it constructs the repositories, mounts the panels, and keeps them
in step. Every decision about how a feature behaves lives in that feature's own
module, which is what makes Package J's criterion "changing one feature service
does not require changing unrelated feature components" true rather than
aspirational.

**Cross-panel refresh is one-directional.** Any panel that mutates emits, and the
panels that display someone else's data reload. A panel never reaches into
another's repository.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QWidget

from nodify.adapters.category_repo import FileSystemCategoryRepository
from nodify.adapters.note_repo import MarkdownNoteRepository
from nodify.adapters.task_repo import MarkdownTaskRepository
from nodify.adapters.timer_repo import MarkdownTimerRepository
from nodify.adapters.vault import Vault
from nodify.domain.clock import Clock, SystemClock
from nodify.domain.reminder_engine import ReminderEngine
from nodify.services.notifications import (
    NotificationService,
    ReminderNotifier,
)
from nodify.services.phone_bridge import PhoneBridgeService
from nodify.services.platform import CoalescingWatcher, VaultChange
from nodify.services.settings import (
    VALID_BACKDROPS,
    AppSettings,
    TileGeometry,
    save_settings,
)
from nodify.ui.layout.tile_layout import (
    DEFAULT_ORDER,
    LayoutState,
    Rect,
    TileId,
    TileLayout,
)
from nodify.ui.tiles.files_panel import FilesPanel
from nodify.ui.tiles.notes_panel import NotesPanel
from nodify.ui.tiles.tasks_panel import TasksPanel
from nodify.ui.tiles.tile_frame import TileFrame
from nodify.ui.tiles.timers_panel import TimersPanel
from nodify.ui.win_backdrop import Backdrop


class VaultNotSelectedError(Exception):
    """The application has no vault to work with."""


@dataclass
class Services:
    """Every adapter the dashboard needs, built from one vault.

    Constructed once and passed around, so no two panels can end up with different
    repositories pointing at the same files.
    """

    vault: Vault
    clock: Clock = field(default_factory=SystemClock)

    def __post_init__(self) -> None:
        self.notes = MarkdownNoteRepository(self.vault, self.clock)
        self.categories = FileSystemCategoryRepository(self.vault)
        self.tasks = MarkdownTaskRepository(self.vault, self.clock)
        self.timers = MarkdownTimerRepository(self.vault, self.clock)
        self.files = self.vault.index
        self.reminders = ReminderEngine(self.clock)

    def now(self) -> datetime:
        return self.clock.now()


class Dashboard:
    """The four feature panels, mounted and kept in step.

    Construction is cheap and does no I/O beyond what the repositories do to
    answer their first query, so a test can build one against a temporary vault.
    """

    def __init__(
        self,
        services: Services,
        settings: AppSettings | None = None,
        *,
        confirm: Callable[[str, str], bool] | None = None,
        prompt: Callable[[str, str, str], str | None] | None = None,
    ) -> None:
        self._services = services
        self._settings = settings or AppSettings()
        self._confirm = confirm
        self._prompt = prompt
        self._watcher: CoalescingWatcher | None = None
        self._notifications = NotificationService()
        self._notifier = ReminderNotifier(services.timers, self._notifications, services.clock.now)
        self._bridge = PhoneBridgeService(now=services.clock.now)
        self._layout = TileLayout(LayoutState())
        #: The mounted tile frames, empty until :meth:`mount` is called.
        #: The tile being dragged or resized right now, or ``None``. Held so a
        #: second gesture can be recognised as a continuation rather than a new
        #: arrangement.
        self._dragging: TileId | None = None
        self._frames: dict[TileId, TileFrame] = {}
        #: The widget the frames are parented to, so a remount can be detected.
        self._mounted_to: QWidget | None = None
        #: Whether :meth:`shutdown` has already run.
        self._stopped = False
        #: Recoverable problems reported by panels, newest last.
        self.errors: list[str] = []

        self.notes_panel = NotesPanel(services.notes)
        self.tasks_panel = TasksPanel(services.tasks, services.clock.now)
        self.timers_panel = TimersPanel(services.timers, services.clock.now)
        self.files_panel = self._build_files_panel()

        self._connect()

    def _build_files_panel(self) -> FilesPanel:
        """The Files panel takes injected dialogs so it stays testable."""
        if self._confirm is not None and self._prompt is not None:
            return FilesPanel(
                self._services.files,
                self._services.categories,
                self._services.notes,
                confirm=self._confirm,
                prompt=self._prompt,
            )
        return FilesPanel(self._services.files, self._services.categories, self._services.notes)

    # ------------------------------------------------------------- wiring

    def _connect(self) -> None:
        """Wire cross-panel refresh.

        Notes changing affects the Files listing, so a note edit reloads it. Task
        changes stay inside the tasks panel, because nothing else displays tasks.
        A timer firing is announced through the notifier, which records the
        notification before delivering it.
        """
        self.notes_panel.notes_changed.connect(self._on_notes_changed)
        self.notes_panel.error_occurred.connect(self._on_error)
        self.tasks_panel.tasks_changed.connect(self._on_tasks_changed)
        self.tasks_panel.error_occurred.connect(self._on_error)
        self.files_panel.files_changed.connect(self._on_files_changed)
        self.files_panel.error_occurred.connect(self._on_error)
        self.timers_panel.timers_changed.connect(self._on_timers_changed)
        self.timers_panel.timer_due.connect(self._on_timer_due)
        self.timers_panel.error_occurred.connect(self._on_error)

    def _on_notes_changed(self) -> None:
        """A note was created, edited, moved or deleted.

        The Files panel lists the vault, so it has to re-read. The Notes panel
        reloads itself; it must not be told to.
        """
        self.files_panel.reload()

    def _on_tasks_changed(self) -> None:
        """A task changed. Nothing else displays tasks, so nothing else reloads."""

    def _on_files_changed(self) -> None:
        """A file moved between categories. The Notes list follows the folder."""
        self.notes_panel.reload()

    def _on_timers_changed(self) -> None:
        """A timer changed. Nothing else displays timers."""

    def _on_timer_due(self, timer_id: str) -> None:
        """A timer came due: record it, then notify exactly once."""
        try:
            timer = self._services.timers.get(timer_id)
        except Exception:  # noqa: BLE001 - a due notification must not crash the UI
            return
        # The notifier marks the timer before attempting delivery, so a failure
        # cannot cause the same alert again on the next launch.
        self._notifier.announce(timer_id, timer.title, timer.notes)
        self.timers_panel.forget_announcement(timer_id)

    def _on_error(self, message: str) -> None:
        """Collect a panel's recoverable problem.

        The overlay has nowhere to put a message without disturbing the layout the
        user arranged, so errors are collected for the caller to surface.
        """
        self.errors.append(message)

    # ------------------------------------------------------------- layout

    def apply_layout(self, area: Rect, *, gap: int = 16) -> None:
        """Lay the panels out across ``area`` and store the geometry."""
        self._layout.reset(area, gap=gap)
        self._persist_layout()

    def restore_layout(self, area: Rect, *, gap: int = 16) -> None:
        """Restore the saved geometry, falling back to the default arrangement.

        This deliberately does not persist. Mounting runs on every launch, and
        persisting here would overwrite the arrangement the user saved with the
        default one the first time the overlay is shown.
        """
        self._layout.reset(area, gap=gap)
        bounds = Rect(area.x, area.y, area.width, area.height)
        stored = self._settings.layout
        for index, tile in enumerate(self._layout.state.order):
            # Only tiles the user actually arranged are restored. ``AppSettings.tile``
            # answers an unstored index with a default 520x640 rectangle, whose width
            # is truthy, so a truthiness guard would look like it worked while
            # quietly replacing the computed default with four tiles stacked at the
            # origin.
            if index >= len(stored):
                continue
            geometry = stored[index].clamped()
            if geometry.width and geometry.height:
                restored = Rect(geometry.x, geometry.y, geometry.width, geometry.height)
                self._layout.state.tiles[tile] = restored.clamped_to(bounds)
        self._layout.set_bounds(area)

    # ------------------------------------------------------------- mounting

    def mount(self, parent: QWidget, area: Rect, *, gap: int = 16) -> dict[TileId, TileFrame]:
        """Host every panel in a draggable top-level frame over ``parent``.

        This is what makes the panels visible. Until a panel is inside a real
        window with a real geometry, it exists but is never painted, which is the
        difference between "the tests pass" and "there is an application".

        The frames are separate top-level windows rather than children of
        ``parent``, because the compositor can only blur behind a window and the
        tiles need to be glass. ``parent`` is still needed: it owns the header and
        the click-through gutter, and its top-left is the origin the layout's
        overlay-relative rectangles are measured from.

        Idempotent: showing the overlay repeatedly re-uses the frames rather than
        stacking a second set on top of the first.
        """
        if self._mounted_to is parent and self._frames:
            self.relayout(area, gap=gap)
            return self._frames

        self.restore_layout(area, gap=gap)
        origin = parent.mapToGlobal(QPoint(0, 0))
        backdrop = self._backdrop()

        frames: dict[TileId, TileFrame] = {}
        for tile in DEFAULT_ORDER:
            frame = TileFrame(tile, self.panels()[tile], backdrop)
            frame.apply_rect(self.geometry_for(tile), origin)
            self._connect_frame(frame)
            # Parenting does not show a child, and a top-level window is not shown
            # by construction either. Qt only shows widgets that existed when their
            # parent was shown, and a window created into an already visible
            # desktop stays hidden until it is told otherwise.
            frame.show()
            frames[tile] = frame
        self._frames = frames
        self._mounted_to = parent
        self._stopped = False
        self._raise_frames()
        return frames

    def _backdrop(self) -> Backdrop:
        """The compositor effect the tiles should ask for.

        ``AppSettings`` already clamps the stored value to the valid set, so the
        membership test here is belt and braces for a settings object built by
        hand in a test. Falling back to acrylic rather than to nothing is
        deliberate: a wrong value should still look like glass.
        """
        stored = self._settings.backdrop
        if stored in VALID_BACKDROPS:
            return Backdrop(stored)
        return Backdrop.ACRYLIC

    def _connect_frame(self, frame: TileFrame) -> None:
        """Wire one frame's drag and resize signals to the layout.

        This is the whole of "the tiles can be moved": :class:`TileFrame` has
        always computed the geometry and :class:`TileLayout` has always known what
        to do with it, but nothing joined the two, so every drag ended in a
        signal nobody received.
        """
        frame.tile_moving.connect(self._on_tile_moving)
        frame.tile_dropped.connect(self._on_tile_dropped)
        frame.drag_started.connect(self._on_drag_started)
        frame.drag_finished.connect(self._on_drag_finished)

    def _on_tile_moving(self, tile: TileId, rect: Rect) -> None:
        """Follow the pointer during a drag or resize.

        A zero-area rectangle means a move and a real one means a resize, which
        is how :meth:`TileFrame._perform_drag` and :meth:`TileFrame._perform_resize`
        already differ. Using that rather than a new signal keeps the two paths
        distinguishable without widening the signal signatures.
        """
        if rect.width or rect.height:
            self._layout.resize_tile(tile, rect)
        else:
            self._layout.move_tile(tile, rect)
        self._apply_frame(tile)

    def _on_tile_dropped(self, tile: TileId, rect: Rect) -> None:
        """Commit a finished drag, swapping if it landed on another tile.

        The swap happens here rather than on every move so the tiles visibly
        travel with the pointer and only trade places on release, and so a
        dropped arrangement is written to settings exactly once per gesture
        instead of sixty times a second.
        """
        self._layout.drop_tile(tile, rect)
        # A swap moves two tiles, so every frame is re-applied rather than the
        # one being dragged.
        self._apply_all_frames()
        self._persist_layout()

    def _on_drag_started(self, tile: TileId) -> None:
        self._dragging = tile

    def _on_drag_finished(self, tile: TileId) -> None:
        self._dragging = None

    def _apply_frame(self, tile: TileId) -> None:
        frame = self._frames.get(tile)
        if frame is not None:
            frame.apply_rect(self.geometry_for(tile), self._origin())

    def _apply_all_frames(self) -> None:
        for tile in self._frames:
            self._apply_frame(tile)

    def _origin(self) -> QPoint:
        """The mounted overlay's top-left in global coordinates."""
        if self._mounted_to is None:
            return QPoint(0, 0)
        return self._mounted_to.mapToGlobal(QPoint(0, 0))

    def _raise_frames(self) -> None:
        """Put the tiles above the overlay.

        Both the overlay and the tiles are always-on-top, so their order within
        that band is decided by activation, not by declaration. A tile that ends
        up underneath would still be visible through the overlay's transparent
        gutter but would not receive a click, which looks like a dead spot rather
        than a stacking bug.
        """
        for frame in self._frames.values():
            frame.raise_()
        if self._mounted_to is not None:
            self._mounted_to.lower()

    def relayout(self, area: Rect, *, gap: int = 16) -> None:
        """Re-clamp and re-apply the frames after the area changed size.

        Used on a resolution change. Saved rectangles are absolute, so on a
        smaller screen they can fall outside the new bounds; the restore path
        clamps them rather than letting a tile land somewhere unreachable.
        """
        if not self._frames:
            return
        self.restore_layout(area, gap=gap)
        self._apply_all_frames()
        self._raise_frames()

    @property
    def frames(self) -> dict[TileId, TileFrame]:
        """The mounted frames, keyed by tile. Empty until :meth:`mount`."""
        return dict(self._frames)

    @property
    def is_mounted(self) -> bool:
        return bool(self._frames)

    def is_visible(self) -> bool:
        """Whether any tile is actually on screen.

        ``is_mounted`` is not the same question: frames stay mounted (and keep
        their arrangement) while hidden, so the hotkey toggle has to ask whether
        anything is showing.
        """
        return any(frame.isVisible() for frame in self._frames.values())

    def show_frames(self) -> None:
        """Put every tile back on screen, above the overlay that hosts it."""
        if not self._frames:
            return
        for frame in self._frames.values():
            frame.show()
        self.raise_frames()

    def hide_frames(self) -> None:
        """Take every tile off screen.

        The tiles are top-level windows now, so hiding the overlay alone is not
        hiding the dashboard: it would leave four tiles stranded over whatever
        the user switched to, with no header to close them.
        """
        for frame in self._frames.values():
            frame.hide()

    def raise_frames(self) -> None:
        for frame in self._frames.values():
            frame.raise_()

    def layout_state(self) -> LayoutState:
        return self._layout.state

    def geometry_for(self, tile: TileId) -> Rect:
        return self._layout.state.rect_for(tile)

    def persist_layout(self) -> None:
        self._persist_layout()

    def _persist_layout(self) -> None:
        """Write the current geometry back to settings.

        The backdrop that actually took effect is stored, not the one requested,
        and likewise the geometry the user actually dragged to.
        """
        settings = self._settings
        for index, tile in enumerate(self._layout.state.order):
            rect = self._layout.state.rect_for(tile)
            settings = settings.with_tile(
                index, TileGeometry(rect.x, rect.y, rect.width, rect.height)
            )
        self._settings = settings
        # A failed save must not break the session; the layout the user arranged
        # is still correct for this run, it just will not be remembered.
        with contextlib.suppress(Exception):
            save_settings(settings)

    # ------------------------------------------------------------- watching

    def start_watching(self) -> None:
        """Begin watching the vault. Calling twice does not add a second observer."""
        if self._watcher is not None and self._watcher.is_running():
            return
        self._watcher = CoalescingWatcher(self._services.vault.root, self._on_vault_change)
        self._watcher.start()

    def stop_watching(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    def _on_vault_change(self, changes: list[VaultChange]) -> None:
        """React to external edits.

        The index is authoritative and a watcher is only a prompt to re-scan, so
        this re-reads rather than trying to interpret the individual events.
        """
        self.files_panel.reload()
        self.notes_panel.reload()
        self.tasks_panel.reload()
        self.timers_panel.reload()

    @property
    def watcher(self) -> CoalescingWatcher | None:
        return self._watcher

    # ------------------------------------------------------------- shutdown

    def shutdown(self) -> None:
        """Release everything. A hidden overlay must cost nothing when stopped.

        Safe to call more than once. Both the quit path and a test teardown can
        arrive here, and a second pass would reach a QTimer whose C++ object was
        already destroyed along with its window.
        """
        if self._stopped:
            return
        self._stopped = True
        self.stop_watching()
        self.timers_panel.shutdown()
        for frame in self._frames.values():
            frame.setParent(None)
        self._frames.clear()
        self._mounted_to = None

    # ------------------------------------------------------------ accessors

    @property
    def services(self) -> Services:
        return self._services

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def bridge(self) -> PhoneBridgeService:
        return self._bridge

    @property
    def notifications(self) -> NotificationService:
        return self._notifications

    def panels(self) -> dict[TileId, QWidget]:
        """Every mounted panel, keyed by the tile it lives in."""
        return {
            TileId.TIMERS: self.timers_panel,
            TileId.NOTES: self.notes_panel,
            TileId.TODOS: self.tasks_panel,
            TileId.FILES: self.files_panel,
        }

    def refresh_all(self) -> None:
        """Re-read every panel from disk."""
        self.notes_panel.reload()
        self.tasks_panel.reload()
        self.timers_panel.reload()
        self.files_panel.reload()


def build_services(vault_path: Path, clock: Clock | None = None) -> Services:
    """Open an existing vault and build the service set for it.

    The folder must already be a vault. Creating one is a separate, explicit action:
    silently conjuring a vault at a mistyped path would leave the user looking at an
    empty overlay and no idea why, and the original notes would be in a folder
    nobody was ever told about.
    """
    root = Path(vault_path)
    if not root.is_dir():
        raise VaultNotSelectedError(f"That folder does not exist: {root}")

    vault = Vault(root)
    try:
        vault.open()
    except Exception as exc:  # noqa: BLE001 - reported to the user, not raised raw
        raise VaultNotSelectedError(str(exc)) from exc
    return Services(vault=vault, clock=clock or SystemClock())


__all__ = [
    "DEFAULT_ORDER",
    "Dashboard",
    "Services",
    "VaultNotSelectedError",
    "build_services",
]
