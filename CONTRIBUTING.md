# Contributing to Nodify

Thanks for considering it. Nodify is a local-first Windows desktop dashboard: your
notes, tasks, timers and files stay in a plain folder on your own disk, in Markdown
and YAML that any other tool can read.

## Getting it running

```powershell
git clone https://github.com/canedroid/Nudge.git
cd Nudge
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m nodify
```

Nodify is Windows-only and deliberately so. It uses the Windows compositor for the
blur behind the tiles, registers a Win32 global hotkey, and writes a `.nodify-vault`
that Obsidian opens directly.

## Before you open a pull request

Run the same four gates CI runs. They are fast and they catch nearly everything:

```powershell
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m mypy src
.\.venv\Scripts\python -m pytest
```

Then check the real thing, because the unit tests cannot see the compositor:

```powershell
.\.venv\Scripts\python -m nodify.smoke
```

`smoke` builds a throwaway vault, mounts the dashboard, reads the tile fill alpha
back off a real paint, performs a drag and a resize, and confirms the arrangement
reached disk. If a change touches the tiles, the glass, or window stacking, this is
the check that will tell you the truth.

## How the code is arranged

- `src/nodify/domain` — plain data and rules. No Qt, no filesystem, no clock.
- `src/nodify/adapters` — the filesystem. The only place that knows what a vault is.
- `src/nodify/services` — settings, hotkey registration, the file watcher.
- `src/nodify/ui` — widgets, styles, and the layout engine.
- `src/nodify/app` — the application object that wires the above together.
- `tests` — mirrors the tree.

The rule that keeps it testable: logic does not import Qt. If you find yourself
wanting a `QApplication` inside a rule, the rule wants a parameter instead.

## Things worth knowing before you change them

**The tiles are separate top-level windows.** This is not incidental. The compositor
applies blur per window, so blurring one region of a window is not something it
offers; each tile is its own frameless, always-on-top, translucent window. That is
why hiding the application has to go through `Application.hide()` rather than
`Overlay.hide()` — the overlay is no longer the only window on screen.

**Backdrops are probed, not assumed.** `SetWindowCompositionAttribute` is
undocumented and `DwmSetWindowAttribute` is documented but does not composite on a
layered window. Both are tried, most-likely-first, and a refusal falls back to a
plain translucent tint rather than failing. If you touch `ui/win_backdrop.py`, run
the probe and read the numbers:

```powershell
.\.venv\Scripts\python -m nodify.backdrop_probe "$env:TEMP\probe.png"
```

The sharpness figure is the real measurement. An API that returns success and
changes no pixels is the failure mode this module exists to survive.

**Layout is written on drop, not on move.** Dragging emits `tile_moving` and stays
entirely in memory; releasing emits `tile_dropped` and persists once. Please keep it
that way, or one drag becomes sixty writes to the user's config file.

## Style

Match the file you are in. The project uses `ruff format`, so do not hand-format.
Comments explain *why* something is the way it is, especially where a simpler-looking
alternative is wrong; there are several comments referring back to bugs that this
codebase has actually had. Please keep that habit.

## Licence

Nodify is GPL-3.0-only. That is not a preference: PyQt6 is not available under the
LGPL, so GPL is the only free licence compatible with the dependencies as installed.
By contributing you agree that your contribution is licensed GPL-3.0-only. See
[LICENSE](LICENSE).
