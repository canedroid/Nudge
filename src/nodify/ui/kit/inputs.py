"""Input field QSS generator for QLineEdit, QDateEdit, QTimeEdit."""
from __future__ import annotations

from . import colors


def input_qss() -> str:
    """Return a complete QSS block for input fields."""
    return (
        f"QLineEdit, QDateEdit, QTimeEdit {{"
        f" color: {colors.TEXT}; background: rgba(28, 28, 28, 140);"
        f" border: 1px solid rgba(220, 220, 220, 160); border-radius: 8px; padding: 6px 10px; }}"
        f"QLineEdit:focus, QDateEdit:focus, QTimeEdit:focus {{ border: 1px solid {colors.PURPLE_GLOW}; }}"
        f"QDateEdit::drop-down, QTimeEdit::drop-down {{ border: none; }}"
        f"QDateEdit::down-arrow {{ image: none; width: 0; }}"
        f"QCalendarWidget QWidget {{ alternate-background-color: {colors.BG_GLASS}; }}"
        f"QCalendarWidget QAbstractItemView {{ background: {colors.BG_GLASS_STRONG};"
        f" color: {colors.TEXT}; selection-background-color: {colors.PURPLE_SOFT}; }}"
        f"QCalendarWidget QToolButton {{ color: {colors.PURPLE_GLOW};"
        f" background: transparent; padding: 4px; border-radius: 6px; }}"
    )
