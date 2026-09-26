"""Application bootstrap.

Owns the ``QApplication`` lifecycle, the surface format, the overlay window, and
the wiring between them. The window is configured once at startup and thereafter
only shown and hidden, so the compositor effect is never re-applied per
interaction — re-creating a layered window is expensive and can leave the
click-through state inconsistent.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication

from nodify.app.dashboard import Dashboard, VaultNotSelectedError, build_services
from nodify.services.hotkey import HotkeyService
from nodify.services.platform import TrayService
from nodify.services.settings import AppSettings, SettingsError, load_or_create, save_settings
from nodify.ui.layout.tile_layout import Rect
from nodify.ui.overlay import Overlay

ORGANISATION = "canedroid"
APPLICATION = "Nodify"

#: Asks the user for a folder. Returns an empty string when they cancel.
ChooseVault = Callable[[], str]


def choose_vault_folder() -> str:
    """Ask for the vault folder with the platform's own dialog.

    Only the application layer calls a real dialog. Everything below it takes the
    result as a plain string, which is why the whole vault path can be exercised
    in tests without a user present.
    """
    from PyQt6.QtWidgets import QFileDialog

    chosen = QFileDialog.getExistingDirectory(
        None,
        "Choose your Nodify vault",
        "",
        QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.ReadOnly,
    )
    return chosen or ""


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
        choose_vault: ChooseVault | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self.app = app
        self.overlay = overlay
        self.settings = settings or AppSettings()
        self.dashboard = dashboard
        self.config_dir = config_dir
        self._choose_vault = choose_vault or choose_vault_folder
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
        # A hotkey that fails, or a folder that is not a vault, has to be visible
        # somewhere or the user is left with an application that quietly did
        # nothing.
        self.overlay.status_message.connect(self.overlay.header.show_status)
        self.overlay.header.hotkey_chip.setText(self.settings.hotkey)

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

    def start_watching(self) -> None:
        """Watch the vault so edits made in Obsidian show up here too."""
        if self.dashboard is None:
            return
        self.dashboard.start_watching()

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

    # ----------------------------------------------------------- the vault

    def resolve_vault(self) -> Path | None:
        """Work out which vault to use, asking the user only if we must.

        Returns ``None`` when there is no vault and the user declined to choose
        one. That is not an error: they may have cancelled by accident, and
        closing the application is better than showing an overlay with nothing
        in it and no explanation.
        """
        remembered = self.settings.vault_path.strip()
        if remembered and Path(remembered).is_dir():
            return Path(remembered)

        chosen = self._choose_vault().strip()
        if not chosen:
            return None

        path = Path(chosen)
        self.remember_vault(path)
        return path

    def remember_vault(self, path: Path) -> None:
        """Store the chosen vault so the dialog is not shown again.

        A failure to write is not fatal: the vault still works for this session,
        and the user will be asked again next time rather than being left with a
        vault they cannot get back to.
        """
        self.settings = self.settings.with_vault_path(str(path))
        try:
            save_settings(self.settings, self.config_dir)
        except SettingsError:
            self.report(f"Using {path}, but it could not be remembered for next time.")

    def build_dashboard(self, vault_path: Path) -> Dashboard | None:
        """Open the vault and build the panel set for it.

        A folder that is missing or is not a vault is reported and returns
        ``None``, rather than raising out of the startup path where the user would
        see a crashed window and nothing else.
        """
        try:
            services = build_services(vault_path)
        except VaultNotSelectedError as exc:
            self.report(f"That folder cannot be used as a vault: {exc}")
            return None
        return Dashboard(services, self.settings)

    def report(self, message: str) -> None:
        """Surface a message the user needs to see.

        The overlay has no room for chrome that does not disturb the layout, so
        messages are collected and shown in the header's status area.
        """
        self.overlay.show_status(message)

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
    argv: Sequence[str] | None = None,
    config_dir: Path | None = None,
    *,
    choose_vault: ChooseVault | None = None,
) -> Application:
    """Build the application with settings loaded from disk.

    Deliberately does **not** resolve the vault. This function is also how tests
    and tools construct an application, and prompting for a folder here would put
    a modal dialog in front of anything that merely wanted the settings. Vault
    resolution belongs to the entry point, which is the only place that knows a
    person is present.
    """
    app, overlay = build_application(argv)
    settings = load_or_create(config_dir).settings
    return Application(
        app,
        overlay,
        settings,
        choose_vault=choose_vault,
        config_dir=config_dir,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python -m nodify``."""
    application = build_configured_application(argv)
    vault = application.resolve_vault()
    if vault is None:
        # The user cancelled the folder dialog. There is nothing to show, and an
        # empty overlay on screen would look like a hang rather than a decision.
        return 0
    application.dashboard = application.build_dashboard(vault)
    if application.dashboard is None:
        # The folder is not a usable vault. Already reported by build_dashboard.
        return 0
    application.start()
    application.start_watching()
    application.show()
    return application.app.exec()
