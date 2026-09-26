"""The application startup path, end to end.

The bug this exists to catch: the packaged application started, reached its event
loop, and produced no settings file. Nothing in the suite noticed, because every
test passed settings in explicitly or called the loader with defaults. The gap was
"the real entry point, run the real way".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nodify.app.application import (
    Application,
    build_application,
    build_configured_application,
)
from nodify.services.settings import AppSettings, settings_path


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test out of the real %APPDATA%\\Nodify directory."""
    directory = tmp_path / "config"
    monkeypatch.setenv("NODIFY_CONFIG_DIR", str(directory))
    return directory


class TestBuildApplication:
    def test_builds_an_app_and_overlay(self, qapp: object) -> None:
        from PyQt6.QtWidgets import QApplication

        app, overlay = build_application(["nodify-test"])
        assert isinstance(app, QApplication)
        assert overlay is not None
        overlay.deleteLater()

    def test_never_quits_on_the_last_window_closing(self, qapp: object) -> None:
        """The overlay hides rather than closes, so the process must outlive it."""
        app, overlay = build_application(["nodify-test"])
        assert app.quitOnLastWindowClosed() is False
        overlay.deleteLater()

    def test_sets_the_application_identity(self, qapp: object) -> None:
        app, overlay = build_application(["nodify-test"])
        assert app.applicationName() == "Nodify"
        assert app.organizationName() == "canedroid"
        overlay.deleteLater()


class TestConfiguredApplication:
    def test_a_first_run_writes_a_settings_file(self, isolated_config: Path) -> None:
        """The bug this module exists for.

        The application calls the loader with no explicit defaults, and a guard
        that skipped writing in that case meant the first launch of the packaged
        build produced no settings file at all.
        """
        assert not settings_path(isolated_config).exists()

        application = build_configured_application(["nodify-test"], isolated_config)

        assert settings_path(isolated_config).is_file()
        application.overlay.deleteLater()

    def test_the_written_file_is_valid_and_reloadable(self, isolated_config: Path) -> None:
        application = build_configured_application(["nodify-test"], isolated_config)

        reloaded = build_configured_application(["nodify-test"], isolated_config)
        assert reloaded.settings.hotkey == application.settings.hotkey
        assert reloaded.settings.opacity == application.settings.opacity
        application.overlay.deleteLater()
        reloaded.overlay.deleteLater()

    def test_a_second_run_does_not_overwrite_an_edited_file(self, isolated_config: Path) -> None:
        from nodify.services.settings import save_settings

        save_settings(AppSettings(hotkey="Ctrl+Shift+K"), isolated_config)
        application = build_configured_application(["nodify-test"], isolated_config)
        assert application.settings.hotkey == "Ctrl+Shift+K"
        application.overlay.deleteLater()

    def test_a_malformed_file_still_starts(self, isolated_config: Path) -> None:
        """A stray comma must not leave the user with no application."""
        settings_path(isolated_config).parent.mkdir(parents=True, exist_ok=True)
        settings_path(isolated_config).write_text("{ broken", encoding="utf-8")

        application = build_configured_application(["nodify-test"], isolated_config)
        assert application.settings.hotkey == AppSettings().hotkey
        application.overlay.deleteLater()


