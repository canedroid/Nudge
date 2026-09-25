"""Application styles.

QSS is held as Python string constants rather than loose ``.qss`` files, matching
the convention already used by the reference kit (``kit/buttons.py``,
``kit/inputs.py``, ``kit/menus.py``, ``kit/scrollbar.py``). Keeping the styles in
importable Python means they can be parameterised by a theme later without adding
a packaging resource step, and they are directly testable.
"""

from __future__ import annotations

from nodify.ui.kit import colors

# The click-through gutter is painted at this alpha when clicks should fall through
# to whatever is beneath the overlay. Zero is fully transparent, and a layered
# window is hit-tested by its alpha, so the platform passes the click down.
GUTTER_ALPHA_PASS_THROUGH = 0

# When click-through is disabled the gutter is painted at alpha 1. To the eye this
# is indistinguishable from fully transparent, but it is not hit-testable, so the
# overlay captures every click on screen. See PLAN.MD section 8.2.
GUTTER_ALPHA_BLOCK = 1


def header_qss() -> str:
    """Stylesheet for the compact overlay header."""
    return f"""
    QWidget#header {{
        background: transparent;
    }}
    QLabel#brand {{
        color: {colors.PURPLE_GLOW};
        background: transparent;
        font-family: "{colors.FONT_FAMILY_HEADER}";
        font-size: 15px;
        font-weight: 600;
        letter-spacing: 2px;
    }}
    QLabel#hotkeyChip {{
        color: {colors.TEXT_DIM};
        background: rgba(200, 200, 200, 18);
        border: 1px solid rgba(230, 230, 230, 110);
        border-radius: 6px;
        padding: 3px 8px;
        font-family: "{colors.FONT_FAMILY}";
        font-size: 10px;
        letter-spacing: 1px;
    }}
    QPushButton {{
        color: {colors.TEXT};
        background: rgba(200, 200, 200, 18);
        border: 1px solid rgba(230, 230, 230, 120);
        border-radius: 6px;
        padding: 5px 12px;
        font-family: "{colors.FONT_FAMILY_HEADER}";
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QPushButton:hover {{
        background: rgba(230, 230, 230, 34);
        border-color: {colors.PURPLE_GLOW};
    }}
    QPushButton:pressed {{
        background: rgba(230, 230, 230, 52);
    }}
    """


def tile_qss() -> str:
    """Shared stylesheet for a feature tile.

    Tiles are dark translucent cards on a transparent overlay, so every colour
    here is an rgba over whatever is behind. Opaque colours would punch a hole in
    the glass effect the overlay is built around.
    """
    return f"""
    QWidget#tile {{
        background: {colors.BG_GLASS};
        border: 1px solid rgba(230, 230, 230, 70);
        border-radius: 12px;
    }}
    QLabel#tileTitle {{
        color: {colors.PURPLE_GLOW};
        background: transparent;
        font-family: "{colors.FONT_FAMILY_HEADER}";
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 2px;
    }}
    QLabel#tileHint {{
        color: {colors.TEXT_DIM};
        background: transparent;
        font-family: "{colors.FONT_FAMILY}";
        font-size: 11px;
    }}
    QLabel#emptyState {{
        color: {colors.TEXT_DIM};
        background: transparent;
        font-family: "{colors.FONT_FAMILY}";
        font-size: 12px;
    }}
    """


def notes_qss() -> str:
    """Notes panel stylesheet: category rail, note list and the editor."""
    return f"""
    QWidget#categoryRail {{
        background: rgba(255, 255, 255, 8);
        border: 1px solid rgba(230, 230, 230, 40);
        border-radius: 8px;
    }}
    QListWidget {{
        background: transparent;
        border: none;
        outline: none;
        font-family: "{colors.FONT_FAMILY}";
        font-size: 12px;
        color: {colors.TEXT};
    }}
    QListWidget::item {{
        padding: 5px 6px;
        border-radius: 5px;
    }}
    QListWidget::item:selected {{
        background: rgba(230, 230, 230, 38);
        color: {colors.PURPLE_GLOW};
    }}
    QListWidget::item:hover {{
        background: rgba(230, 230, 230, 20);
    }}
    QLineEdit, QTextEdit, QPlainTextEdit {{
        background: rgba(255, 255, 255, 10);
        border: 1px solid rgba(230, 230, 230, 60);
        border-radius: 6px;
        padding: 6px;
        color: {colors.TEXT};
        font-family: "{colors.FONT_FAMILY}";
        font-size: 12px;
        selection-background-color: rgba(230, 230, 230, 90);
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border-color: {colors.PURPLE_GLOW};
    }}
    QPushButton#primary {{
        color: {colors.BG_GLASS};
        background: {colors.PURPLE_GLOW};
        border: 1px solid {colors.PURPLE_GLOW};
        border-radius: 6px;
        padding: 5px 12px;
        font-family: "{colors.FONT_FAMILY_HEADER}";
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QPushButton#primary:hover {{
        background: #ffffff;
    }}
    QPushButton#primary:disabled {{
        color: {colors.TEXT_DIM};
        background: rgba(230, 230, 230, 30);
        border-color: rgba(230, 230, 230, 40);
    }}
    QPushButton#danger {{
        color: {colors.TEXT_DIM};
        background: transparent;
        border: 1px solid rgba(230, 230, 230, 60);
        border-radius: 6px;
        padding: 5px 10px;
        font-family: "{colors.FONT_FAMILY}";
        font-size: 10px;
    }}
    QPushButton#danger:hover {{
        color: {colors.TEXT};
        border-color: {colors.PURPLE_GLOW};
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(230, 230, 230, 60);
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: rgba(230, 230, 230, 100);
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    """
