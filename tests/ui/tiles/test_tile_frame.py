"""Tests for the tile frame's window setup, painting and mouse handling.

The frame had no widget-level tests at all: :class:`TileLayout` was covered by 64
pure-geometry tests while the widget that turns a mouse drag into those geometry
calls was not covered once. That gap is exactly how dragging shipped doing
nothing, so the gesture path is exercised here with real events rather than by
calling the private methods a test would otherwise reach for.

Two properties are load-bearing and easy to lose:

**The frame is its own window.** The compositor can only blur behind a window, so
a frame parented to the overlay could never be glass. It also means the frame has
no parent to convert coordinates through, which is why the origin is remembered
explicitly.

**The fill is translucent.** It was an opaque palette colour, which made every
tile a flat black card and hid the blur behind it. The pixel test below is the
regression guard for that.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel

from nodify.ui.kit import colors
from nodify.ui.layout.tile_layout import Rect, TileId
from nodify.ui.tiles.tile_frame import HANDLE_HEIGHT, TileFrame
from nodify.ui.win_backdrop import Backdrop

ORIGIN = QPoint(100, 50)


@pytest.fixture
def frame(qapp: QApplication) -> Iterator[TileFrame]:
    widget = TileFrame(TileId.NOTES, QLabel("notes"), Backdrop.NONE)
    # Placed through the real path, which is how the dashboard does it.
    widget.apply_rect(Rect(200, 150, 400, 300))
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()


def _press(widget: TileFrame, point: QPoint, *, release: bool = True) -> None:
    """Send a press, and optionally a release, at a point in the widget."""
    widget.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(point),
            QPointF(widget.mapToGlobal(point)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    if release:
        widget.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(point),
                QPointF(widget.mapToGlobal(point)),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )


def _move(widget: TileFrame, point: QPoint) -> None:
    widget.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(point),
            QPointF(widget.mapToGlobal(point)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


class TestWindowSetup:
    def test_the_frame_is_its_own_window(self, frame: TileFrame) -> None:
        """A child widget cannot be blurred, so the frame must be top-level."""
        assert frame.isWindow()
        assert frame.parent() is None

    def test_the_frame_is_frameless_translucent_and_always_on_top(self, frame: TileFrame) -> None:
        flags = frame.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert frame.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def test_the_content_fills_the_area_below_the_handle(self, frame: TileFrame) -> None:
        assert frame.content.geometry() == QRect(
            0, HANDLE_HEIGHT, frame.width(), frame.height() - HANDLE_HEIGHT
        )

    def test_content_is_laid_out_even_when_sized_without_the_layout(
        self, qapp: QApplication
    ) -> None:
        """A window sized directly, then shown, must still host its panel properly.

        Setting the geometry of a window that was never shown emits no resize
        event, because nothing changed from Qt's point of view. Relying on
        ``resizeEvent`` alone left the panel at its default 640x480, which is the
        kind of thing that looks like a rendering bug and is not.
        """
        widget = TileFrame(TileId.FILES, QLabel("files"), Backdrop.NONE)
        widget.setGeometry(0, 0, 320, 240)
        try:
            widget.show()
            qapp.processEvents()

            assert widget.content.geometry() == QRect(0, HANDLE_HEIGHT, 320, 240 - HANDLE_HEIGHT)
        finally:
            widget.close()
            widget.deleteLater()

    def test_a_backdrop_is_accepted_or_declined_without_raising(self, qapp: QApplication) -> None:
        """Losing the blur must never be fatal: the tint alone still works.

        The compositor call can be refused, and a headless or unusual Windows
        build may not have the entry point at all. Either way the tiles have to
        end up on screen.
        """
        widget = TileFrame(TileId.FILES, QLabel("files"), Backdrop.ACRYLIC)
        widget.setGeometry(0, 0, 200, 200)
        widget.show()
        try:
            qapp.processEvents()
            assert widget.isVisible()
            assert widget.backdrop_mechanism is None or isinstance(widget.backdrop_mechanism, str)
        finally:
            widget.close()
            widget.deleteLater()


class TestPlacement:
    def test_apply_rect_offsets_by_the_overlay_origin(self, frame: TileFrame) -> None:
        """A top-level window is placed in screen coordinates, not overlay ones."""
        frame.apply_rect(Rect(10, 20, 300, 400), ORIGIN)
        assert frame.geometry() == QRect(110, 70, 300, 400)

    def test_the_origin_is_remembered_for_the_conversion_back(self, frame: TileFrame) -> None:
        """The frame has no parent to ask, so the offset has to survive."""
        frame.apply_rect(Rect(0, 0, 300, 400), ORIGIN)
        assert frame._to_overlay(QPoint(150, 250)) == Rect(50, 200, 0, 0)

    def test_a_dropped_rect_is_reported_in_overlay_coordinates(self, frame: TileFrame) -> None:
        """Emitting global coordinates would shift a tile by the screen position."""
        frame.apply_rect(Rect(5, 6, 300, 400), ORIGIN)
        assert frame._current_rect() == Rect(5, 6, 300, 400)


class TestDragging:
    def test_pressing_the_handle_starts_a_drag(self, frame: TileFrame) -> None:
        started: list[TileId] = []
        frame.drag_started.connect(started.append)

        _press(frame, QPoint(50, 5), release=False)

        assert started == [TileId.NOTES]

    def test_pressing_the_panel_does_not_start_a_drag(self, frame: TileFrame) -> None:
        """A click in the notes editor must reach the editor, not move the tile."""
        started: list[TileId] = []
        frame.drag_started.connect(started.append)

        _press(frame, QPoint(50, HANDLE_HEIGHT + 20), release=False)

        assert started == []

    def test_dragging_reports_a_zero_area_rect(self, frame: TileFrame) -> None:
        """The host tells a move from a resize by the area, so a move is empty."""
        frame.apply_rect(Rect(0, 0, 400, 300), ORIGIN)
        moving: list[Rect] = []
        frame.tile_moving.connect(lambda tile, rect: moving.append(rect))

        _press(frame, QPoint(50, 5), release=False)
        _move(frame, QPoint(90, 45))

        assert moving, "the drag produced no geometry"
        assert moving[-1].width == 0
        assert moving[-1].height == 0

    def test_the_tile_stays_under_the_cursor(self, frame: TileFrame) -> None:
        """The grab offset is subtracted, so the frame does not jump to the pointer."""
        frame.apply_rect(Rect(0, 0, 400, 300), ORIGIN)
        moving: list[Rect] = []
        frame.tile_moving.connect(lambda tile, rect: moving.append(rect))

        _press(frame, QPoint(50, 5), release=False)
        _move(frame, QPoint(80, 25))

        # Pointer moved 30 right and 20 down from where it was grabbed, and the
        # proposed corner follows it by exactly that much.
        assert moving[-1] == Rect(30, 20, 0, 0)

    def test_releasing_reports_the_drop_and_finishes(self, frame: TileFrame) -> None:
        dropped: list[Rect] = []
        finished: list[TileId] = []
        frame.tile_dropped.connect(lambda tile, rect: dropped.append(rect))
        frame.drag_finished.connect(finished.append)
        frame.apply_rect(Rect(0, 0, 400, 300), ORIGIN)

        _press(frame, QPoint(50, 5), release=False)
        _move(frame, QPoint(90, 45))
        _press(frame, QPoint(90, 45), release=True)

        assert dropped and finished == [TileId.NOTES]
        assert dropped[-1].width == 400, "a drop must report the real size"

    def test_a_release_without_a_press_reports_nothing(self, frame: TileFrame) -> None:
        dropped: list[Rect] = []
        frame.tile_dropped.connect(lambda tile, rect: dropped.append(rect))

        _press(frame, QPoint(50, 5), release=False)
        _move(frame, QPoint(90, 45))
        frame._dragging = False  # the host saw the release already

        _press(frame, QPoint(90, 45), release=True)

        assert dropped == []


class TestResizing:
    def test_pressing_the_grip_starts_a_resize(self, frame: TileFrame) -> None:
        started: list[TileId] = []
        frame.drag_started.connect(started.append)
        corner = QPoint(frame.width() - 2, frame.height() - 2)

        _press(frame, corner, release=False)

        assert started == [TileId.NOTES]

    def test_resizing_reports_a_real_area(self, frame: TileFrame) -> None:
        """The host routes by area, so a resize must not be reported as a move."""
        moving: list[Rect] = []
        frame.tile_moving.connect(lambda tile, rect: moving.append(rect))
        corner = QPoint(frame.width() - 2, frame.height() - 2)

        _press(frame, corner, release=False)
        _move(frame, corner + QPoint(40, 30))

        assert moving
        assert moving[-1].width > 0
        assert moving[-1].height > 0

    def test_dragging_the_corner_past_the_origin_normalises(self, frame: TileFrame) -> None:
        """Dragging the bottom-right grip up and left must not produce a negative size."""
        moving: list[Rect] = []
        frame.tile_moving.connect(lambda tile, rect: moving.append(rect))
        corner = QPoint(frame.width() - 2, frame.height() - 2)

        _press(frame, corner, release=False)
        _move(frame, QPoint(5, 5))

        assert moving[-1].width > 0
        assert moving[-1].height > 0


class TestPainting:
    def test_the_fill_is_translucent_not_opaque(self, frame: TileFrame) -> None:
        """The regression that made every tile a flat black card.

        The fill used to be the opaque palette colour, which hid the compositor's
        blur and read as bland black. The exact alpha is asserted, because
        "translucent-ish" is not the contract: the tint has to match the token
        the stylesheet uses.
        """
        frame.show()
        QApplication.processEvents()
        image = frame.grab().toImage()

        # A point well inside the tile, below the handle and clear of the border.
        colour = image.pixelColor(frame.width() // 2, frame.height() // 2)

        assert colour.alpha() == colors.TILE_TINT_ALPHA
        assert colour.alpha() < 255, "the tile is opaque, so no blur can show through"

    def test_the_stylesheet_tint_matches_the_painted_one(self, qapp: QApplication) -> None:
        """One alpha for both paths, or a tile is glass in one and solid in the other."""
        from nodify.styles.app_qss import tile_qss

        qss = tile_qss()
        assert f"rgba(14, 14, 14, {colors.TILE_TINT_ALPHA})" in qss
        assert f"background: {colors.BG_GLASS};" not in qss

    def test_the_grip_only_highlights_under_the_pointer(self, frame: TileFrame) -> None:
        _move(frame, QPoint(5, 5))
        assert not frame._hover_grip

        _move(frame, QPoint(frame.width() - 2, frame.height() - 2))
        assert frame._hover_grip


class TestLifecycle:
    def test_closing_hides_rather_than_quits(self, frame: TileFrame) -> None:
        from PyQt6.QtGui import QCloseEvent

        event = QCloseEvent()
        frame.closeEvent(event)

        assert event.isAccepted() is False, "closing must not end the process"