class TestVisibility:
    def test_toggle_shows_and_hides(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        assert not subject.is_visible()
        subject.toggle()
        assert subject.is_visible()
        subject.toggle()
        assert not subject.is_visible()
        overlay.deleteLater()

    def test_show_applies_the_saved_click_through(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings(click_through=False))
        subject.show()
        assert overlay.click_through is False
        overlay.deleteLater()

    def test_set_click_through_is_remembered(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())
        subject.set_click_through(False)
        assert subject.settings.click_through is False
        assert overlay.click_through is False
        overlay.deleteLater()

    def test_a_failed_hotkey_is_reported_not_fatal(self, qapp: object) -> None:
        from nodify.services.hotkey import HotkeyService, RecordingRegistrar

        _app, overlay = build_application(["nodify-test"])
        messages: list[str] = []
        overlay.status_message.connect(messages.append)

        registrar = RecordingRegistrar()
        registrar.refuse.add(AppSettings().hotkey)
        hotkey = HotkeyService(registrar.register, registrar.unregister)
        subject = Application(_app, overlay, AppSettings(), hotkey=hotkey)

        assert not subject.start()
        assert messages
        # The message names the combination that failed, so the user knows which
        # key to try something else for.
        assert "Ctrl+Space" in messages[-1]
        overlay.deleteLater()

    def test_a_successful_start_registers_the_hotkey(self, qapp: object) -> None:
        from nodify.services.hotkey import HotkeyService, RecordingRegistrar

        _app, overlay = build_application(["nodify-test"])
        registrar = RecordingRegistrar()
        hotkey = HotkeyService(registrar.register, registrar.unregister)
        subject = Application(_app, overlay, AppSettings(), hotkey=hotkey)

        assert subject.start()
        assert registrar.registered == ["Ctrl+Space"]
        subject.quit()
        overlay.deleteLater()

    def test_quit_releases_the_hotkey(self, qapp: object) -> None:
        from nodify.services.hotkey import HotkeyService, RecordingRegistrar

        app, overlay = build_application(["nodify-test"])
        registrar = RecordingRegistrar()
        hotkey = HotkeyService(registrar.register, registrar.unregister)
        subject = Application(app, overlay, AppSettings(), hotkey=hotkey)
        subject.start()
        subject.quit()
        assert not hotkey.is_registered
        overlay.deleteLater()

    def test_starting_twice_registers_once(self, qapp: object) -> None:
        from nodify.services.hotkey import HotkeyService, RecordingRegistrar

        _app, overlay = build_application(["nodify-test"])
        registrar = RecordingRegistrar()
        hotkey = HotkeyService(registrar.register, registrar.unregister)
        subject = Application(_app, overlay, AppSettings(), hotkey=hotkey)

        subject.start()
        subject.start()
        assert registrar.registered == ["Ctrl+Space"]
        overlay.deleteLater()

    def test_the_settings_button_reports_rather_than_doing_nothing(self, qapp: object) -> None:
        """A button that silently does nothing looks like a broken application."""
        _app, overlay = build_application(["nodify-test"])
        messages: list[str] = []
        overlay.status_message.connect(messages.append)
        subject = Application(_app, overlay, AppSettings())

        subject.open_settings()
        assert messages
        overlay.deleteLater()

    def test_the_tile_area_is_exposed_for_the_layout(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())
        area = subject.tile_area()
        assert area.width > 0
        assert area.height > 0
        overlay.deleteLater()


class TestShowMountsTheDashboard:
    """Showing the overlay must put the panels on screen.

    ``Application.show()`` is what a user sees when they press the hotkey, and for
    the whole of Package J it mounted nothing: the overlay appeared with an empty
    rectangle in it. These tests drive the real ``show()`` rather than a dashboard
    method, because the wiring between the two is exactly what was missing.
    """

    @pytest.fixture
    def subject(self, qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Application:
        from nodify.adapters.vault import Vault, create
        from nodify.app.dashboard import Dashboard, Services

        monkeypatch.setenv("NODIFY_CONFIG_DIR", str(tmp_path / "config"))
        root = tmp_path / "vault"
        create(root, initial_year_month="2026-09")
        vault = Vault(root)
        vault.open()

        _app, overlay = build_application(["nodify-test"])
        dashboard = Dashboard(Services(vault=vault), AppSettings())
        application = Application(_app, overlay, AppSettings(), dashboard=dashboard)
        yield application
        application.dashboard.shutdown()

    def test_showing_mounts_every_panel_into_the_overlay(
        self, subject: Application, qapp: object
    ) -> None:
        assert not subject.dashboard.is_mounted

        subject.show()
        qapp.processEvents()

        assert subject.dashboard.is_mounted
        for tile, frame in subject.dashboard.frames.items():
            assert frame.parentWidget() is subject.overlay, f"{tile} is not in the overlay"

    def test_the_panels_are_actually_visible_after_showing(
        self, subject: Application, qapp: object
    ) -> None:
        subject.show()
        qapp.processEvents()

        for tile, frame in subject.dashboard.frames.items():
            assert frame.isVisible(), f"{tile} is not visible"
            assert frame.width() > 0 and frame.height() > 0, f"{tile} has no area"
            assert frame.content.isVisible(), f"the panel inside {tile} is hidden"

    def test_tiles_land_inside_the_overlay(self, subject: Application, qapp: object) -> None:
        """A tile placed outside the window is invisible however correct it is."""
        subject.show()
        qapp.processEvents()

        area = subject.tile_area()
        for tile in subject.dashboard.frames:
            rect = subject.dashboard.geometry_for(tile)
            assert area.contains_point(rect.x, rect.y), f"{tile} is outside the overlay"
            assert rect.right <= area.right, f"{tile} overflows the overlay"

    def test_hiding_and_showing_again_keeps_the_panels(
        self, subject: Application, qapp: object
    ) -> None:
        """The overlay is toggled constantly; each toggle must not lose the panels."""
        subject.show()
        subject.hide()
        subject.show()
        qapp.processEvents()

        assert len(subject.dashboard.frames) == 4
        for tile, frame in subject.dashboard.frames.items():
            assert frame.isVisible(), f"{tile} disappeared after a toggle"

    def test_a_dashboard_free_application_still_shows(self, qapp: object) -> None:
        """No vault yet must not stop the overlay appearing."""
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        subject.show()
        assert subject.is_visible()
        assert subject.dashboard is None
        overlay.deleteLater()

    def test_quit_releases_the_dashboard(self, subject: Application, qapp: object) -> None:
        """Otherwise the watcher thread outlives the process."""
        subject.show()
        assert subject.dashboard.is_mounted

        subject.quit()

        assert not subject.dashboard.is_mounted

    def test_resizing_the_overlay_repositions_the_tiles(
        self, subject: Application, qapp: object
    ) -> None:
        """The saved rectangles are absolute, so a resolution change strands them."""
        subject.show()
        qapp.processEvents()

        subject.overlay.resize(900, 600)
        qapp.processEvents()

        area = subject.tile_area()
        for tile in subject.dashboard.frames:
            rect = subject.dashboard.geometry_for(tile)
            assert rect.right <= area.right, f"{tile} overflowed after the resize"
            assert rect.bottom <= area.bottom, f"{tile} overflowed after the resize"
