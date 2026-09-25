"""Shared card background painting — gradient, border, top accent line."""
from __future__ import annotations

from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen

from . import colors


def paint_card_bg(
    painter: QPainter,
    rect,
    radius: int = 16,
    border_color: str = colors.PURPLE_GLOW,
    border_width: float = 1.0,
    top_line_alpha: int = 170,
    top_line_inset: int = 20,
    top_line_width: float = 1.5,
) -> None:
    """Paint the standard HUD card background.

    Draws:
    1. Vertical gradient from #262626 (top) to #161616 (bottom)
    2. 1px rounded border in border_color
    3. A thin accent line near the top edge
    """
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = rect.adjusted(1, 1, -1, -1)

    # gradient fill
    gradient = QLinearGradient(0, 0, 0, r.height())
    gradient.setColorAt(0.0, QColor(38, 38, 38, 255))
    gradient.setColorAt(0.6, QColor(22, 22, 22, 255))
    gradient.setColorAt(1.0, QColor(16, 16, 16, 255))
    painter.setBrush(gradient)
    painter.setPen(QPen(QColor(border_color), border_width))
    painter.drawRoundedRect(r, radius, radius)

    # top accent line
    top_color = QColor(border_color)
    top_color.setAlpha(top_line_alpha)
    painter.setPen(QPen(top_color, top_line_width))
    painter.drawLine(
        r.left() + top_line_inset,
        r.top() + 2,
        r.right() - top_line_inset,
        r.top() + 2,
    )
