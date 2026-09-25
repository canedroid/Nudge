"""Glow effect helper — QGraphicsDropShadowEffect wrapper."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget


def apply_glow(
    widget: "QWidget",
    color: str = "#ececec",
    blur: int = 42,
    alpha: int = 220,
    offset: int = 0,
) -> QGraphicsDropShadowEffect:
    """Apply a glowing outer shadow to a widget. Returns the effect object."""
    effect = QGraphicsDropShadowEffect(widget)
    glow_color = QColor(color)
    glow_color.setAlpha(alpha)
    effect.setColor(glow_color)
    effect.setBlurRadius(blur)
    effect.setOffset(offset, offset)
    widget.setGraphicsEffect(effect)
    return effect
