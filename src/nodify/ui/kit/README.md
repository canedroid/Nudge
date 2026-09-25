# glass_ui_without_edges — Observer-style HUD kit (reference, no edge glow)

A self-contained PyQt6 kit for building translucent glass HUD apps like
*Observer*. Nothing in this repo imports it — the whole folder is designed to
be copied into your project as-is.

This variant has **no drop-shadow glow / no transparent halo** around the
window edges — the shell is sized exactly to the content.

## Quick start

```bash
# from packages/
python -m glass_ui_without_edges.demo        # run the reference HUD with fake data
```

The demo mounts a `HudWindow` (sidebar + five screens), a tray icon and the
settings slider dialog.

## Copying into your app

```python
from glass_ui_without_edges import colors, fonts, painting, glow, opacity
from glass_ui_without_edges.glass import GlassShell
from glass_ui_without_edges.window import HudWindow
from glass_ui_without_edges import screens
```

A dialog is three lines:

```python
from glass_ui_without_edges.glass import GlassShell
from glass_ui_without_edges import colors

class MyDialog(GlassShell):
    def __init__(self):
        super().__init__(360, 220)   # width, height — no glow, no halo
        self.mount(MyCard())
```

The main window follows the `HudWindow` pattern: a painted glass card holds a
sidebar + `QStackedWidget`. There is no glow effect and no transparent halo —
the window hugs the card exactly.

## Design tokens

| Token              | Value     | Use |
|--------------------|-----------|-----|
| `PURPLE`           | `#c9c9c9` | muted borders / progress fill |
| `PURPLE_SOFT`      | `#3b3b3b` | hover / selection |
| `PURPLE_GLOW`      | `#ececec` | borders, headers |
| `BG_GLASS`         | `#0e0e0e` | deepest background |
| `BG_GLASS_STRONG`  | `#1c1c1c` | cards, menus |
| `TEXT`             | `#f5f5f5` | primary text |
| `TEXT_DIM`         | `#a8a8a8` | secondary text |
| Headers            | Bahnschrift, demi-bold, `header_font()` |
| Body               | Segoe UI, `body_font()` |

Cards are painted with `painting.paint_card_bg()` — vertical gradient
`#262626 → #161616`, 1px `PURPLE_GLOW` border, thin top accent line.

## Screen data contract

`HudWindow` expects a context object exposing:

```python
snapshot()      # dict        -> Now
day()           # dict        -> Today
period(days)    # dict        -> History
habits()        # list[dict]  -> Habits
suggestions()   # list        -> Suggestions
quit()          # -> None     (tray Quit)
```

The pure shapers (`screens.now_screen_data`, `today_screen_data`, …) only
read plain keys, so your model layer plugs straight in.

## Module map

| Module | Contents |
|--------|----------|
| `colors`   | palette + font-family constants |
| `fonts`    | `header_font`, `body_font`, `font_families` |
| `buttons`  | `button_qss` |
| `inputs`   | `input_qss` (line/date/time edits) |
| `menus`    | `menu_qss` (tray/popup menus) |
| `scrollbar`| `scrollbar_qss` (lists + scrollbars) |
| `painting` | `paint_card_bg` |
| `glow`     | `apply_glow` (kept as a helper; not used by the windows) |
| `opacity`  | live window-opacity system (`set_opacity`) |
| `glass`    | `GlassShell` (no halo, no glow) |
| `window`   | `HudWindow` (sidebar + stacked screens) |
| `screens`  | five dashboard presenters + pure data shapers |
| `settings_window` | glass transparency dialog |
| `tray`     | `TrayIcon` (runtime-drawn icon + menu) |
| `theme`    | one-import facade over the whole kit |