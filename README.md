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

A live end-to-end check, which builds a throwaway vault, mounts all four tiles,
reads the tile fill's alpha back off a real paint, performs a drag and a resize,
confirms the arrangement reached disk, and writes a note and reads it back:

```bat
.venv\Scripts\python -m nodify.smoke
```

It exits non-zero if any of that does not happen, so it is a gate rather than a
demo.

All five run automatically on every push to `main` and on every pull request, on
Windows. See `.github/workflows/ci.yml`.

## Layout

The overlay is fullscreen and always on top. Four tiles run left to right across
the width:

| Position | Tile | Width |
| --- | --- | --- |
| 1 | Timers and reminders | narrowest |
| 2 | Notes | widest |
| 3 | To-Do | standard |
| 4 | Files | standard |

### Arranging them

**Drag a tile by its title strip to move it. Drag any edge or corner to resize it.**
Both are live, snapped to an 8-pixel grid, magnetically aligned to the other
tiles' edges, and clamped so a tile can never be left somewhere it cannot be
recovered from. **Dropping a tile on top of another swaps the two**, trading
positions but keeping each one's size, so the notes tile stays the widest no
matter where you put it.

The arrangement is remembered across restarts. It is written once when you let go,
not on every mouse movement, so a drag is a single write rather than sixty.

The layout engine, the drag and resize gestures, and the wiring between them are
unit tested, including a mutation check that fails if the signals are
disconnected — the bug that once made every tile immovable would now break the
suite.

### The tiles are separate windows

Each tile is its own frameless, always-on-top, translucent top-level window, not a
region of the overlay. This is not incidental: the Windows compositor applies blur
per window and offers no way to blur one rectangle inside a window, so blurring the
tiles individually requires separate windows. The area between and around them is
still the overlay, which is why it can remain **click-through** while the tiles
themselves stay interactive.

The consequence is that the overlay is no longer the only window on screen, so
hiding goes through the application rather than through the overlay: `Escape`, the
Hide button, the window close button and the tray all take the tiles with them.
Hiding only the overlay would leave four tiles floating over whatever you switched
to, with no header left to close them.

The area between and around the tiles is **click-through**, so the desktop
underneath stays usable. This can be turned off, which makes the overlay capture
every click.

Toggle the overlay with `Escape`, the Hide button, or the tray. Quitting is
explicit. Closing the window hides the dashboard rather than ending the process,
and the hotkey is released on exit.

## The glass

The tiles are genuinely see-through, and the blur behind them is real rather than a
painted approximation. Nodify asks the Windows compositor for it, in this order:

1. `SetWindowCompositionAttribute` with the requested effect (acrylic, blur, or
   mica). Undocumented, but it has shipped unchanged since Windows 8 and is the
   only one known to composite on a translucent Qt window.
2. The same call with plain blur behind, which is the most widely supported.
3. `DwmSetWindowAttribute` with `DWMWA_SYSTEMBACKDROP_TYPE`. This is the documented
   Win11 22H2+ route, but it is reported not to composite while the layered-window
   style Qt needs for translucency is set, so it is a last resort.

If the compositor refuses all of them the tiles keep a translucent tint and
everything still works; losing the blur is cosmetic, never a failure.

**The order is measured, not assumed.** On Windows 11 build 26200 the acrylic state
does composite and Mica's host-backdrop state does not, which is the opposite of
what most guides assume, so the ladder is probed on the machine in front of you:

```bat
.venv\Scripts\python -m nodify.backdrop_probe "%TEMP%\probe.png"
```

It generates a stripe pattern, measures how much the compositor actually blurs it,
and prints the numbers. An API that returns success while changing no pixels is
precisely the failure this exists to catch, and it is invisible to a screenshot
taken in the wrong place.

`backdrop` in `settings.json` takes `acrylic` (the default), `blur`, `mica`, or
`none`. An explicit choice is always respected, so turning the glass off is not
undone by upgrading.

The tint is medium: about 55% of the dark tile colour, so text stays readable over
anything behind it. It is one constant, `TILE_TINT_ALPHA`, read by both the
stylesheet and the tile's own paint, so the two cannot disagree.

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

```json
{
  "backdrop": "acrylic",
  "click_through": true,
  "hotkey": "Ctrl+Space",
  "layout": [
    { "height": 900, "width": 306, "x": 0, "y": 0 }
  ],
  "opacity": 0.75,
  "vault_path": "C:\\Users\\you\\vault",
  "version": 1
}
```

`layout` holds one entry per tile, in the order given in the table above, so the
first rectangle is the timers tile. It is written on drop, so a hand-edited
arrangement is preserved until you move something.

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

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the four gates, the live smoke check, and
the handful of non-obvious things about this codebase. Bug reports and feature
requests have their own templates under `.github/ISSUE_TEMPLATE`.

## Known limitations

Honest list, so nothing here is a surprise:

- **The hotkey is not system-wide.** It is a `QShortcut`, not a Win32
  `RegisterHotKey` hook, so it needs Nodify to have focus.
- **No vault-creation interface.** A one-line command is documented above.
- **One screen.** The tiles are placed on the display under the cursor and are not
  tracked per monitor, so a resolution change re-flows them but a second monitor is
  not given its own arrangement.
- **The phone companion is inert.** The bridge interface exists and is disabled by
  default.
- **No settings dialog.** `settings.json` is the configuration surface; the header's
  settings button says "Settings are not available yet" in the status line rather
  than failing silently.

## Licence

**GPL-3.0-only.** The full text is in [LICENSE](LICENSE).

This is not a preference, and it is not a leftover. PyQt6 is dual licensed under
GPL-3.0-only and the Riverbank Commercial License, and is **not** available under
the LGPL, although the bundled Qt libraries are. Riverbank states plainly that if
you use the GPL version of PyQt, your own code must also use a GPL-compatible
licence. GPL-3.0-only is therefore the only free option for a project that keeps
PyQt6, and it is what the dependencies as installed actually require.

The two alternatives, for the record: buy a Riverbank commercial licence, which must
be bought before the first distribution and covers only the versions current at
purchase; or move to PySide6, which is LGPL and would change the licensing picture
entirely, at the cost of a toolkit swap.

If you redistribute a build, you must pass on this same licence and make the source
available, and you must keep the notices intact. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the short version.

Third-party components keep their own licences: `ruamel.yaml` is MIT,
`watchdog` is Apache-2.0, and `PyQt6-sip` is BSD-2-Clause.
