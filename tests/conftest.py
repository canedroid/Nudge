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


@pytest.fixture(scope="session", autouse=True)
def isolated_config_directory(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point the config directory at a temporary one for the whole session.

    ``load_settings()`` and ``save_settings()`` both fall back to the real
    per-user directory when given no argument, which on this machine is the
    developer's actual ``%APPDATA%\\Nodify``. One test that forgets to pass a
    directory would then overwrite real settings, and the damage is invisible
    until the next launch. This happened once while writing the BOM test.

    Tests that assert where the real directory is set or remove the variable
    themselves with ``monkeypatch``, so this does not constrain them.
    """
    directory = tmp_path_factory.mktemp("nodify-session-config")
    previous = os.environ.get("NODIFY_CONFIG_DIR")
    os.environ["NODIFY_CONFIG_DIR"] = str(directory)
    yield
    if previous is None:
        os.environ.pop("NODIFY_CONFIG_DIR", None)
    else:
        os.environ["NODIFY_CONFIG_DIR"] = previous


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
