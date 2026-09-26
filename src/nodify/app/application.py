"""Application bootstrap.

Owns the ``QApplication`` lifecycle, the surface format, the overlay window, and
the wiring between them. The window is configured once at startup and thereafter
only shown and hidden, so the compositor effect is never re-applied per
interaction — re-creating a layered window is expensive and can leave the
click-through state inconsistent.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication

from nodify.app.dashboard import Dashboard
from nodify.services.hotkey import HotkeyService
from nodify.services.platform import TrayService
from nodify.services.settings import AppSettings, load_or_create
from nodify.ui.layout.tile_layout import Rect
from nodify.ui.overlay import Overlay

ORGANISATION = "canedroid"
APPLICATION = "Nodify"


def configure_surface_format() -> None:
    """Give the window surface an alpha channel.

    This must run **before** ``QApplication`` is constructed. Qt 6 will otherwise
    silently fall back to a surface without an alpha channel, and
    ``WA_TranslucentBackground`` then renders an opaque black background — which
    also breaks click-through, because an opaque window is hit-testable
    everywhere. See PLAN.MD section 8.2.
    """
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setRedBufferSize(8)
    fmt.setGreenBufferSize(8)
    fmt.setBlueBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)


class Application:
    """The running application: window, hotkey, tray and settings.

    Kept as a class rather than loose functions so the pieces can be handed a
    test double for any of them, and so shutdown has one obvious place.
    """

    def __init__(
        self,
        app: QApplication,
        overlay: Overlay,
        settings: AppSettings | None = None,
        *,
        dashboard: Dashboard | None = None,
        hotkey: HotkeyService | None = None,
        tray: TrayService | None = None,
    ) -> None:
        self.app = app
        self.overlay = overlay
        self.settings = settings or AppSettings()
        self.dashboard = dashboard
        self.hotkey = hotkey or HotkeyService(on_press=self.toggle)
        self.tray = tray or TrayService(
            application=app,
            on_toggle=self.toggle,
            on_settings=self.open_settings,
            on_quit=self.quit,
        )
        self._wire()

    def _wire(self) -> None:
        self.overlay.header.settings_button.clicked.connect(self.open_settings)
        self.overlay.header.hide_button.clicked.connect(self.overlay.hide)
        self.overlay.header.quit_button.clicked.connect(self.quit)
        self.overlay.resized.connect(self._on_overlay_resized)

    def _on_overlay_resized(self) -> None:
        """Re-place the tiles when the overlay changes size.

        The saved rectangles are absolute, so a resolution change or a move to
        another monitor would otherwise leave tiles outside the visible area.
        """
        if self.dashboard is not None:
            self.dashboard.relayout(self.tile_area())

    # ------------------------------------------------------------- behaviour

    def is_visible(self) -> bool:
        return self.overlay.isVisible()

    def show(self) -> None:
        """Show the overlay over the screen under the cursor."""
        self.overlay.show_overlay()
        self.overlay.set_click_through(self.settings.click_through)
        self.mount_dashboard()

    def mount_dashboard(self) -> None:
        """Put the panels on screen, now that the overlay has a real size.

        Mounting happens here rather than at construction because the tiles need
        the overlay's geometry, which is only known once it covers a screen.
        """
        if self.dashboard is None:
            return
        self.dashboard.mount(self.overlay, self.tile_area())

    def hide(self) -> None:
        self.overlay.hide()

    def toggle(self) -> None:
        """Show or hide. This is what the global hotkey calls."""
        if self.is_visible():
            self.hide()
        else:
            self.show()

    def set_click_through(self, enabled: bool) -> None:
        """Toggle click-through and remember the choice."""
        self.settings = type(self.settings)(
            hotkey=self.settings.hotkey,
            backdrop=self.settings.backdrop,
            opacity=self.settings.opacity,
            click_through=enabled,
            vault_path=self.settings.vault_path,
            layout=self.settings.layout,
        )
        self.overlay.set_click_through(enabled)

    def open_settings(self) -> None:
        """Settings are not built yet. The button reports that rather than
        silently doing nothing, which would look like a broken application."""
        self.overlay.status_message.emit("Settings are not available yet.")

    def start(self) -> bool:
        """Register the hotkey. Returns whether it worked.

        A failure is not fatal: the user can still use the tray to show the
        overlay, and the reason is available for the UI to explain.
        """
        if not self.hotkey.register(self.settings.hotkey):
            self.overlay.status_message.emit(
                self.hotkey.last_error or "The hotkey could not be registered."
            )
            return False
        return True

    def quit(self) -> None:
        """Release the hotkey, stop the tray, then end the process."""
        self.hotkey.unregister()
        if self.dashboard is not None:
            self.dashboard.shutdown()
        self.tray.hide()
        self.app.quit()

    def tile_area(self) -> Rect:
        """The overlay region the tiles occupy, as layout coordinates."""
        area = self.overlay.tile_area()
        return Rect(area.x(), area.y(), area.width(), area.height())


def build_application(argv: Sequence[str] | None = None) -> tuple[QApplication, Overlay]:
    """Create the application and its overlay, wired to each other.

    An existing ``QApplication`` is reused rather than replaced. Qt permits exactly
    one per process, and constructing a second one is a hard access violation rather
    than an exception, so a caller that already has an instance must not be given a
    crash.
    """
    configure_surface_format()
    existing = QApplication.instance()
    app = (
        existing
        if isinstance(existing, QApplication)
        else QApplication(list(argv) if argv is not None else sys.argv)
    )
    app.setApplicationName(APPLICATION)
    app.setOrganizationName(ORGANISATION)
    # The overlay hides rather than closes, so the process must outlive the last
    # visible window. Quitting is explicit, from the tray or the Quit button.
    app.setQuitOnLastWindowClosed(False)

    overlay = Overlay(on_quit=app.quit)
    return app, overlay


def build_configured_application(
    argv: Sequence[str] | None = None, config_dir: Path | None = None
) -> Application:
    """Build the application with settings loaded from disk."""
    app, overlay = build_application(argv)
    settings = load_or_create(config_dir).settings
    return Application(app, overlay, settings)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python -m nodify``."""
    application = build_configured_application(argv)
    application.start()
    application.show()
    return application.app.exec()
