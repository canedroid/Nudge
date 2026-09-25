"""Live window opacity system — register windows, change opacity globally."""
from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

from . import colors

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

OPACITY_MIN = 0.10
OPACITY_MAX = 1.0

_LIVE_WINDOWS: weakref.WeakSet["QWidget"] = weakref.WeakSet()


def bind_opacity(widget: "QWidget") -> None:
    """Register a main window so its opacity updates live with the global setting."""
    _LIVE_WINDOWS.add(widget)
    widget.setWindowOpacity(current_opacity())


def current_opacity() -> float:
    return float(colors.WINDOW_OPACITY)


def set_opacity(value: float) -> float:
    """Apply a new opacity to the whole app: config + every live window."""
    value = min(OPACITY_MAX, max(OPACITY_MIN, float(value)))
    colors.WINDOW_OPACITY = value
    for widget in list(_LIVE_WINDOWS):
        try:
            widget.setWindowOpacity(value)
        except RuntimeError:
            pass
    return value
