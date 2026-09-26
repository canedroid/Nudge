"""Color palette and font constants for the monochrome HUD glass theme."""
from __future__ import annotations

from PyQt6.QtGui import QColor


def qcolor(hex_value: str, alpha: int = 255) -> QColor:
    color = QColor(hex_value)
    color.setAlpha(alpha)
    return color


# --- Palette (monochrome — no hue, pure grayscale) ---
PURPLE = "#c9c9c9"
PURPLE_SOFT = "#3b3b3b"
PURPLE_GLOW = "#ececec"
BG_GLASS = "#0e0e0e"
BG_GLASS_STRONG = "#1c1c1c"
TEXT = "#f5f5f5"
TEXT_DIM = "#a8a8a8"
DANGER = "#e0e0e0"
SUCCESS = "#9a9a9a"
WARNING = "#c0c0c0"

#: Alpha of the tint painted over the tiles, 0-255. This is the single place the
#: glass opacity is decided: the stylesheet and the tile's own paint both read it,
#: because two literals is how a tile ends up opaque in one code path and
#: translucent in the other.
#:
#: 140 is a deliberate middle. Lower and white body text loses contrast against a
#: bright window behind the overlay; higher and the desktop stops being visible
#: through the tiles, which is the entire point of the effect.
TILE_TINT_ALPHA = 140

# --- Typography ---
FONT_FAMILY = "Segoe UI"
FONT_FAMILY_HEADER = "Bahnschrift"
FONT_SCALE = 1.0  # global UI font scale (0.8 – 1.6); fonts helpers multiply by it
