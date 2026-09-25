"""glass_ui_without_edges — Reusable monochrome HUD glass UI kit (no edge glow).

Dark glass panels, Bahnschrift headers, gradient-painted cards.
No drop-shadow glow, no transparent halo margin.

This folder is a **self-contained reference** for building an Observer-style
HUD. Everything here imports only its own modules, so you can copy the whole
``glass_ui_without_edges`` folder into any project and keep working — no app
depends on it.

Kit modules (drop-in style):
    colors, fonts, buttons, inputs, menus, scrollbar, painting, glow, opacity

Observer-style app layer (copy what you need):
    window          HudWindow — sidebar + stacked screens main window
    screens         Now/Today/History/Habits/Suggestions presenters
    settings_window Glass settings dialog (transparency slider)
    tray            Runtime-drawn tray icon + menu
    theme           One-import facade over the whole kit

See it running::

    python -m glass_ui_without_edges.demo

"""