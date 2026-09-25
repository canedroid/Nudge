"""Theme facade — one import point for the whole HUD look.

Mirrors how the Observer app exposes the kit through a single ``theme``
module, so callers keep a stable import surface while the kit internals are
free to move.
"""

from __future__ import annotations

from . import colors, fonts, glow, opacity, painting
from .colors import (
    BG_GLASS,
    BG_GLASS_STRONG,
    DANGER,
    PURPLE,
    PURPLE_GLOW,
    PURPLE_SOFT,
    SUCCESS,
    TEXT,
    TEXT_DIM,
    WARNING,
)
from .fonts import body_font, font_families, header_font
from .glow import apply_glow

__all__ = [
    "BG_GLASS",
    "BG_GLASS_STRONG",
    "DANGER",
    "PURPLE",
    "PURPLE_GLOW",
    "PURPLE_SOFT",
    "SUCCESS",
    "TEXT",
    "TEXT_DIM",
    "WARNING",
    "apply_glow",
    "body_font",
    "colors",
    "font_families",
    "header_font",
    "opacity",
    "painting",
]