"""The Timers tile.

A countdown shows a live ticking figure and a progress ring; an absolute reminder
shows the time it is due. Both are derived from the repository through the
:class:`TimerRepository` protocol, and every evaluation happens against an
injected clock, so the panel can be tested without waiting for real time to pass.

The ticking display is driven by a :class:`QTimer`, which means it can drift if
the event loop is busy. Every frame recomputes from the clock rather than
decrementing a counter, so a late tick produces a correct value rather than a
value that is permanently behind.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from nodify.domain.documents import Timer, TimerKind, TimerStatus
from nodify.domain.ports import TimerRepository
from nodify.domain.reminder_engine import ReminderEngine, TimerState
from nodify.styles import app_qss

_T = TypeVar("_T")

#: How often the countdown display refreshes. A second is the finest granularity
#: any of the stored timestamps have, so a faster tick would only burn CPU.
TICK_INTERVAL_MS = 1000

COUNTDOWN_CHOICES = (5, 10, 15, 25, 45, 60, 90)


class _Failure:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<repository call failed>"


FAILED = _Failure()


def format_duration(seconds: int) -> str:
    """``25:00`` or ``1:05:00``.

    A countdown reads as a clock face, so minutes and seconds are zero-padded and
    separated by a colon rather than rendered as a bare number of minutes.
    """
    sign = "-" if seconds < 0 else ""
    total = abs(int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{sign}{hours}:{minutes:02d}:{secs:02d}"
    return f"{sign}{minutes:02d}:{secs:02d}"


class TimersPanel(QWidget):
    """Countdown timers and absolute reminders."""

    timers_changed = pyqtSignal()
    #: A timer became due and has not been announced yet. The application layer
    #: decides how to notify; the panel only reports.
    timer_due = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        repository: TimerRepository,
        now: Callable[[], datetime],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tile")
        self._repository = repository
        self._now = now
        self._engine = ReminderEngine()
        self._announced: set[str] = set()
        self._cache: dict[str, Timer] = {}
        self._build_ui()
        self._start_ticking()
        self.reload()

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        self.setStyleSheet(app_qss.tile_qss() + app_qss.notes_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        title = QLabel("TIMERS")
        title.setObjectName("tileTitle")
        outer.addWidget(title)

        outer.addWidget(self._build_composer())
        outer.addWidget(self._build_list(), 1)
        outer.addLayout(self._build_actions())

        self._status = QLabel("")
        self._status.setObjectName("tileHint")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

    def _build_composer(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)

        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("Timer or reminder")
        self._title_input.returnPressed.connect(self.add_timer)
        row.addWidget(self._title_input, 1)

        self._kind_input = QComboBox()
        self._kind_input.addItem("Countdown", TimerKind.COUNTDOWN)
        self._kind_input.addItem("At a time", TimerKind.ABSOLUTE)
        self._kind_input.setFixedWidth(96)
        self._kind_input.currentIndexChanged.connect(self._on_kind_changed)
        row.addWidget(self._kind_input)

        self._minutes_input = QSpinBox()
        self._minutes_input.setRange(1, 1440)
        self._minutes_input.setValue(25)
        self._minutes_input.setSuffix(" min")
        self._minutes_input.setFixedWidth(80)
        row.addWidget(self._minutes_input)

        self._clock_input = QLineEdit()
        self._clock_input.setPlaceholderText("HH:MM")
        self._clock_input.setFixedWidth(62)
        self._clock_input.setVisible(False)
        row.addWidget(self._clock_input)

        layout.addLayout(row)
        return container

    def _on_kind_changed(self) -> None:
        countdown = self._selected_kind() is TimerKind.COUNTDOWN
        self._minutes_input.setVisible(countdown)
        self._clock_input.setVisible(not countdown)

    def _build_list(self) -> QWidget:
        self._list = QListWidget()
        return self._list

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._add_button = QPushButton("START")
        self._add_button.setObjectName("primary")
        self._add_button.clicked.connect(self.add_timer)

        self._snooze_button = QPushButton("SNOOZE 10")
        self._snooze_button.clicked.connect(self.snooze_selected)

        self._complete_button = QPushButton("DONE")
        self._complete_button.clicked.connect(self.complete_selected)

        self._dismiss_button = QPushButton("DISMISS")
        self._dismiss_button.clicked.connect(self.dismiss_selected)

        self._delete_button = QPushButton("DELETE")
        self._delete_button.setObjectName("danger")
        self._delete_button.clicked.connect(self.delete_selected)

        for button in (
            self._add_button,
            self._snooze_button,
            self._complete_button,
            self._dismiss_button,
            self._delete_button,
        ):
            row.addWidget(button)
        return row

    def _call(self, action: Callable[[], _T], message: str) -> _T | _Failure:
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - a panel must not crash
            self.error_occurred.emit(f"{message}: {exc}")
            return FAILED

    def _start_ticking(self) -> None:
        self._ticker = QTimer(self)
        self._ticker.setInterval(TICK_INTERVAL_MS)
        self._ticker.timeout.connect(self._on_tick)
        self._ticker.start()

    def _on_tick(self) -> None:
        self._refresh_labels()

    # --------------------------------------------------------------- display

    def reload(self) -> None:
        """Rebuild the list from the repository."""
        moment = self._now()
        pending = self._call(self._repository.all_pending, "Cannot read timers")
        finished = self._call(self._repository.all_timers, "Cannot read timers")
        if isinstance(pending, _Failure) or isinstance(finished, _Failure):
            return

        pending_ids = {timer.id for timer in pending}
        closed = [
            timer
            for timer in finished
            if timer.id not in pending_ids
            and timer.status in (TimerStatus.COMPLETED, TimerStatus.DISMISSED)
        ]

        self._list.blockSignals(True)
        self._list.clear()
        self._cache.clear()
        self._populate(pending, moment)
        self._populate_group("FINISHED", closed, moment)
        self._list.blockSignals(False)

        if self._list.count() == 0:
            self._status.setText("No timers. Add one above.")

        self._raise_due(pending, moment)

    def _populate(self, timers: list[Timer], moment: datetime) -> None:
        for timer in timers:
            # Cached so the one-second tick can redraw without touching the disk.
            self._cache[timer.id] = timer
            item = QListWidgetItem(self._text_for(timer, moment))
            item.setData(Qt.ItemDataRole.UserRole, timer.id)
            self._list.addItem(item)

    def _populate_group(self, label: str, timers: list[Timer], moment: datetime) -> None:
        if not timers:
            return
        header = QListWidgetItem(label)
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        header.setForeground(Qt.GlobalColor.gray)
        self._list.addItem(header)
        self._populate(timers, moment)

    def _text_for(self, timer: Timer, moment: datetime) -> str:
        """The row text for a timer at ``moment``.

        A countdown is rendered from the clock every time, never decremented, so
        a dropped or coalesced tick cannot leave the display permanently behind.
        """
        view = self._engine.view(timer, now=moment)
        title = view.title or "Untitled"

        if timer.kind is TimerKind.COUNTDOWN:
            if view.state in (TimerState.OVERDUE, TimerState.DUE):
                return f"{title}  —  TIME'S UP"
            remaining = view.remaining_seconds or 0
            return f"{title}  —  {format_duration(remaining)}"

        if view.state is TimerState.OVERDUE:
            return f"{title}  —  MISSED"
        due = timer.due_at
        if due is None:
            return title
        return f"{title}  —  {due.astimezone().strftime('%d %b %H:%M')}"

    def _refresh_labels(self) -> None:
        """Repaint row text from the clock, preserving the selection.

        The timers themselves are cached rather than re-read: a one-second tick
        that hit the filesystem once per row would be a needless amount of IO, and
        the values being displayed only depend on the clock, not on the data.
        """
        moment = self._now()
        for timer_id, item in self._rows():
            timer = self._cache.get(timer_id)
            if timer is None:
                continue
            item.setText(self._text_for(timer, moment))
        if self._selected_timer_id() is not None:
            self._select(self._selected_timer_id() or "")

    def _rows(self) -> list[tuple[str, QListWidgetItem]]:
        rows: list[tuple[str, QListWidgetItem]] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None:
                continue
            value = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(value, str):
                rows.append((value, item))
        return rows

    def _raise_due(self, timers: list[Timer], moment: datetime) -> None:
        """Emit :attr:`timer_due` once per timer that has come due.

        The in-memory set guards against a burst of reloads re-announcing the same
        timer. The durable guard is ``notified_at`` in the vault, which survives a
        restart; this set only covers the lifetime of the process.
        """
        for timer in self._engine.due_for_notification(timers, now=moment):
            if timer.id in self._announced:
                continue
            self._announced.add(timer.id)
            self.timer_due.emit(timer.id)

    def forget_announcement(self, timer_id: str) -> None:
        """Allow a timer to be announced again.

        Called once its notification has been recorded, so a snooze re-arms it and
        the next time it comes due the panel reports it once more.
        """
        self._announced.discard(timer_id)

    # ------------------------------------------------------------ operations

    def _selected_kind(self) -> TimerKind:
        value = self._kind_input.currentData()
        return value if isinstance(value, TimerKind) else TimerKind.COUNTDOWN

    def _selected_timer_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else None

    def add_timer(self) -> bool:
        """Create a timer or reminder from the composer."""
        title = self._title_input.text().strip()
        if not title:
            self.error_occurred.emit("A timer needs a title.")
            return False

        moment = self._now()
        kind = self._selected_kind()

        if kind is TimerKind.COUNTDOWN:
            minutes = self._minutes_input.value()
            created = self._call(
                lambda: self._repository.create(
                    moment, title, kind=TimerKind.COUNTDOWN, duration_seconds=minutes * 60
                ),
                "Cannot start the timer",
            )
        else:
            due = self._parse_clock(moment)
            if due is None:
                self.error_occurred.emit("Enter a time as HH:MM.")
                return False
            created = self._call(
                lambda: self._repository.create(moment, title, kind=TimerKind.ABSOLUTE, due_at=due),
                "Cannot add the reminder",
            )

        if isinstance(created, _Failure):
            return False

        self._title_input.clear()
        self.reload()
        self._select(created.id)
        self._status.setText("Started.")
        self.timers_changed.emit()
        return True

    def _parse_clock(self, moment: datetime) -> datetime | None:
        """Read ``HH:MM`` as a time on the day of ``moment``.

        A time earlier in the day than now is taken to mean tomorrow. A reminder
        set for 09:00 at 14:00 is a reminder for tomorrow morning, not one that is
        already eight hours late.
        """
        text = self._clock_input.text().strip()
        if not text:
            return None
        try:
            hours_text, _, minutes_text = text.partition(":")
            hours, minutes = int(hours_text), int(minutes_text or 0)
        except ValueError:
            return None
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            return None

        local = moment.astimezone()
        candidate = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if candidate <= local:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(UTC)

    def _select(self, timer_id: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == timer_id:
                self._list.setCurrentItem(item)
                return

    def complete_selected(self) -> bool:
        timer_id = self._selected_timer_id()
        if timer_id is None:
            return False
        moment = self._now()
        outcome = self._call(
            lambda: self._repository.complete(timer_id, moment), "Cannot finish the timer"
        )
        if isinstance(outcome, _Failure):
            return False
        self.forget_announcement(timer_id)
        self.reload()
        self.timers_changed.emit()
        return True

    def dismiss_selected(self) -> bool:
        timer_id = self._selected_timer_id()
        if timer_id is None:
            return False
        outcome = self._call(lambda: self._repository.dismiss(timer_id), "Cannot dismiss the timer")
        if isinstance(outcome, _Failure):
            return False
        self.forget_announcement(timer_id)
        self.reload()
        self.timers_changed.emit()
        return True

    def snooze_selected(self) -> bool:
        timer_id = self._selected_timer_id()
        if timer_id is None:
            return False
        moment = self._now()
        outcome = self._call(
            lambda: self._repository.snooze(timer_id, 10, at=moment), "Cannot snooze"
        )
        if isinstance(outcome, _Failure):
            return False
        self.forget_announcement(timer_id)
        self.reload()
        self._status.setText("Snoozed for 10 minutes.")
        self.timers_changed.emit()
        return True

    def delete_selected(self) -> bool:
        timer_id = self._selected_timer_id()
        if timer_id is None:
            return False
        outcome = self._call(lambda: self._repository.delete(timer_id), "Cannot delete the timer")
        if isinstance(outcome, _Failure):
            return False
        self.forget_announcement(timer_id)
        self.reload()
        self.timers_changed.emit()
        return True

    def shutdown(self) -> None:
        """Stop the ticker. Called when the panel goes away."""
        self._ticker.stop()
