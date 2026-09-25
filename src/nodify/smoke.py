"""Live smoke check: launch the real overlay on the Windows platform plugin.

Run with ``python -m nodify.smoke``. Exits by itself after a short delay so it can
be used to confirm the window appears, is transparent, and honours click-through
without leaving a process behind.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QGuiApplication

from nodify.app.application import build_application


def main() -> int:
    app, overlay = build_application([])
    print(f"platform: {QGuiApplication.platformName()}")
    overlay.show_overlay()
    print(f"visible: {overlay.isVisible()}  size: {overlay.width()}x{overlay.height()}")
    print(f"tile area: {overlay.tile_area()}")
    print(f"click-through: {overlay.click_through}")

    image = overlay.grab().toImage()
    gutter = image.pixelColor(2, overlay.height() - 4)
    print(
        f"gutter pixel: rgba({gutter.red()}, {gutter.green()}, {gutter.blue()}, {gutter.alpha()})"
    )

    overlay.set_click_through(False)
    overlay.repaint()
    blocked = overlay.grab().toImage().pixelColor(2, overlay.height() - 4)
    print(f"blocked pixel alpha: {blocked.alpha()}")

    QTimer.singleShot(2500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
