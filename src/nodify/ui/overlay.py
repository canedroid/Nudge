"""The fullscreen, always-on-top overlay window.

The overlay is a frameless, translucent, top-level widget that covers the whole
display. Everything between and around the tiles is left unpainted, which makes
those regions click-through: Qt renders this window with per-pixel alpha as a
layered window, and the platform hit-tests a layered window by its alpha, so an
alpha-zero region passes mouse input to whatever is beneath.

See PLAN.MD sections 8 and 8.2.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QResizeEvent,
)
from PyQt6.QtWidgets import QWidget

from nodify.styles.app_qss import GUTTER_ALPHA_BLOCK, GUTTER_ALPHA_PASS_THROUGH
from nodify.ui.header import HEADER_HEIGHT, OverlayHeader

#: Margin between the screen edge and the tile area.
GUTTER = 16

#: Width of the grab strip on each edge of a tile that starts a resize.
RESIZE_GRIP = 6

#: Alpha painted into the gutter to make the overlay ignore the mouse everywhere.
GUTTER_BLOCK_COLOR = QColor(0, 0, 0, GUTTER_ALPHA_BLOCK)
GUTTER_PASS_COLOR = QColor(0, 0, 0, GUTTER_ALPHA_PASS_THROUGH)


class Overlay(QWidget):
    """Frameless translucent always-on-top fullscreen overlay."""

    #: Emitted with a message worth showing the user, such as a hotkey that could
    #: not be registered. Collected by the application layer rather than painted
    #: here, because the overlay has no chrome to put a message in without
    #: disturbing the layout the user arranged.
    status_message = pyqtSignal(str)

    #: Emitted after the overlay's geometry changed, so a host can re-place the
    #: tiles. The tile rectangles are absolute, so a resolution change would
    #: otherwise leave them outside the new bounds.
    resized = pyqtSignal()

    #: Emitted instead of hiding the overlay, because the overlay is no longer the
    #: only window on screen. The tiles are independent top-level windows, so
    #: hiding just this one would leave four tiles floating over the desktop with
    #: no way to reach them. Whoever owns the tiles has to hide them too.
    hide_requested = pyqtSignal()

    def __init__(self, on_quit: Callable[[], None] | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setWindowTitle("Nodify")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # No background brush: the window must start fully transparent so that
        # click-through works before anything is painted.
        self.setAutoFillBackground(False)

        self._on_quit = on_quit
        self._click_through = True

        self.header = OverlayHeader(self)
        self._place_header()

    # ------------------------------------------------------------------ setup

    def show_status(self, message: str) -> None:
        """Put a message in the header and emit it for anyone listening."""
        self.header.show_status(message)
        self.status_message.emit(message)

    def _place_header(self) -> None:
        self.header.setGeometry(
            QRect(
                GUTTER,
                GUTTER,
                max(0, self.width() - 2 * GUTTER),
                HEADER_HEIGHT,
            )
        )

    def show_overlay(self) -> None:
        """Cover the screen under the cursor and raise above everything else."""
        screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self._place_header()
        self.show()
        self.raise_()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._place_header()
        self.resized.emit()

    # ----------------------------------------------------------- click-through

    @property
    def click_through(self) -> bool:
        """Whether clicks outside the tiles reach the windows underneath."""
        return self._click_through

    def set_click_through(self, enabled: bool) -> None:
        """Enable or disable click-through outside the tiles.

        Disabling it repaints the gutter at alpha 1 instead of alpha 0. The
        difference is not visible, but alpha 1 is hit-testable, so the overlay
        captures the mouse everywhere.
        """
        if enabled == self._click_through:
            return
        self._click_through = enabled
        self.update()

    def _gutter_color(self) -> QColor:
        return GUTTER_PASS_COLOR if self._click_through else GUTTER_BLOCK_COLOR

    # --------------------------------------------------------------- painting

    def paintEvent(self, event: object) -> None:  # noqa: N802 (Qt naming)
        """Paint only what must be interactive; leave the rest transparent.

        Painting a fully transparent rectangle is equivalent to not painting at
        all, so this is done explicitly to make the intent obvious and to keep a
        single place where the click-through behaviour is decided.

        There is deliberately no outline around the tile area. The tiles are
        separate top-level windows that float wherever the user put them, so a
        rectangle drawn around the whole area would cut a line across them, and
        because the overlay is always-on-top it would do so even when the tiles
        are stacked above it. Each tile draws its own border instead.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._gutter_color())

    # --------------------------------------------------------------- geometry

    def tile_area(self) -> QRect:
        """The region the four tiles occupy, below the header."""
        top = GUTTER + HEADER_HEIGHT + 8
        return QRect(
            GUTTER,
            top,
            max(0, self.width() - 2 * GUTTER),
            max(0, self.height() - top - GUTTER),
        )

    # ------------------------------------------------------------------ events

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 (Qt naming)
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self.hide_requested.emit()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 (Qt naming)
        # Absorb presses that land on the overlay background. In click-through mode
        # these never arrive; in blocking mode they would otherwise act on whatever
        # is beneath, which is not what the user asked for.
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802 (Qt naming)
        """Closing the window hides the whole dashboard instead of quitting.

        Routed through ``hide_requested`` for the same reason as Escape: the
        overlay is no longer the only visible window, so it must not decide on its
        own what "hide" means.
        """
        if event is not None:
            event.ignore()
        self.hide_requested.emit()

    def request_quit(self) -> None:
        """Actually end the process. Only the tray and the Quit button use this."""
        if self._on_quit is not None:
            self._on_quit()
