"""Shared test fixtures.

Qt tests run on the ``offscreen`` platform plugin so that the suite does not need
a desktop session. Tests that need a window create real widgets against that
plugin; they exercise Qt's own behaviour, not the compositor's hit testing, which
is verified manually and recorded in ``PROGRESS.MD``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QApplication

# Must be set before any Qt application is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    """A single session-wide QApplication, as Qt requires.

    Typed as the real class so tests can use the Qt API directly; yielding
    ``object`` hid that from both mypy and anyone reading the fixture.
    """
    from nodify.app.application import configure_surface_format

    configure_surface_format()
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app  # type: ignore[misc]
