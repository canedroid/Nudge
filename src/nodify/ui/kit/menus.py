"""QMenu QSS for the HUD glass theme."""
from __future__ import annotations

from . import colors


def menu_qss() -> str:
    """Return a complete QMenu stylesheet block."""
    return (
        f"QMenu {{ background-color: {colors.BG_GLASS_STRONG}; color: {colors.TEXT};"
        f" border: 1px solid {colors.PURPLE}; border-radius: 8px; padding: 4px; }}"
        f"QMenu::item {{ padding: 6px 20px; }}"
        f"QMenu::item:selected {{ background-color: rgba(220, 220, 220, 70); }}"
        f"QMenu::separator {{ height: 1px; background: rgba(220, 220, 220, 90); margin: 4px 6px; }}"
    )
