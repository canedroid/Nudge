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
from nodify.services.settings import AppSettings, TileGeometry, save_settings
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
from nodify.ui.tiles.timers_panel import TimersPanel


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

    def restore_layout(self, area: Rect) -> None:
        """Restore the saved geometry, falling back to the default arrangement."""
        self._layout.reset(area)
        bounds = Rect(area.x, area.y, area.width, area.height)
        for index, tile in enumerate(self._layout.state.order):
            geometry = self._settings.tile(index).clamped()
            if geometry.width and geometry.height:
                restored = Rect(geometry.x, geometry.y, geometry.width, geometry.height)
                self._layout.state.tiles[tile] = restored.clamped_to(bounds)
        self._layout.set_bounds(area)

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
        """Release everything. A hidden overlay must cost nothing when stopped."""
        self.stop_watching()
        self.timers_panel.shutdown()

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

    def panels(self) -> dict[TileId, object]:
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
