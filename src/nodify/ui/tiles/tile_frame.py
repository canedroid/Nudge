"""A draggable, resizable tile window.

Each feature panel is hosted in one of these. The container owns the drag handle
and the resize grips, and forwards the resulting geometry to the shared
:class:`TileLayout`, so the layout rules stay in the pure module and this class
only translates mouse events.

The drag handle is the whole tile's title strip rather than a separate button: the
tiles sit in a row with no other chrome, and a dedicated handle would cost
horizontal space that the notes editor needs.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QMouseEvent, QPainter, QResizeEvent
from PyQt6.QtWidgets import QWidget

from nodify.ui.kit import colors
from nodify.ui.layout.tile_layout import Rect, TileId

#: Height of the drag strip at the top of a tile.
HANDLE_HEIGHT = 26

#: Width of the resize grip on the bottom-right corner.
GRIP_SIZE = 12


class TileFrame(QWidget):
    """Hosts one feature panel with a drag handle and a resize grip."""

    #: Emitted with the tile and its proposed rectangle on a completed drag.
    tile_dropped = pyqtSignal(object, object)
    #: Emitted continuously during a drag, for live repositioning.
    tile_moving = pyqtSignal(object, object)
    #: Emitted when the user is about to start dragging, so a host can pause.
    drag_started = pyqtSignal(object)
    drag_finished = pyqtSignal(object)

    def __init__(self, tile: TileId, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tile = tile
        self._content = content
        content.setParent(self)

        self._dragging = False
        self._resizing = False
        self._grab_offset = QPoint(0, 0)
        self._pointer = QPoint(0, 0)
        self._resize_origin = QPoint(0, 0)
        self._start_rect = QRect()
        self._hover_grip = False

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

    @property
    def tile(self) -> TileId:
        return self._tile

    @property
    def content(self) -> QWidget:
        return self._content

    def handle_rect(self) -> QRect:
        """The drag strip along the top of the tile."""
        return QRect(0, 0, self.width(), HANDLE_HEIGHT)

    def grip_rect(self) -> QRect:
        """The resize grip in the bottom-right corner."""
        return QRect(
            self.width() - GRIP_SIZE,
            self.height() - GRIP_SIZE,
            GRIP_SIZE,
            GRIP_SIZE,
        )

    def content_rect(self) -> QRect:
        """Where the hosted panel lives, below the handle."""
        return QRect(0, HANDLE_HEIGHT, self.width(), max(0, self.height() - HANDLE_HEIGHT))

    def apply_rect(self, rect: Rect) -> None:
        """Move and resize the frame to a layout rectangle."""
        self.setGeometry(rect.x, rect.y, rect.width, rect.height)

    def _relayout(self) -> None:
        self._content.setGeometry(self.content_rect())

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event: object) -> None:  # noqa: N802 (Qt naming)
        """Paint the tile background and the drag handle.

        The handle is a faint band rather than a labelled title: the panel inside
        already names itself, and a second title would waste the width.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors.qcolor(colors.BG_GLASS))
        painter.drawRoundedRect(self.rect(), 12, 12)

        painter.setBrush(colors.qcolor(colors.PURPLE, 24))
        painter.drawRoundedRect(self.handle_rect().adjusted(0, 0, -1, 1), 12, 12)
        painter.fillRect(
            QRect(0, HANDLE_HEIGHT - 1, self.width(), 1),
            colors.qcolor(colors.PURPLE, 70),
        )

        if self._hover_grip:
            painter.setBrush(colors.qcolor(colors.PURPLE_GLOW, 120))
            painter.drawRect(self.grip_rect())

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 (Qt naming)
        if event is None:
            return
        point = event.position().toPoint()

        if event.button() == Qt.MouseButton.LeftButton and self.grip_rect().contains(point):
            self._resizing = True
            self._resize_origin = self.mapToGlobal(point)
            self._start_rect = QRect(self.geometry())
            self.drag_started.emit(self._tile)
            event.accept()
            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.handle_rect().contains(point)
            and not self._is_on_content(point)
        ):
            self._dragging = True
            self._grab_offset = point
            self.drag_started.emit(self._tile)
            event.accept()
            return

        super().mousePressEvent(event)

    def _is_on_content(self, point: QPoint) -> bool:
        """Whether a press landed on the hosted panel rather than the handle.

        Presses inside the panel belong to the panel. Without this, a click on the
        notes editor would start a drag.
        """
        return not self.handle_rect().contains(point)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 (Qt naming)
        if event is None:
            return
        point = event.position().toPoint()
        self._pointer = point

        if self._resizing:
            self._perform_resize(point)
            return
        if self._dragging:
            self._perform_drag()
            return

        # Show the resize affordance only when the pointer is over the grip.
        over = self.grip_rect().contains(point)
        if over != self._hover_grip:
            self._hover_grip = over
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if over else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def _perform_drag(self) -> None:
        """Move the frame, emitting the proposed rectangle in parent coordinates.

        The grab offset is subtracted from the live pointer position, so the frame
        stays exactly under the cursor without accumulating rounding.
        """
        corner = self.mapToGlobal(self._pointer) - self._grab_offset
        self.tile_moving.emit(self._tile, self._to_parent(corner))

    def _perform_resize(self, point: QPoint) -> None:
        """Resize the frame from its original extent to the current corner."""
        corner = self.mapToGlobal(point)
        start_left, start_top = self._start_rect.left(), self._start_rect.top()
        # Which corner is being dragged decides which edges move; normalise
        # handles the case where the corner is dragged past the opposite one.
        anchor_x, anchor_y = min(start_left, corner.x()), min(start_top, corner.y())
        proposed = self._to_parent(QPoint(anchor_x, anchor_y))
        self.tile_moving.emit(
            self._tile,
            Rect(
                x=proposed.x,
                y=proposed.y,
                width=abs(corner.x() - start_left),
                height=abs(corner.y() - start_top),
            ),
        )

    def _to_parent(self, global_point: QPoint) -> Rect:
        """A zero-sized rectangle at a global point, expressed in parent coordinates.

        The layout engine wants tile-space coordinates, while Qt hands out global
        ones, so the conversion lives in exactly one place.
        """
        parent = self.parentWidget()
        offset_x = parent.x() if parent is not None else 0
        offset_y = parent.y() if parent is not None else 0
        return Rect(global_point.x() - offset_x, global_point.y() - offset_y, 0, 0)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 (Qt naming)
        if event is None:
            return
        was_active = self._dragging or self._resizing
        self._dragging = False
        self._resizing = False
        if was_active:
            self.tile_dropped.emit(self._tile, self._current_rect())
            self.drag_finished.emit(self._tile)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _current_rect(self) -> Rect:
        """The frame's own position, which is already parent-relative."""
        return Rect(self.x(), self.y(), self.width(), self.height())

    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802 (Qt naming)
        """Closing a tile hides it; the process keeps running."""
        if event is not None:
            event.ignore()
        self.hide()


__all__ = ["GRIP_SIZE", "HANDLE_HEIGHT", "TileFrame"]
