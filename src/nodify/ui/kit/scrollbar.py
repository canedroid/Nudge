"""List widget + scrollbar QSS for the HUD glass theme."""
from __future__ import annotations

from . import colors


def scrollbar_qss() -> str:
    """Return a complete QSS block for QListWidget + QScrollBar."""
    return f"""
    QListWidget {{
        background: transparent;
        border: 1px solid rgba(200, 200, 200, 90);
        border-radius: 10px;
        outline: none;
    }}
    QListWidget::item {{
        color: {colors.TEXT};
        border-bottom: 1px solid rgba(200, 200, 200, 40);
        padding: 8px;
    }}
    QListWidget::item:selected {{
        background-color: rgba(220, 220, 220, 70);
        color: {colors.TEXT};
    }}
    QScrollBar:vertical {{
        background: rgba(18, 18, 18, 120);
        width: 8px;
        margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {colors.PURPLE};
        min-height: 24px;
        border-radius: 4px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
