# Nodify

A local-first desktop productivity command center for Windows: notes, to-dos,
files, and timers in a transparent, always-on-top overlay.

Markdown files in a vault you choose are the source of truth. There is no
database, and everything stays readable in Obsidian.

## Requirements

- Windows 10 or 11
- Python 3.13

## Setup

```bat
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Run

```bat
run.bat
```

or

```bat
.venv\Scripts\python -m nodify
```

## Verify

All four must pass before a change is committed.

```bat
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m pytest
```

## Layout

The overlay is fullscreen and always on top. Tiles run left to right:

| Position | Tile | Shape |
| --- | --- | --- |
| 1 | Timers and reminders | narrow portrait, full height |
| 2 | Notes | portrait, full height |
| 3 | To-Do | top of the wide right column |
| 4 | Explorer | wide, short landscape, below To-Do |

Drag a tile by its `⠿` handle and drop it on another tile to **swap their
positions and sizes**. Tiles can also be resized from any edge or corner, and the
layout is remembered.

The area between and around the tiles is **click-through**, so the desktop
underneath stays usable. This can be turned off in settings, which makes the
overlay capture every click.

Toggle the overlay with the global hotkey (default `Ctrl+Space`), `Escape`, the
Hide button, or the tray. Quitting is explicit.

## Vault

The vault is a folder of plain Markdown with YAML frontmatter:

```text
vault/
├── notes/
│   └── [Category]/
│       └── [Note-Title].md
├── todos/
│   └── [YYYY-MM]/
│       └── [DD-YYYY].md
└── timer/
    └── [YYYY-MM]/
        └── [DD-YYYY].md
```

The folder names are a public contract and will not change without a migration.

## Design documents

`PLAN.MD` is the design and sequencing source of truth. `PROGRESS.MD` is the
chronological implementation log. Both are git-ignored, so a fresh clone will not
contain them.

## Licence

MIT.
