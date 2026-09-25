"""System tray icon + context menu — Observer-style reference.

Runtime-drawn icon (rounded glass plate with a status glyph). The context menu
jumps straight to any screen and provides Quit; left-click restores the window.
Wire ``TrayIcon(window)`` to any window exposing ``navigate(key)``,
``show_centered()`` (or ``show``/``raise_``/``activateWindow``) and a
``_open_settings()`` handler.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from . import colors, fonts, menus


def make_icon() -> QIcon:
    """Runtime-drawn tray icon: gray rounded plate with the '◉' glyph."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setBrush(QColor(colors.PURPLE_SOFT))
    painter.setPen(QColor(colors.PURPLE_GLOW).lighter(140))
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)

    painter.setPen(QColor(colors.PURPLE_GLOW))
    font = fonts.header_font(30)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "◉")
    painter.end()
    return QIcon(pixmap)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, window):
        super().__init__(make_icon())
        self._window = window
        self.setToolTip("HUD — tracking")

        menu = QMenu()
        menu.setStyleSheet(menus.menu_qss())
        actions = [
            ("▦  Now", lambda: self._go("now")),
            ("▦  Today", lambda: self._go("today")),
            ("▥  History", lambda: self._go("history")),
            ("◈  Habits", lambda: self._go("habits")),
            ("⚠  Suggestions", lambda: self._go("suggestions")),
        ]
        for text, handler in actions:
            action = QAction(text, menu)
            action.triggered.connect(handler)
            menu.addAction(action)
        menu.addSeparator()
        settings_action = QAction("⚙  Settings", menu)
        settings_action.triggered.connect(lambda: self._window._open_settings())
        menu.addAction(settings_action)
        quit_action = QAction("✕  Quit", menu)
        quit_action.triggered.connect(lambda: self._close())
        menu.addAction(quit_action)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _close(self) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.instance().quit()

    def _go(self, screen: str) -> None:
        self._window.navigate(screen)
        self._show()

    def _show(self) -> None:
        if hasattr(self._window, "show_centered"):
            self._window.show_centered()
        else:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show()