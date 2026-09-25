"""Application bootstrap.

Owns the ``QApplication`` lifecycle, the surface format, and the overlay window.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication

from nodify.ui.overlay import Overlay

ORGANISATION = "canedroid"
APPLICATION = "Nodify"


def configure_surface_format() -> None:
    """Give the window surface an alpha channel.

    This must run **before** ``QApplication`` is constructed. Qt 6 will otherwise
    silently fall back to a surface without an alpha channel, and
    ``WA_TranslucentBackground`` then renders an opaque black background — which
    also breaks click-through, because an opaque window is hit-testable
    everywhere. See PLAN.MD section 8.2.
    """
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setRedBufferSize(8)
    fmt.setGreenBufferSize(8)
    fmt.setBlueBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)


def build_application(argv: Sequence[str] | None = None) -> tuple[QApplication, Overlay]:
    """Create the application and its overlay, wired to each other."""
    configure_surface_format()
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName(APPLICATION)
    app.setOrganizationName(ORGANISATION)
    # The overlay hides rather than closes, so the process must outlive the last
    # visible window. Quitting is explicit, from the tray or the Quit button.
    app.setQuitOnLastWindowClosed(False)

    overlay = Overlay(on_quit=app.quit)
    overlay.header.settings_button.clicked.connect(lambda: None)
    overlay.header.hide_button.clicked.connect(overlay.hide)
    overlay.header.quit_button.clicked.connect(app.quit)
    return app, overlay


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python -m nodify``."""
    app, overlay = build_application(argv)
    overlay.show_overlay()
    return app.exec()
