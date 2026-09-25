"""Button QSS generator for the HUD glass theme."""
from __future__ import annotations

from . import colors


def button_qss(
    outline: str,
    hover: str,
    text: str = colors.TEXT,
    padding: str = "8px 16px",
) -> str:
    """Return a complete QPushButton stylesheet block."""
    return f"""
    QPushButton {{
        color: {text};
        background-color: rgba(28, 28, 28, 90);
        border: 1px solid {outline};
        border-radius: 9px;
        padding: {padding};
        font-family: "{colors.FONT_FAMILY_HEADER}";
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QPushButton:hover {{
        background-color: {hover};
        border-color: {colors.PURPLE_GLOW};
    }}
    QPushButton:pressed {{
        background-color: rgba(200, 200, 200, 60);
    }}
    """
