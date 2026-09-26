"""A draggable, resizable tile window.

Each feature panel is hosted in one of these. The container owns the drag handle
and the resize grips, and forwards the resulting geometry to the shared
:class:`TileLayout`, so the layout rules stay in the pure module and this class
only translates mouse events.

The drag handle is the whole tile's title strip rather than a separate button: the
tiles sit in a row with no other chrome, and a dedicated handle would cost
horizontal space that the notes editor needs.

**Each frame is a top-level window, not a child of the overlay.** That is what
makes the glass possible. Windows composites a blur behind a *window*, not behind
a region of one, so a single fullscreen overlay can only ever blur the entire
screen at once, which would destroy the transparent gutter the click-through
depends on. One small window per tile blurs only that tile and leaves the rest of
the desktop untouched. See :mod:`nodify.ui.win_backdrop`.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QMouseEvent, QPainter, QResizeEvent, QShowEvent
from PyQt6.QtWidgets import QWidget

from nodify.ui.kit import colors
from nodify.ui.layout.tile_layout import Rect, TileId
from nodify.ui.win_backdrop import Backdrop
from nodify.ui.win_backdrop import apply as apply_backdrop

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

    def __init__(
        self,
        tile: TileId,
        content: QWidget,
        backdrop: Backdrop = Backdrop.ACRYLIC,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._tile = tile
        self._content = content
        self._backdrop = backdrop
        #: The global position of the host overlay's top-left corner. A top-level
        #: window is positioned in global coordinates while the layout engine
        #: works in overlay-relative ones, so the difference is remembered here
        #: rather than recomputed from a parent that no longer exists.
        self._origin = QPoint(0, 0)
        content.setParent(self)

        self._dragging = False
        self._resizing = False
        self._grab_offset = QPoint(0, 0)
        self._pointer = QPoint(0, 0)
        self._resize_origin = QPoint(0, 0)
        self._start_rect = QRect()
        self._hover_grip = False
        self._mechanism: str | None = None

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

    @property
    def tile(self) -> TileId:
        return self._tile

    @property
    def content(self) -> QWidget:
        return self._content

    @property
    def backdrop_mechanism(self) -> str | None:
        """Which compositor mechanism was accepted, or ``None`` for a plain tint.

        Recorded because "the blur silently does nothing" is otherwise invisible,
        and :mod:`nodify.backdrop_probe` exists to make the same question
        answerable for a whole machine.
        """
        return self._mechanism

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 (Qt naming)
        # The native handle a widget draws into does not exist until the window
        # is created, so this is the only point at which a backdrop can be
        # attached. Applying it in __init__ would be silently discarded.
        super().showEvent(event)
        # The compositor may resize a window when it first maps, and a frame
        # created at its final size never sees a resize event at all.
        self._relayout()
        self._mechanism = apply_backdrop(self, self._backdrop)
        if self._mechanism is not None and self._backdrop is not Backdrop.NONE:
            # The tint is painted by this widget; without it the blur alone would
            # leave text unreadable over a bright window.
            self.update()

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

    def apply_rect(self, rect: Rect, origin: QPoint | None = None) -> None:
        """Move and resize the frame to a layout rectangle.

        ``origin`` is the host overlay's top-left in global coordinates. It is
        remembered because a drag has to convert back the other way, and a
        top-level frame has no parent left to ask.
        """
        if origin is not None:
            self._origin = QPoint(origin)
        self.setGeometry(
            self._origin.x() + rect.x,
            self._origin.y() + rect.y,
            rect.width,
            rect.height,
        )
        # Laid out here as well as in resizeEvent. A window given its final
        # geometry before it is ever shown does not get a resize event, because
        # nothing changed, so the hosted panel would keep whatever size it had
        # and render at the wrong dimensions or not at all.
        self._relayout()

    def _relayout(self) -> None:
        self._content.setGeometry(self.content_rect())

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event: object) -> None:  # noqa: N802 (Qt naming)
        """Paint the tile background and the drag handle.

        The handle is a faint band rather than a labelled title: the panel inside
        already names itself, and a second title would waste the width.

        The fill is the translucent tint, not the opaque palette colour. Painted
        opaque it would hide the compositor's blur behind it and the tile would
        read as the flat black card this replaced.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors.qcolor(colors.BG_GLASS, colors.TILE_TINT_ALPHA))
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
        self.tile_moving.emit(self._tile, self._to_overlay(corner))

    def _perform_resize(self, point: QPoint) -> None:
        """Resize the frame from its original extent to the current corner."""
        corner = self.mapToGlobal(point)
        start_left, start_top = self._start_rect.left(), self._start_rect.top()
        # Which corner is being dragged decides which edges move; normalise
        # handles the case where the corner is dragged past the opposite one.
        anchor_x, anchor_y = min(start_left, corner.x()), min(start_top, corner.y())
        proposed = self._to_overlay(QPoint(anchor_x, anchor_y))
        self.tile_moving.emit(
            self._tile,
            Rect(
                x=proposed.x,
                y=proposed.y,
                width=abs(corner.x() - start_left),
                height=abs(corner.y() - start_top),
            ),
        )

    def _to_overlay(self, global_point: QPoint) -> Rect:
        """A zero-sized rectangle at a global point, in overlay coordinates.

        The layout engine works in overlay space while Qt hands out global
        coordinates, so the conversion lives in exactly one place. The offset is
        the remembered origin rather than a parent's position, because a
        top-level frame has no parent to ask.
        """
        return Rect(
            global_point.x() - self._origin.x(),
            global_point.y() - self._origin.y(),
            0,
            0,
        )

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
        """The frame's own extent, in overlay coordinates.

        ``x()``/``y()`` are global for a top-level window, so the origin comes off
        again here; emitting them raw would shift a dropped tile by the overlay's
        screen position every time the overlay moved to another monitor.
        """
        return Rect(
            self.x() - self._origin.x(),
            self.y() - self._origin.y(),
            self.width(),
            self.height(),
        )

    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802 (Qt naming)
        """Closing a tile hides it; the process keeps running."""
        if event is not None:
            event.ignore()
        self.hide()


__all__ = ["GRIP_SIZE", "HANDLE_HEIGHT", "TileFrame"]
