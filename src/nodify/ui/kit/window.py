"""HudWindow — the Observer-style main HUD window (no edge glow).

Frameless + translucent; a gradient-painted card holds a sidebar
(Now / Today / History / Habits / Suggestions) and a QStackedWidget.
Drag from any non-interactive space, Esc hides.

The window drives a tiny presenter contract instead of concrete models:

    context.snapshot()               -> dict        (Now screen)
    context.day()                    -> dict        (Today screen)
    context.period(days)             -> dict        (History screen)
    context.habits()                 -> list[dict]  (Habits screen)
    context.suggestions()            -> list       (Suggestion screen)
    context.quit()                   -> None        (tray Quit action)

Return whatever your app has; the screens only read the plain keys documented
in :mod:`glass_ui_without_edges.screens`.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import colors, fonts, opacity, painting
from . import screens

SCREENS = ("now", "today", "history", "habits", "suggestions")
SCREEN_TITLES = ("Now", "Today", "History", "Habits", "Suggestions")

WINDOW_W, WINDOW_H = 760, 560
HALO = 0  # no edge glow — the window is exactly the card size


def _nav_qss(active: bool = False) -> str:
    border = f"1px solid {colors.PURPLE_GLOW}" if active else "1px solid rgba(200, 200, 200, 90)"
    bg = "rgba(220, 220, 220, 40)" if active else "rgba(28, 28, 28, 90)"
    return f"""
    QPushButton {{
        color: {colors.TEXT};
        background-color: {bg};
        border: {border};
        border-radius: 8px;
        padding: 9px 14px;
        text-align: left;
        font-family: "{colors.FONT_FAMILY_HEADER}";
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QPushButton:hover {{ background-color: rgba(200, 200, 200, 30); }}
    """


class _PaintedCard(QWidget):
    """The gradient-painted glass card."""

    def __init__(self, w: int, h: int):
        super().__init__()
        self.setFixedSize(w, h)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painting.paint_card_bg(painter, self.rect(), radius=16)


class HudWindow(QWidget):
    def __init__(self, context):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._context = context
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("HUD")
        self.setFixedSize(WINDOW_W, WINDOW_H)
        opacity.bind_opacity(self)
        self._drag_offset: QPoint | None = None
        self._settings = None

        self.card = _PaintedCard(WINDOW_W, WINDOW_H)

        self._close_btn = QPushButton("✕", self.card)
        self._close_btn.setObjectName("closeButton")
        self._close_btn.setToolTip("Hide")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setGeometry(WINDOW_W - 48, 12, 32, 28)
        self._close_btn.setStyleSheet(
            f"QPushButton {{ color: {colors.TEXT_DIM}; background: rgba(200, 200, 200, 18);"
            f" border: 1px solid rgba(230, 230, 230, 120); border-radius: 6px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: rgba(230, 230, 230, 30); }}"
        )
        self._close_btn.clicked.connect(self.hide)

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 14, 0)
        card_layout.setSpacing(0)

        # sidebar
        self.sidebar = QWidget(self.card)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(18, 18, 10, 18)
        sidebar_layout.setSpacing(8)
        self.sidebar.setFixedWidth(172)

        brand = QLabel("HUD", self.sidebar)
        brand.setFont(fonts.header_font(17))
        brand.setStyleSheet(f"color: {colors.PURPLE_GLOW}; background: transparent;")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addSpacing(6)

        self._nav_buttons: dict[str, QPushButton] = {}
        for key, title in zip(SCREENS, SCREEN_TITLES):
            btn = QPushButton(title, self.sidebar)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
            sidebar_layout.addWidget(btn)
            self._nav_buttons[key] = btn
        sidebar_layout.addStretch(1)

        settings_btn = QPushButton("⚙  SETTINGS", self.sidebar)
        settings_btn.setToolTip("Settings — transparency")
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_nav_qss(False))
        settings_btn.clicked.connect(self._open_settings)
        sidebar_layout.addWidget(settings_btn)

        drag = QLabel("⠿", self.sidebar)
        drag.setStyleSheet(f"color: {colors.TEXT_DIM}; background: transparent; font-size: 13px;")
        sidebar_layout.addWidget(drag)

        # screens
        self.stack = QStackedWidget(self.card)
        self.screens: dict[str, QWidget] = {
            "now": screens.NowScreen(),
            "today": screens.TodayScreen(),
            "history": screens.HistoryScreen(),
            "habits": screens.HabitsScreen(),
            "suggestions": screens.SuggestionsScreen(),
        }
        for key in SCREENS:
            self.stack.addWidget(self.screens[key])

        card_layout.addWidget(self.sidebar, 0)
        card_layout.addWidget(self.stack, 1)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(HALO, HALO, HALO, HALO)
        outer.addWidget(self.card)

        self._close_btn.raise_()
        self.navigate("now")

    # ------------------------------------------------------------ navigation

    def navigate(self, key: str) -> None:
        self.stack.setCurrentWidget(self.screens[key])
        for k, btn in self._nav_buttons.items():
            btn.setStyleSheet(_nav_qss(k == key))
        self.refresh_screen(key)

    def refresh_screen(self, key: str) -> None:
        ctx = self._context
        if key == "now":
            self.screens["now"].refresh(screens.now_screen_data(ctx.snapshot()))
        elif key == "today":
            self.screens["today"].refresh(screens.today_screen_data(ctx.day()))
        elif key == "history":
            self.screens["history"].refresh(screens.history_screen_data(ctx.period(7)))
        elif key == "habits":
            self.screens["habits"].refresh(screens.habits_screen_data(ctx.habits()))
        elif key == "suggestions":
            self.screens["suggestions"].refresh(screens.suggestion_cards(ctx.suggestions()))

    def refresh_now(self) -> None:
        if self.stack.currentWidget() is self.screens["now"]:
            self.screens["now"].refresh(screens.now_screen_data(self._context.snapshot()))

    # ------------------------------------------------------------- showing

    def show_centered(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else self.geometry()
        x = max(geo.left(), min(geo.center().x() - self.width() // 2, geo.right() - self.width() + 1))
        y = max(geo.top(), min(geo.center().y() - self.height() // 2, geo.bottom() - self.height() + 1))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()

    # ------------------------------------------------------------- dragging

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    # ---------------------------------------------------------------- keys

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------- settings

    def _open_settings(self) -> None:
        if self._settings is not None:
            try:
                self._settings.show_centered()
                return
            except RuntimeError:
                self._settings = None
        from .settings_window import SettingsWindow

        self._settings = SettingsWindow()
        self._settings.destroyed.connect(self._on_settings_destroyed)
        self._settings.show_centered()

    def _on_settings_destroyed(self) -> None:
        self._settings = None