"""Clock abstraction.

The reminder engine and the schedule windows must be testable without depending
on wall-clock time, so nothing in ``domain`` may call ``datetime.now()`` directly.
Every time source is injected through this protocol.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """A source of the current time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock frozen at a chosen instant, for tests.

    The value may be advanced with :meth:`advance` to simulate the passage of
    time without sleeping.
    """

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def advance(self, **kwargs: float) -> None:
        """Move the clock forward by a ``datetime.timedelta`` keyword amount."""
        from datetime import timedelta

        self._moment += timedelta(**kwargs)

    def set(self, moment: datetime) -> None:
        """Jump the clock to an absolute instant."""
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._moment = moment
