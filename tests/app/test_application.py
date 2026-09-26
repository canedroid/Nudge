"""The application startup path, end to end.

The bug this exists to catch: the packaged application started, reached its event
loop, and produced no settings file. Nothing in the suite noticed, because every
test passed settings in explicitly or called the loader with defaults. The gap was
"the real entry point, run the real way".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtTest import QTest

from nodify.app.application import (
    Application,
    build_application,
    build_configured_application,
)
from nodify.services.settings import AppSettings, load_settings, settings_path


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
        overlay_origin = subject.overlay.mapToGlobal(QPoint(0, 0))
        for tile, frame in subject.dashboard.frames.items():
            assert frame.isWindow(), f"{tile} is not its own window"
            expected = subject.dashboard.geometry_for(tile)
            assert frame.geometry() == QRect(
                overlay_origin.x() + expected.x,
                overlay_origin.y() + expected.y,
                expected.width,
                expected.height,
            ), f"{tile} is not placed over the overlay"

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

    def test_hiding_takes_the_tiles_off_screen_too(
        self, subject: Application, qapp: object
    ) -> None:
        """The bug the signal was added for.

        The tiles became top-level windows so the compositor would blur them
        individually. That made "hide the overlay" insufficient: the header
        vanished but four tiles stayed on the desktop with no way to reach them,
        and the only thing that could have noticed was a human.
        """
        subject.show()
        qapp.processEvents()
        assert all(frame.isVisible() for frame in subject.dashboard.frames.values())

        subject.hide()
        qapp.processEvents()

        assert not subject.overlay.isVisible()
        for tile, frame in subject.dashboard.frames.items():
            assert not frame.isVisible(), f"{tile} was stranded on the desktop"

    def test_escape_hides_the_tiles_and_the_overlay(
        self, subject: Application, qapp: object
    ) -> None:
        subject.show()
        qapp.processEvents()

        QTest.keyClick(subject.overlay, Qt.Key.Key_Escape)
        qapp.processEvents()

        assert not subject.overlay.isVisible()
        for tile, frame in subject.dashboard.frames.items():
            assert not frame.isVisible(), f"escape stranded {tile}"

    def test_the_header_hide_button_hides_the_tiles_and_the_overlay(
        self, subject: Application, qapp: object
    ) -> None:
        subject.show()
        qapp.processEvents()

        subject.overlay.header.hide_button.click()
        qapp.processEvents()

        assert not subject.overlay.isVisible()
        for tile, frame in subject.dashboard.frames.items():
            assert not frame.isVisible(), f"the hide button stranded {tile}"

    def test_closing_the_overlay_hides_the_tiles_and_the_overlay(
        self, subject: Application, qapp: object
    ) -> None:
        subject.show()
        qapp.processEvents()

        subject.overlay.close()
        qapp.processEvents()

        assert not subject.overlay.isVisible()
        for tile, frame in subject.dashboard.frames.items():
            assert not frame.isVisible(), f"closing stranded {tile}"

    def test_the_toggle_never_leaves_a_tile_behind(
        self, subject: Application, qapp: object
    ) -> None:
        """The hotkey path, which is the one the user actually presses.

        Pressed twice it must return to a clean desktop, not to a desktop with
        half a dashboard on it.
        """
        for _ in range(3):
            subject.toggle()
            qapp.processEvents()
            assert subject.is_visible()
            assert all(frame.isVisible() for frame in subject.dashboard.frames.values())

            subject.toggle()
            qapp.processEvents()
            assert not subject.is_visible()
            for tile, frame in subject.dashboard.frames.items():
                assert not frame.isVisible(), f"the hotkey stranded {tile}"

    def test_hiding_a_dashboard_free_application_is_harmless(self, qapp: object) -> None:
        """No vault means no tiles, and that path must not raise."""
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        subject.show()
        subject.hide()
        subject.toggle()
        assert subject.is_visible()
        overlay.deleteLater()

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


class TestResolvingTheVault:
    """Choosing a vault, once, and remembering it.

    The entry point needs a vault before it can show anything useful, and it has
    to ask the user for one the first time. The dialog is injected so this can be
    driven without a person present: a real ``QFileDialog`` blocks the event loop,
    which is exactly the trap that made an earlier version of this hang the suite.
    """

    @pytest.fixture
    def vault(self, tmp_path: Path) -> Path:
        from nodify.adapters.vault import create

        root = tmp_path / "vault"
        create(root, initial_year_month="2026-09")
        return root

    @pytest.fixture
    def asked(self) -> list[str]:
        """Records which folder the application would have asked for."""
        return []

    def build(
        self,
        config_dir: Path,
        asked: list[str],
        answer: str,
        settings: AppSettings | None = None,
    ) -> Application:
        _app, overlay = build_application(["nodify-test"])

        def choose() -> str:
            asked.append(answer)
            return answer

        return Application(
            _app,
            overlay,
            settings or AppSettings(),
            choose_vault=choose,
            config_dir=config_dir,
        )

    def test_a_first_run_asks_for_a_folder(self, isolated_config: Path, asked: list[str]) -> None:
        subject = self.build(isolated_config, asked, "")

        subject.resolve_vault()

        assert asked, "the user was never asked which vault to use"

    def test_a_remembered_vault_is_not_asked_for_again(
        self, isolated_config: Path, vault: Path, asked: list[str], qapp: object
    ) -> None:
        subject = self.build(isolated_config, asked, "", AppSettings(vault_path=str(vault)))

        resolved = subject.resolve_vault()

        assert resolved == vault
        assert asked == [], "the dialog was shown despite a remembered vault"

    def test_the_chosen_vault_is_remembered_for_next_time(
        self, isolated_config: Path, vault: Path, asked: list[str]
    ) -> None:
        subject = self.build(isolated_config, asked, str(vault))

        subject.resolve_vault()

        assert subject.settings.vault_path == str(vault)
        # Read it back as JSON rather than searching the text: a Windows path is
        # full of backslashes, which JSON escapes, so a substring check would
        # fail on a file that is perfectly correct.
        written = json.loads(settings_path(isolated_config).read_text(encoding="utf-8"))
        assert written["vault_path"] == str(vault)

    def test_a_default_settings_object_knows_no_vault(self) -> None:
        """Guards the assertion above from passing for the wrong reason."""
        assert AppSettings().vault_path == ""

    def test_the_remembered_vault_survives_a_restart(
        self, isolated_config: Path, vault: Path, asked: list[str]
    ) -> None:
        first = self.build(isolated_config, asked, str(vault))
        first.resolve_vault()

        # A real restart re-reads the file rather than carrying the object over.
        reloaded = load_settings(isolated_config)
        second = self.build(isolated_config, asked, "", reloaded.settings)

        assert second.resolve_vault() == vault
        assert asked == [str(vault)], "the second run asked again"

    def test_cancelling_the_dialog_is_not_an_error(
        self, isolated_config: Path, asked: list[str]
    ) -> None:
        """A dialog the user dismissed by accident must not crash the application."""
        subject = self.build(isolated_config, asked, "")

        assert subject.resolve_vault() is None
        assert subject.settings.vault_path == "", "a cancelled choice must not be stored"

    def test_a_remembered_vault_that_has_gone_asks_again(
        self, isolated_config: Path, asked: list[str], tmp_path: Path
    ) -> None:
        """A vault on a drive that is no longer plugged in must not be fatal."""
        missing = tmp_path / "unplugged"
        subject = self.build(isolated_config, asked, "", AppSettings(vault_path=str(missing)))

        subject.resolve_vault()

        assert asked, "a vault that no longer exists should prompt for a new one"

    def test_a_folder_that_is_not_a_vault_is_reported(
        self, isolated_config: Path, tmp_path: Path
    ) -> None:
        """The user must be told why nothing appeared, not left guessing."""
        from PyQt6.sip import isdeleted

        empty = tmp_path / "not-a-vault"
        empty.mkdir()
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings(), config_dir=isolated_config)

        result = subject.build_dashboard(empty)

        assert result is None
        assert overlay.header.status.text(), "the failure was not shown to the user"
        assert not isdeleted(overlay)

    def test_a_valid_vault_builds_a_dashboard(self, isolated_config: Path, vault: Path) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings(), config_dir=isolated_config)

        dashboard = subject.build_dashboard(vault)

        assert dashboard is not None
        assert dashboard.services.vault.root == vault
        dashboard.shutdown()


class TestStatusMessages:
    """A problem the user cannot see looks like an application that did nothing."""

    def test_a_message_reaches_the_header(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        subject.report("something went wrong")

        assert overlay.header.status.text() == "something went wrong"
        overlay.show()
        assert overlay.header.status.isVisibleTo(overlay), "the message is not on screen"
        overlay.deleteLater()

    def test_the_hotkey_chip_shows_the_configured_combination(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        Application(_app, overlay, AppSettings(hotkey="Ctrl+Shift+K"))
        assert overlay.header.hotkey_chip.text() == "Ctrl+Shift+K"
        overlay.deleteLater()

    def test_emitting_status_message_shows_it(self, qapp: object) -> None:
        """The signal is connected, rather than emitted into the void."""
        _app, overlay = build_application(["nodify-test"])
        Application(_app, overlay, AppSettings())

        overlay.status_message.emit("from the signal")

        assert overlay.header.status.text() == "from the signal"
        overlay.deleteLater()

    def test_clearing_removes_the_message(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        subject.report("a problem")
        subject.report("")

        assert overlay.header.status.text() == ""
        overlay.deleteLater()

    def test_a_fatal_message_explains_itself(self, qapp: object, monkeypatch) -> None:
        """A startup failure the user cannot see looks like a crash.

        Found by running the packaged build against a folder that is not a vault:
        the process exited, and the reason had been written to a status label on
        an overlay that was never shown.
        """
        shown: list[str] = []

        class FakeBox:
            class Icon:  # noqa: N801 - matching Qt's spelling
                Warning = object()

            class StandardButton:  # noqa: N801
                Ok = object()

            def __init__(self, *args: object, **kwargs: object) -> None:
                shown.append(str(args[2] if len(args) > 2 else ""))

            def setStandardButtons(self, _buttons: object) -> None:  # noqa: N802
                return None

            def exec(self) -> int:
                return 0

        import PyQt6.QtWidgets as widgets

        monkeypatch.setattr(widgets, "QMessageBox", FakeBox)
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        subject.report_fatal("that folder is not a vault")

        assert shown, "no dialog was shown"
        assert "not a vault" in shown[0]
        overlay.deleteLater()


class TestRealNativeServices:
    """The application must use the working registrar, not the stand-in.

    ``HotkeyService`` falls back to ``RecordingRegistrar`` when no register
    function is supplied, which records the request and does nothing else. The
    application relied on that fallback, so Ctrl+Space did nothing in the running
    program while every test passed against an injected fake.
    """

    def test_the_default_hotkey_is_a_real_qt_registrar(self, qapp: object) -> None:
        from nodify.services.hotkey import QtShortcutRegistrar

        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        assert isinstance(subject.registrar, QtShortcutRegistrar)
        overlay.deleteLater()

    def test_one_registrar_serves_both_halves(self, qapp: object) -> None:
        """Two registrars would leave the system claim behind on exit."""
        from nodify.services.hotkey import QtShortcutRegistrar

        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        subject.start()
        assert subject.hotkey.is_registered
        subject.quit()
        assert not subject.hotkey.is_registered
        assert isinstance(subject.registrar, QtShortcutRegistrar)
        overlay.deleteLater()

    def test_starting_actually_registers_on_a_real_shortcut(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        assert subject.start(), subject.hotkey.last_error
        assert subject.registrar._shortcut is not None, "no Qt shortcut was created"
        subject.quit()
        assert subject.registrar._shortcut is None, "the shortcut was not released"
        overlay.deleteLater()

    def test_the_tray_is_shown_on_start_and_hidden_on_quit(self, qapp: object) -> None:
        _app, overlay = build_application(["nodify-test"])
        subject = Application(_app, overlay, AppSettings())

        subject.start()
        assert subject.tray.is_visible()
        subject.quit()
        assert not subject.tray.is_visible()
        overlay.deleteLater()

    def test_the_tray_still_appears_when_the_hotkey_fails(self, qapp: object) -> None:
        """A refused hotkey must not take the tray with it.

        The tray may be the only way left to reach the application, so losing it
        at the same time as the hotkey would leave no route back.
        """
        from nodify.services.hotkey import HotkeyService, RecordingRegistrar

        _app, overlay = build_application(["nodify-test"])
        registrar = RecordingRegistrar()
        registrar.refuse.add(AppSettings().hotkey)
        hotkey = HotkeyService(registrar.register, registrar.unregister)
        subject = Application(_app, overlay, AppSettings(), hotkey=hotkey)

        assert not subject.start()
        assert subject.tray.is_visible(), "the tray went away with the hotkey"
        assert overlay.header.status.text(), "the failure was not shown"
        overlay.deleteLater()

    def test_a_desktop_without_a_tray_still_starts(self, qapp: object) -> None:
        """A session with no system tray must not stop the application."""
        from nodify.services.platform import TrayService

        _app, overlay = build_application(["nodify-test"])
        # No icon supplied, which is what a tray-less desktop produces.
        subject = Application(_app, overlay, AppSettings(), tray=TrayService(application=_app))

        assert subject.start(), "a missing tray must not be fatal"
        assert subject.hotkey.is_registered
        subject.quit()
        overlay.deleteLater()
