"""GlassShell — frameless translucent window base class (no edge glow).

Every HUD window is a thin GlassShell (top-level, translucent, opacity-bound)
that mounts its content as a child card. The shell is sized exactly to the
content — there is no transparent halo and no drop-shadow glow around the
edges.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from . import opacity as _opacity


class GlassShell(QWidget):
    """Frameless translucent window sized exactly = content.

    Subclasses build a content card and call :meth:`mount`; show_centered and
    Esc-to-hide live here. No glow, no halo margin — the window hugs the card.
    """

    def __init__(self, content_w: int, content_h: int, blur: int = 0, alpha: int = 0):
        # blur/alpha kept for API compatibility with the old glowing shell;
        # they are ignored — this shell has no edge glow.
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(content_w, content_h)
        _opacity.bind_opacity(self)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._mounted: QWidget | None = None

    def mount(self, card: QWidget, blur: int = 0, alpha: int = 0) -> None:
        """Add the card filling the shell (no glow is applied)."""
        self._layout.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        self._mounted = card

    @property
    def card(self) -> QWidget:
        return self._mounted

    def show_centered(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else self.geometry()
        x = geo.center().x() - self.width() // 2
        y = geo.center().y() - self.height() // 2
        # never let the window spill off the available desktop rect
        x = max(geo.left(), min(x, geo.right() - self.width() + 1))
        y = max(geo.top(), min(y, geo.bottom() - self.height() + 1))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)