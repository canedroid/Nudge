"""Tests for the overlay window and its click-through behaviour.

These verify the *decisions* Nodify makes — which alpha is painted into the
gutter, that the window is configured frameless/translucent/always-on-top, and
that Escape and close hide rather than quit. Whether the Windows compositor honours
per-pixel alpha hit testing is a platform property and is verified manually; the
plan records that as a live check.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtGui import QCloseEvent, QKeyEvent, QMouseEvent, QResizeEvent
from PyQt6.QtWidgets import QApplication

from nodify.styles.app_qss import GUTTER_ALPHA_BLOCK, GUTTER_ALPHA_PASS_THROUGH
from nodify.ui.overlay import GUTTER, Overlay


@pytest.fixture
def overlay(qapp: object) -> Overlay:
    widget = Overlay()
    widget.resize(1600, 900)
    return widget


class TestWindowConfiguration:
    def test_window_is_frameless_and_always_on_top(self, overlay: Overlay) -> None:
        flags = overlay.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint

    def test_window_is_translucent(self, overlay: Overlay) -> None:
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def test_background_is_not_automatically_filled(self, overlay: Overlay) -> None:
        # An auto-filled background would paint every pixel and silently defeat
        # click-through before a single tile is drawn.
        assert not overlay.autoFillBackground()

    def test_header_placed_within_the_gutter(self, overlay: Overlay) -> None:
        assert overlay.header.x() == GUTTER
        assert overlay.header.y() == GUTTER
        assert overlay.header.width() <= overlay.width() - 2 * GUTTER


class TestClickThrough:
    def test_defaults_to_click_through(self, overlay: Overlay) -> None:
        assert overlay.click_through is True

    def test_gutter_is_fully_transparent_when_passing_through(self, overlay: Overlay) -> None:
        color = overlay._gutter_color()  # noqa: SLF001
        assert color.alpha() == GUTTER_ALPHA_PASS_THROUGH == 0

    def test_gutter_becomes_hit_testable_when_disabled(self, overlay: Overlay) -> None:
        overlay.set_click_through(False)
        color = overlay._gutter_color()  # noqa: SLF001
        # Alpha 1 is invisible to the eye but is not zero, so the layered window is
        # hit-tested there and the overlay keeps the click.
        assert color.alpha() == GUTTER_ALPHA_BLOCK == 1

    def test_disabling_click_through_is_observable(self, overlay: Overlay) -> None:
        overlay.set_click_through(False)
        assert overlay.click_through is False
        overlay.set_click_through(True)
        assert overlay.click_through is True

    def test_setting_the_same_mode_is_a_no_op(self, overlay: Overlay) -> None:
        overlay.set_click_through(True)
        assert overlay.click_through is True


class TestGeometry:
    def test_tile_area_sits_below_the_header_and_inside_the_gutter(self, overlay: Overlay) -> None:
        area = overlay.tile_area()
        assert area.x() == GUTTER
        assert area.y() > GUTTER
        assert area.width() == overlay.width() - 2 * GUTTER
        assert area.bottom() <= overlay.height() - GUTTER + 1

    def test_tile_area_is_never_negative_on_a_tiny_window(self, overlay: Overlay) -> None:
        overlay.resize(10, 10)
        area = overlay.tile_area()
        assert area.width() >= 0
        assert area.height() >= 0

    def test_header_follows_a_resize(self, overlay: Overlay) -> None:
        overlay.resize(1280, 720)
        overlay.resizeEvent(QResizeEvent(overlay.size(), QSize(0, 0)))
        assert overlay.header.width() == 1280 - 2 * GUTTER


class TestLifecycle:
    def test_escape_asks_the_owner_to_hide_rather_than_hiding_itself(
        self, overlay: Overlay
    ) -> None:
        """The overlay no longer decides what "hide" means.

        It is not the only window on screen any more: the tiles are independent
        top-level windows, because per-pixel blur within one window is not
        something the compositor offers. Hiding only the overlay would leave four
        tiles floating over the desktop with no header to close them, so the
        request is passed up to whoever owns the tiles.
        """
        overlay.show()
        asked: list[bool] = []
        overlay.hide_requested.connect(lambda: asked.append(True))

        overlay.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        )

        assert asked, "escape did not ask anyone to hide the dashboard"
        assert overlay.isVisible(), "the overlay hid itself and left the tiles behind"

    def test_close_asks_the_owner_to_hide_instead_of_ending_the_process(
        self, overlay: Overlay
    ) -> None:
        app = QApplication.instance()
        overlay.show()
        asked: list[bool] = []
        overlay.hide_requested.connect(lambda: asked.append(True))

        event = QCloseEvent()
        overlay.closeEvent(event)

        assert not event.isAccepted(), "closing must not end the process"
        assert asked, "closing did not ask anyone to hide the dashboard"
        # The application must still be alive to be shown again.
        assert QApplication.instance() is app

    def test_an_unrelated_key_is_left_alone(self, overlay: Overlay) -> None:
        """Only Escape means "hide"; everything else belongs to the panels."""
        overlay.show()
        asked: list[bool] = []
        overlay.hide_requested.connect(lambda: asked.append(True))

        overlay.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
        )

        assert not asked
        assert overlay.isVisible()

    def test_background_press_is_absorbed(self, overlay: Overlay) -> None:
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            overlay.rect().center().toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        overlay.mousePressEvent(event)
        assert event.isAccepted()
