"""Frameless HUD settings dialog — live app-window transparency scale.

The slider drives :func:`glass_ui_without_edges.opacity.set_opacity` so every
open window is re-tinted in real time. The ✕ button (top right) hides the
dialog; right-clicking the card does the same.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import colors, fonts, opacity, painting
from .glass import GlassShell

SETTINGS_WIDTH = 360
SETTINGS_HEIGHT = 232


class _SettingsCard(QWidget):
    """The glass card holding the live transparency slider."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(SETTINGS_WIDTH, SETTINGS_HEIGHT)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("SETTINGS", self)
        title.setFont(fonts.header_font(15))
        title.setStyleSheet(f"color: {colors.PURPLE_GLOW}; background: transparent;")
        sub = QLabel("╱ window transparency", self)
        sub.setFont(fonts.body_font(9))
        sub.setStyleSheet(f"color: {colors.TEXT_DIM}; background: transparent;")
        header.addWidget(title)
        header.addWidget(sub)
        header.addStretch(1)

        close_btn = QPushButton("✕", self)
        close_btn.setToolTip("Close settings")
        close_btn.setFixedWidth(30)
        close_btn.setStyleSheet(
            f"QPushButton {{ color: {colors.TEXT_DIM}; background: rgba(200, 200, 200, 18);"
            f" border: 1px solid rgba(230, 230, 230, 120); border-radius: 6px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: rgba(230, 230, 230, 30); }}"
        )
        close_btn.clicked.connect(self.dismiss)
        header.addWidget(close_btn)
        root.addLayout(header)

        self._value_label = QLabel(self)
        self._value_label.setFont(fonts.header_font(12))
        self._value_label.setStyleSheet(f"color: {colors.TEXT}; background: transparent;")
        root.addWidget(self._value_label)

        slider_row = QHBoxLayout()
        label_min = QLabel("ghost 10%", self)
        label_min.setFont(fonts.body_font(9))
        label_min.setStyleSheet(f"color: {colors.TEXT_DIM}; background: transparent;")
        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setRange(10, 100)
        slider.setValue(round(opacity.current_opacity() * 100))
        slider.setTickInterval(5)
        slider.setCursor(Qt.CursorShape.PointingHandCursor)
        slider.valueChanged.connect(self._on_slider)
        slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ background: rgba(200, 200, 200, 60);"
            f" height: 4px; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ background: {colors.PURPLE_GLOW};"
            f" width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}"
            f"QSlider::sub-page:horizontal {{ background: {colors.PURPLE}; border-radius: 2px; }}"
        )
        label_max = QLabel("solid 100%", self)
        label_max.setFont(fonts.body_font(9))
        label_max.setStyleSheet(f"color: {colors.TEXT_DIM}; background: transparent;")
        slider_row.addWidget(label_min)
        slider_row.addWidget(slider, stretch=1)
        slider_row.addWidget(label_max)
        root.addLayout(slider_row)
        self._slider = slider

        hint = QLabel("Applies instantly to every open window.", self)
        hint.setWordWrap(True)
        hint.setFont(fonts.body_font(9))
        hint.setStyleSheet(f"color: {colors.TEXT_DIM}; background: transparent;")
        root.addWidget(hint)
        root.addStretch(1)

        self._update_label()

    # -- painting ------------------------------------------------------------ #
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painting.paint_card_bg(painter, self.rect(), radius=14)

    # -- actions ------------------------------------------------------------- #
    def _on_slider(self, value: int) -> None:
        opacity.set_opacity(value / 100.0)
        self._update_label()

    def _update_label(self) -> None:
        percent = round(opacity.current_opacity() * 100)
        self._value_label.setText(f"TRANSPARENCY   {percent}%")

    def dismiss(self) -> None:
        self.window().hide()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.RightButton:
            self.dismiss()
        super().mousePressEvent(event)


class SettingsWindow(GlassShell):
    """Mounts the settings card inside a glass shell (no edge glow)."""

    def __init__(self):
        super().__init__(SETTINGS_WIDTH, SETTINGS_HEIGHT)
        self.mount(_SettingsCard())

    @property
    def _slider(self) -> QSlider:
        return self.card._slider

    @property
    def _value_label(self) -> QLabel:
        return self.card._value_label

    def dismiss(self) -> None:
        self.card.dismiss()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming) — hide, never destroy
        event.ignore()
        self.dismiss()