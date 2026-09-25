"""Dashboard screen presenters — plain data in, HUD glass widgets out.

This is a self-contained reference of the Observer app's dashboard screens.
The pure ``*_screen_data`` helpers shape plain dicts/list-of-dicts (no app
models — pass whatever your app produces), and the widgets stay tiny and
easily copyable.
"""

from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import colors, fonts


def fmt_duration(seconds: float) -> str:
    """'1h 23m' / '45m' / '12s' — reference helper, copy or replace."""
    seconds = max(0, round(float(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def display_name(app: str) -> str:
    """Trim over-long app titles to a clean label."""
    name = str(app or "Unknown")
    if " - " in name:
        name = name.split(" - ")[0]
    if len(name) > 32:
        name = name[:29] + "..."
    return name


# ------------------------------------------------------------ pure shaping

def now_screen_data(snapshot: dict) -> dict:
    """Shape a live snapshot dict → Now screen fields."""
    mode = snapshot.get("mode")
    if mode == "away":
        return {
            "away": True,
            "headline": "Away from the desk",
            "detail": f"idle for {fmt_duration(snapshot.get('idle_seconds', 0.0))}",
        }
    app = display_name(snapshot.get("app") or "Unknown")
    title = snapshot.get("title") or ""
    elapsed = fmt_duration(snapshot.get("elapsed", 0.0))
    return {
        "away": False,
        "headline": title if title else app,
        "detail": f"{app} · running {elapsed}",
    }


def today_screen_data(day: dict) -> dict:
    """Shape a day dict → Today screen fields.

    Expects keys: ``active_sec``, ``away_sec``, ``sessions``, ``apps``
    (list of {"name", "total_sec", "opens"}), ``rows`` (list of
    {"started", "ended", "duration", "app", "title"}), ``aways``
    (list of {"started", "ended", "duration"}).
    """
    active = day.get("active_sec") or 1.0
    apps = sorted(day.get("apps") or [], key=lambda a: -(a.get("total_sec") or 0))
    apps_rows = [
        {
            "name": display_name(a.get("name")),
            "total_text": fmt_duration(a.get("total_sec") or 0),
            "opens": a.get("opens", 0),
            "pct": int((a.get("total_sec") or 0) / active * 100),
        }
        for a in apps
    ]
    rows = [
        {
            "when": f"{dt.datetime.fromtimestamp(r['started']):%H:%M}–"
            f"{dt.datetime.fromtimestamp(r['ended']):%H:%M}",
            "dur": fmt_duration(r.get("duration") or 0),
            "app": display_name(r.get("app")),
            "title": r.get("title", ""),
        }
        for r in day.get("rows") or []
    ]
    aways = [
        {
            "when": f"{dt.datetime.fromtimestamp(a['started']):%H:%M}–"
            f"{dt.datetime.fromtimestamp(a['ended']):%H:%M}",
            "dur": fmt_duration(a.get("duration") or 0),
        }
        for a in day.get("aways") or []
    ]
    return {
        "active": fmt_duration(day.get("active_sec") or 0),
        "away": fmt_duration(day.get("away_sec") or 0),
        "sessions": day.get("sessions", 0),
        "apps": apps_rows,
        "rows": rows,
        "aways": aways,
    }


def history_screen_data(period: dict) -> dict:
    """Shape a period dict → History screen fields.

    Expects keys: ``start``/``end`` (ISO strings or datetimes), ``active_sec``,
    ``away_sec``, ``apps`` (list of {"name", "total_sec", "opens"}).
    """
    active = period.get("active_sec") or 1.0
    apps = sorted(period.get("apps") or [], key=lambda a: -(a.get("total_sec") or 0))[:15]
    start = period.get("start")
    end = period.get("end")
    if hasattr(start, "isoformat"):
        start = start.isoformat()
    if hasattr(end, "isoformat"):
        end = end.isoformat()
    return {
        "start": start,
        "end": end,
        "active": fmt_duration(period.get("active_sec") or 0),
        "away": fmt_duration(period.get("away_sec") or 0),
        "apps": [
            {
                "name": display_name(a.get("name")),
                "total_text": fmt_duration(a.get("total_sec") or 0),
                "opens": a.get("opens", 0),
                "pct": int((a.get("total_sec") or 0) / active * 100),
            }
            for a in apps
        ],
    }


def habits_screen_data(apps: list[dict]) -> list[dict]:
    """Shape habit rules (list of {"app", "days_used", "window", "label"}) → rows."""
    rows = []
    for reg in sorted(apps, key=lambda r: -(r.get("days_used") or 0)):
        rows.append(
            {
                "app": display_name(reg.get("app")),
                "days_used": reg.get("days_used", 0),
                "window": reg.get("window", 7),
                "label": reg.get("label", ""),
            }
        )
    return rows


def suggestion_cards(suggestions: list) -> list[dict]:
    """Shape suggestion objects (severity/title/detail) → cards.

    Each suggestion may be a dict or an object exposing ``severity``,
    ``title`` and ``detail`` attributes.
    """
    levels = {"info": "i", "warn": "!", "alert": "!!"}
    cards = []
    for s in suggestions:
        if isinstance(s, dict):
            severity = s.get("severity", "info")
            cards.append(
                {
                    "tag": levels.get(severity, "i"),
                    "severity": severity,
                    "title": s.get("title", ""),
                    "detail": s.get("detail", ""),
                }
            )
        else:
            severity = getattr(s, "severity", "info")
            cards.append(
                {
                    "tag": levels.get(severity, "i"),
                    "severity": severity,
                    "title": getattr(s, "title", ""),
                    "detail": getattr(s, "detail", ""),
                }
            )
    return cards


# --------------------------------------------------------------- widgets

def _muted(text: str, size: int = 10) -> QLabel:
    label = QLabel(text)
    label.setFont(fonts.body_font(size))
    label.setStyleSheet(f"color: {colors.TEXT_DIM}; background: transparent;")
    return label


def _table_qss() -> str:
    return f"""
    QTableWidget {{
        background-color: rgba(20, 20, 20, 140);
        alternate-background-color: rgba(200, 200, 200, 12);
        gridline-color: rgba(200, 200, 200, 40);
        border: 1px solid rgba(200, 200, 200, 90);
        border-radius: 10px;
        color: {colors.TEXT};
        font-family: "{colors.FONT_FAMILY}";
        font-size: 11px;
        outline: none;
    }}
    QTableWidget::item {{ padding: 4px; }}
    QHeaderView::section {{
        background-color: transparent;
        color: {colors.TEXT_DIM};
        border: none;
        padding: 4px;
        font-family: "{colors.FONT_FAMILY_HEADER}";
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QTableCornerButton::section {{
        background: transparent;
        border: none;
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


class _Bar(QWidget):
    def __init__(self, name: str, value_text: str, pct: int):
        super().__init__()
        self.setStyleSheet(f"background: rgba(200, 200, 200, 14); border-radius: 8px;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        label = QLabel(f"{name}")
        label.setFont(fonts.body_font(12))
        label.setStyleSheet(f"color: {colors.TEXT}; background: transparent;")
        value = QLabel(value_text)
        value.setFont(fonts.body_font(10, bold=True))
        value.setStyleSheet(f"color: {colors.TEXT_DIM}; background: transparent;")
        bar = QProgressBar()
        bar.setMaximum(100)
        bar.setValue(max(1, min(100, pct)))
        bar.setFixedWidth(160)
        bar.setStyleSheet(_bar_qss())
        layout.addWidget(label)
        layout.addWidget(value)
        layout.addStretch(1)
        layout.addWidget(bar)


def _bar_qss() -> str:
    return f"""
    QProgressBar {{
        background-color: rgba(200, 200, 200, 30);
        border: none;
        border-radius: 4px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ border-radius: 4px; background-color: {colors.PURPLE_GLOW}; }}
    """


class NowScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.headline = QLabel("—")
        self.headline.setFont(fonts.body_font(26, bold=True))
        self.headline.setWordWrap(True)
        self.headline.setStyleSheet(f"color: {colors.TEXT}; background: transparent;")
        self.detail = _muted("", 13)
        layout = QVBoxLayout(self)
        layout.addWidget(self.headline)
        layout.addWidget(self.detail)
        layout.addStretch(1)

    def refresh(self, data: dict):
        self.headline.setText(data["headline"])
        self.detail.setText(data["detail"])


class TodayScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.summary = _muted("", 10)
        self.bars_box = QVBoxLayout()
        self.bars_box.setSpacing(6)
        self.rows_table = QTableWidget(0, 4)
        self.rows_table.setHorizontalHeaderLabels(["WHEN", "DUR", "APP", "WHAT I WAS DOING"])
        self.rows_table.verticalHeader().setVisible(False)
        self.rows_table.setAlternatingRowColors(True)
        self.rows_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.rows_table.setStyleSheet(_table_qss())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 18, 18, 18)
        layout.addWidget(self.summary)
        layout.addLayout(self.bars_box)
        layout.addWidget(self.rows_table, stretch=1)

    def refresh(self, data: dict):
        self.summary.setText(
            f"ACTIVE {data['active']}   ·   AWAY {data['away']}   ·   {data['sessions']} SESSIONS"
        )
        while self.bars_box.count():
            item = self.bars_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for app in data["apps"]:
            self.bars_box.addWidget(
                _Bar(app["name"], app["total_text"], app["pct"])
            )
        self.rows_table.setRowCount(len(data["rows"]))
        for i, row in enumerate(data["rows"]):
            for j, value in enumerate(
                (row["when"], row["dur"], row["app"], row["title"])
            ):
                item = QTableWidgetItem(value)
                item.setForeground(QColor(colors.TEXT))
                self.rows_table.setItem(i, j, item)
        self.rows_table.resizeColumnsToContents()


class HistoryScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.summary = _muted("", 10)
        self.bars_box = QVBoxLayout()
        self.bars_box.setSpacing(6)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 18, 18, 18)
        layout.addWidget(self.summary)
        layout.addLayout(self.bars_box)
        layout.addStretch(1)

    def refresh(self, data: dict):
        self.summary.setText(
            f"{data['start']} → {data['end']}   ·   ACTIVE {data['active']}   ·   AWAY {data['away']}"
        )
        while self.bars_box.count():
            item = self.bars_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for app in data["apps"]:
            self.bars_box.addWidget(
                _Bar(app["name"], app["total_text"], app["pct"])
            )


class HabitsScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.box = QVBoxLayout()
        self.box.setSpacing(6)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 18, 18, 18)
        layout.addWidget(_muted("HABITS · LAST 7 DAYS", 10))
        layout.addLayout(self.box)
        layout.addStretch(1)

    def refresh(self, rows: list[dict]):
        while self.box.count():
            item = self.box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for row in rows:
            label = QLabel(f"{row['app']}  —  {row['label']}")
            label.setFont(fonts.body_font(12))
            label.setWordWrap(True)
            label.setStyleSheet(
                f"color: {colors.TEXT}; background: rgba(200, 200, 200, 14);"
                f" border-radius: 8px; padding: 10px;"
            )
            self.box.addWidget(label)


class SuggestionsScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.box = QVBoxLayout()
        self.box.setSpacing(10)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 18, 18, 18)
        layout.addWidget(_muted("SUGGESTIONS", 10))
        layout.addLayout(self.box)
        layout.addStretch(1)

    def refresh(self, cards: list[dict]):
        while self.box.count():
            item = self.box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for card in cards:
            self.box.addWidget(_SuggestionCard(card))


class _SuggestionCard(QWidget):
    """A single mini glass card: tag badge + title + muted detail."""

    def __init__(self, card: dict):
        super().__init__()
        self.setFixedWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        header = QHBoxLayout()
        tag = card["tag"]
        badge = QLabel(tag)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedWidth(26)
        badge.setFont(fonts.body_font(10, bold=True))
        badge.setStyleSheet(
            f"color: {colors.BG_GLASS_STRONG}; background: {colors.PURPLE_GLOW};"
            f" border-radius: 6px; padding: 1px 4px;"
        )
        title = QLabel(card["title"])
        title.setFont(fonts.body_font(12, bold=True))
        title.setStyleSheet(f"color: {colors.TEXT}; background: transparent;")
        title.setWordWrap(True)
        header.addWidget(badge)
        header.addWidget(title, stretch=1)
        layout.addLayout(header)

        detail = QLabel(card["detail"])
        detail.setFont(fonts.body_font(10))
        detail.setWordWrap(True)
        detail.setStyleSheet(f"color: {colors.TEXT_DIM}; background: transparent;")
        layout.addWidget(detail)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        gradient = QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0.0, QColor(36, 36, 36, 255))
        gradient.setColorAt(1.0, QColor(20, 20, 20, 255))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(colors.PURPLE), 1))
        painter.drawRoundedRect(rect, 10, 10)