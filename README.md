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

On the first run a folder picker asks for your vault, and the choice is
remembered. If that folder later stops being a vault, you are asked again rather
than started on a broken vault.

## Create a vault

Nodify **has no interface for creating a vault yet**, so an existing one is
required before first run. To make an empty one:

```bat
.venv\Scripts\python -c "from pathlib import Path; from nodify.adapters.vault import create; print(create(Path(r'C:\path\to\your\vault')))"
```

This writes the `.nodify-vault` marker and the `notes/`, `todos/`, and `timer/`
folders. It refuses to touch a non-empty folder, so pointing it at a directory of
existing Markdown will not silently rearrange your files. Pick that folder in
the prompt.

An existing plain-Markdown folder can also be used by creating the empty
`.nodify-vault` marker inside it by hand.

## Verify

All four must pass before a change is committed.

```bat
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m pytest
```

A live end-to-end check, which builds a throwaway vault, launches the overlay with
all four panels, writes a note and reads it back:

```bat
.venv\Scripts\python -m nodify.smoke
```

## Layout

The overlay is fullscreen and always on top. Four tiles run left to right across
the width:

| Position | Tile | Width |
| --- | --- | --- |
| 1 | Timers and reminders | narrowest |
| 2 | Notes | widest |
| 3 | To-Do | standard |
| 4 | Files | standard |

The layout engine and each tile's drag strip and resize grip are implemented
and unit tested, including eight-pixel snapping, edge alignment, and clamping a
tile inside the screen so it can never be dragged somewhere it cannot be
recovered from. **The frames are mounted, but their drag and resize signals are
not yet connected to the layout, so tiles do not move yet and a drag is not
remembered across restarts.** Until that is wired up, the four tiles sit at their
computed starting positions.

The area between and around the tiles is **click-through**, so the desktop
underneath stays usable. This can be turned off, which makes the overlay capture
every click.

Toggle the overlay with `Escape`, the Hide button, or the tray. Quitting is
explicit. Closing the window hides the overlay rather than ending the process,
and the hotkey is released on exit.

The default accelerator is `Ctrl+Space` and it is registered, but **it is
currently a Qt `QShortcut` parented to the `QApplication`, not a Win32
`RegisterHotKey` hook.** It fires while Nodify owns focus, which is enough for
the tests and the tray path but is *not* a true system-wide hotkey; it will not
summon the overlay over another application. Making it genuinely global needs a
Win32 registrar, and pressing the combination with Nodify in the background is
the manual check that has not been done yet.

A hotkey without a modifier is refused, and a rebind that the system rejects
rolls back to the previous working combination rather than leaving the overlay
unreachable.

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

A day file under `todos/` holds a structured `tasks:` sequence with a generated
Obsidian checklist body, so many tasks share one file. The YAML is the record and
the checklist is a rendering; editing the checklist in Obsidian is not read back.

Note and timer categories come from the filesystem. A folder you create in
Obsidian, or drop into the vault from anywhere, is a category Nodify lists.

### Your files are not rewritten

Nodify is a safe editor for a vault you also open in other tools.

- Frontmatter keys it does not recognise, including Obsidian metadata, are written
  back exactly as they were read.
- Comments, quoting style and key order in frontmatter survive a save.
- The Markdown body is never escaped or reformatted.
- Editing one task does not rewrite its siblings in the same day file.
- A file that cannot be read is left byte for byte alone and stays visible in the
  Files view, because only you can repair your own text.
- If a file changed outside Nodify while it was open, the save is refused and
  reported rather than discarding what you changed.

Writes go through a temporary file in the same folder and are then moved into
place, so an interrupted save cannot leave a truncated document.

## Configuration

`%APPDATA%\Nodify\settings.json` is written on first launch and holds the hotkey,
opacity, backdrop, click-through state and the tile layout. A malformed file is
moved to `settings.recovered.json` and the application starts on defaults, rather
than overwriting whatever you had configured or refusing to start.

A UTF-8 byte-order mark is tolerated, because Notepad and PowerShell both write
one by default on Windows. Treating that as corruption would discard a working
configuration on a perfectly ordinary save, so a BOM-prefixed file is read
normally and rewritten without it.

If the remembered vault is no longer a valid vault, the app says so plainly and
exits, rather than starting against the wrong folder and reporting confusing
errors from every panel.

Every value is clamped on read. A negative opacity, a zero tile width or a
modifier-less hotkey are all reachable by hand-editing the file, and each would
produce a broken or unsafe window.

## Packaging

```bat
.venv\Scripts\python -m PyInstaller nodify.spec --noconfirm
```

Produces a windowed `Nodify.exe` of roughly 35 MB with no console window, which
would otherwise be a second always-on-top window competing with the product.

## Design documents

`PLAN.MD` is the design and sequencing source of truth. `PROGRESS.MD` is the
chronological implementation log. Both are git-ignored, so a fresh clone will not
contain them.

## Licence

**The declared `MIT` licence in `pyproject.toml` is not compatible with the
dependencies, and must be resolved before this project is distributed.**

Verified against Riverbank Computing's documentation and the PyQt6 package
metadata: PyQt6 is dual licensed under the GPL v3 and the Riverbank Commercial
License, and is **not** available under the LGPL. The bundled Qt libraries are
LGPL v3. Riverbank states plainly that if you use the GPL version of PyQt, your
own code must also use a GPL-compatible licence.

So the GPL-3.0-only wheels installed here carry that obligation through to this
application. Distributing a build of it as MIT would not be compliant.

The three ways this resolves:

1. **Relicense this project under the GPL v3.** The simplest option, and the one
   that matches what the code is actually bound to today.
2. **Purchase a Riverbank PyQt commercial licence**, which permits distribution
   under a proprietary licence. The licence must be bought before the first
   distribution, and covers only the versions current at purchase.
3. **Replace PyQt6 with PySide6**, which is LGPL. This changes the licensing
   picture entirely and is the only option that preserves a permissive licence,
   at the cost of a toolkit swap.

Until one of these is chosen, treat the repository as not distributable. The
licence question is unresolved deliberately rather than by oversight.

Third-party components keep their own licences: `ruamel.yaml` is MIT,
`watchdog` is Apache-2.0, and `PyQt6-sip` is BSD-2-Clause.
