"""Runnable observer-style HUD demo — ``python -m glass_ui_without_edges.demo``.

Builds a HudWindow with a fake context (plain dicts), a tray icon and the
settings dialog, so the whole kit can be eyeballed and copy-pasted from.

Brought to you by the bundled kit only — nothing outside glass_ui_without_edges is touched.
"""

from __future__ import annotations

import datetime as dt
import sys

from PyQt6.QtWidgets import QApplication

from . import window
from .tray import TrayIcon

_TODAY = dt.date.today()
_NOW = dt.datetime.now()


class _FakeContext:
    """The data contract HudWindow expects — plug in your own app here."""

    def snapshot(self) -> dict:
        return {
            "mode": "focus",
            "app": "Visual Studio Code",
            "title": "glass_ui_without_edges/demo.py — HUD reference kit",
            "elapsed": 3725.0,
        }

    def day(self) -> dict:
        return {
            "active_sec": 5 * 3600 + 13 * 60,
            "away_sec": 47 * 60,
            "sessions": 11,
            "apps": [
                {"name": "Visual Studio Code", "total_sec": 2 * 3600 + 800, "opens": 14},
                {"name": "Chrome", "total_sec": 1 * 3600 + 900, "opens": 22},
                {"name": "Windows Terminal", "total_sec": 1800, "opens": 9},
                {"name": "Discord", "total_sec": 900, "opens": 6},
            ],
            "rows": [
                {
                    "started": int((_NOW - dt.timedelta(hours=2, minutes=12)).timestamp()),
                    "ended": int((_NOW - dt.timedelta(hours=1, minutes=40)).timestamp()),
                    "duration": 1900.0,
                    "app": "Visual Studio Code",
                    "title": "refactor bottom bar",
                },
                {
                    "started": int((_NOW - dt.timedelta(minutes=38)).timestamp()),
                    "ended": int(_NOW.timestamp()),
                    "duration": 2280.0,
                    "app": "Chrome",
                    "title": "PyQt6 translucent window docs",
                },
            ],
            "aways": [
                {
                    "started": int((_NOW - dt.timedelta(hours=1, minutes=18)).timestamp()),
                    "ended": int((_NOW - dt.timedelta(minutes=58)).timestamp()),
                    "duration": 1200.0,
                }
            ],
        }

    def period(self, days: int) -> dict:
        return {
            "start": (_TODAY - dt.timedelta(days=days - 1)).isoformat(),
            "end": _TODAY.isoformat(),
            "active_sec": 22 * 3600,
            "away_sec": 3 * 3600,
            "apps": [
                {"name": "Visual Studio Code", "total_sec": 12 * 3600, "opens": 88},
                {"name": "Chrome", "total_sec": 6 * 3600, "opens": 120},
                {"name": "Windows Terminal", "total_sec": 2 * 3600, "opens": 50},
            ],
        }

    def habits(self) -> list[dict]:
        return [
            {"app": "Chrome", "days_used": 7, "window": 7, "label": "Daily browsing"},
            {"app": "Visual Studio Code", "days_used": 6, "window": 7, "label": "Coding streak"},
            {"app": "Discord", "days_used": 2, "window": 7, "label": "Weekly check-in"},
        ]

    def suggestions(self) -> list:
        return [
            {"severity": "warn", "title": "Chrome passed 40% of your day", "detail": "Try a 25-minute focus block before the next tab."},
            {"severity": "info", "title": "Discord is late in the evening again", "detail": "Two nights at 23:30. Schedule a cutoff at 22:00."},
            {"severity": "alert", "title": "No away breaks in 3 sessions", "detail": "Step away for 5 minutes — your streak is at risk."},
        ]

    def quit(self) -> None:
        QApplication.instance().quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    hud = window.HudWindow(_FakeContext())
    hud.show_centered()

    tray = TrayIcon(hud)
    tray.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())