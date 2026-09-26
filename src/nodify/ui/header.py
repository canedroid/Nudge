"""The compact overlay header.

Carries the brand, the current hotkey chip, and the Settings, Hide and Quit
actions described in PLAN.MD section 8. It occupies a fixed strip at the top of the
tile area and is the only chrome drawn by the shell.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from nodify.styles.app_qss import header_qss
from nodify.ui.kit import colors, fonts

HEADER_HEIGHT = 40


class OverlayHeader(QWidget):
    """Brand, hotkey chip, and the Settings/Hide/Quit actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("header")
        self.setFixedHeight(HEADER_HEIGHT)
        self.setStyleSheet(header_qss())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.brand = QLabel("NODIFY", self)
        self.brand.setObjectName("brand")
        self.brand.setFont(fonts.header_font(15))
        layout.addWidget(self.brand)
        layout.addSpacing(4)

        self.hotkey_chip = QLabel("Ctrl+Space", self)
        self.hotkey_chip.setObjectName("hotkeyChip")
        self.hotkey_chip.setFont(fonts.body_font(10))
        self.hotkey_chip.setToolTip("Global hotkey — toggles the overlay")
        layout.addWidget(self.hotkey_chip)

        # Messages the user has to see, such as a hotkey that could not be
        # registered or a folder that is not a vault. Given a place between the
        # brand and the actions, because an error with nowhere to go looks
        # identical to an application that silently did nothing.
        self.status = QLabel("", self)
        self.status.setObjectName("statusMessage")
        self.status.setFont(fonts.body_font(10))
        self.status.setStyleSheet(f"color: {colors.TEXT_DIM};")
        self.status.setVisible(False)
        layout.addWidget(self.status)

        layout.addStretch(1)

        self.settings_button = QPushButton("SETTINGS", self)
        self.settings_button.setToolTip("Settings")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.settings_button)

        self.hide_button = QPushButton("HIDE", self)
        self.hide_button.setToolTip("Hide the overlay (Esc)")
        self.hide_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide_button.setStyleSheet(
            f"QPushButton {{ color: {colors.TEXT}; background: rgba(200, 200, 200, 18);"
            f" border: 1px solid rgba(230, 230, 230, 120); border-radius: 6px;"
            f" padding: 5px 12px; font-family: '{colors.FONT_FAMILY_HEADER}';"
            f" font-size: 10px; font-weight: 600; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: rgba(230, 230, 230, 34); }}"
        )
        layout.addWidget(self.hide_button)

        self.quit_button = QPushButton("QUIT", self)
        self.quit_button.setToolTip("Quit Nodify")
        self.quit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quit_button.setStyleSheet(
            f"QPushButton {{ color: {colors.TEXT_DIM}; background: rgba(200, 200, 200, 10);"
            f" border: 1px solid rgba(230, 230, 90, 110); border-radius: 6px;"
            f" padding: 5px 12px; font-family: '{colors.FONT_FAMILY_HEADER}';"
            f" font-size: 10px; font-weight: 600; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: rgba(230, 230, 230, 28);"
            f" color: {colors.TEXT}; }}"
        )
        layout.addWidget(self.quit_button)

    def set_hotkey(self, accelerator: str) -> None:
        """Show the currently registered global hotkey in the chip."""
        self.hotkey_chip.setText(accelerator)

    def show_status(self, message: str) -> None:
        """Show a message, or clear it when given nothing.

        The overlay has nowhere else to put this, and a message the user cannot
        see is the same as not reporting the problem at all.
        """
        text = str(message or "").strip()
        self.status.setText(text)
        self.status.setVisible(bool(text))
