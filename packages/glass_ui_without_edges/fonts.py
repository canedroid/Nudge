"""Font factory helpers for the HUD glass theme."""
from __future__ import annotations

from PyQt6.QtGui import QFont, QFontDatabase

from . import colors


def _scaled(pixel_size: int) -> int:
    return max(1, round(pixel_size * colors.FONT_SCALE))


def header_font(pixel_size: int = 15, bold: bool = True) -> QFont:
    size = _scaled(pixel_size)
    font = QFont(colors.FONT_FAMILY_HEADER, size)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def body_font(pixel_size: int = 12, bold: bool = False) -> QFont:
    size = _scaled(pixel_size)
    font = QFont(colors.FONT_FAMILY, size)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return font


def font_families() -> tuple[str, str]:
    available = set(QFontDatabase.families())
    header = colors.FONT_FAMILY_HEADER if colors.FONT_FAMILY_HEADER in available else "Segoe UI"
    body = colors.FONT_FAMILY if colors.FONT_FAMILY in available else "Calibri"
    return header, body
