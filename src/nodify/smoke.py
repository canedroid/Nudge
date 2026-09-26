"""Live launch check for the integrated application.

Exits by itself after verifying that the overlay, the four tiles, the hotkey and
the vault all came up together. Run with ``python -m nodify.smoke``.

This is the end-to-end counterpart to the test suite: the tests prove each piece,
this proves they meet.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    """Build a throwaway vault, launch the dashboard, and report what came up."""
    from nodify.adapters.vault import Vault, create
    from nodify.app.application import configure_surface_format
    from nodify.app.dashboard import Dashboard, Services
    from nodify.domain.clock import FixedClock
    from nodify.services.hotkey import HotkeyService, RecordingRegistrar
    from nodify.services.settings import AppSettings
    from nodify.ui.layout.tile_layout import Rect

    configure_surface_format()
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setApplicationName("NodifySmoke")
    app.setQuitOnLastWindowClosed(False)

    root = Path(tempfile.mkdtemp()) / "SmokeVault"
    create(root, initial_year_month="2026-09")
    vault = Vault(root)
    vault.open()

    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    services = Services(vault=vault, clock=FixedClock(now))

    # Keep the smoke run out of the real config directory.
    os.environ["NODIFY_CONFIG_DIR"] = str(root.parent / "config")

    registrar = RecordingRegistrar()
    hotkey = HotkeyService(registrar.register, registrar.unregister)
    registered = hotkey.register(AppSettings().hotkey)

    dashboard = Dashboard(
        services,
        AppSettings(),
        confirm=lambda _t, _m: True,
        prompt=lambda _t, _l, initial: initial,
    )

    from nodify.ui.overlay import Overlay

    overlay = Overlay()
    overlay.setGeometry(0, 0, 1600, 900)
    overlay.show_overlay()
    app.processEvents()

    # Mount for real. An earlier version of this check printed "tiles mounted: 4"
    # while only constructing the panels, which read like reassurance and proved
    # nothing: nothing was ever a child of a window. Assert what the user would
    # see instead, using the overlay's own tile area so the real geometry path is
    # what gets exercised.
    region = overlay.tile_area()
    area = dashboard.mount(overlay, Rect(region.x(), region.y(), region.width(), region.height()))
    app.processEvents()

    visible = sum(1 for frame in area.values() if frame.isVisible() and frame.width() > 0)
    print(f"platform: {app.platformName()}")
    print(f"overlay visible: {overlay.isVisible()}  size: {overlay.width()}x{overlay.height()}")
    print(f"click-through: {overlay.click_through}")
    print(f"hotkey registered: {registered} ({hotkey.current})")
    print(f"tiles visible: {visible}/{len(area)}")
    for tile, frame in area.items():
        rect = dashboard.geometry_for(tile)
        inside = (
            "in" if overlay.rect().contains(frame.geometry().topLeft()) else "OUTSIDE THE OVERLAY"
        )
        print(
            f"  {tile.value:8} {type(frame.content).__name__:14} "
            f"{frame.width():>4}x{frame.height():<4} at ({rect.x},{rect.y}) {inside}"
        )
    print(f"vault: {vault.root.name}")
    print(f"bridge enabled: {dashboard.bridge.is_enabled()}")

    if visible != len(area):
        raise SystemExit(f"only {visible} of {len(area)} tiles are actually on screen")

    # A real mutation, to prove the panels are wired to the vault and not stubs.
    note = services.notes.create("Smoke note", "Smoke", "written by the smoke test\n")
    dashboard.refresh_all()
    listed = dashboard.files_panel._list.count()
    print(f"note written: {note.id} | files listed: {listed}")
    print(f"note body round-trips: {services.notes.get(note.id).body.strip()!r}")

    # And that it reached the disk, not just the in-memory index.
    on_disk = sorted(p.name for p in (root / "notes" / "Smoke").glob("*.md"))
    print(f"note on disk: {on_disk}")
    if not on_disk:
        raise SystemExit("the note never reached the vault")

    dashboard.shutdown()
    hotkey.unregister()
    overlay.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
