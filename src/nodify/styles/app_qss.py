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
