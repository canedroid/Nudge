"""Application settings: where they live, and how they are kept sane.

Settings live in one JSON file under the OS config directory. Two decisions are
worth stating.

**A malformed file is recovered, not destroyed.** On a parse failure the file is
renamed to ``settings.recovered.json`` and defaults are used. Overwriting it would
throw away whatever the user had configured, and refusing to start would leave
them with no application at all because of a stray comma.

**Values are clamped on read, not trusted.** A negative opacity, a zero width or
a hotkey with no modifier are all reachable by hand-editing the file, and each
would produce a broken or unsafe window. Every value is validated at the boundary
so no consumer has to defend itself.

A hand-edited ``config.toml`` layered on top of this file is **planned but not
built**. Until it exists, ``settings.json`` is the only source, and editing it by
hand is the override mechanism. This docstring previously pointed at
:mod:`nodify.services.config`, a module that was never written, which would have
sent the next person looking for a file that does not exist.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Final

from nodify.services.accelerator import Accelerator, InvalidAccelerator, default_accelerator

SETTINGS_FILENAME: Final = "settings.json"
RECOVERED_FILENAME: Final = "settings.recovered.json"

#: Bounds. The opacity floor leaves the window faintly visible rather than
#: invisible, which would be indistinguishable from the app having crashed.
MIN_OPACITY: Final = 0.15
MAX_OPACITY: Final = 1.0
MIN_DIMENSION: Final = 160
MAX_DIMENSION: Final = 20000

VALID_BACKDROPS: Final = frozenset({"blur", "acrylic", "mica", "none"})

DEFAULT_HOTKEY: Final = default_accelerator()
DEFAULT_VAULT_PATH: Final = ""


class SettingsError(Exception):
    """Settings could not be read or written."""


@dataclass(frozen=True, slots=True)
class TileGeometry:
    """A tile's position and size, in screen-independent pixels."""

    x: int = 0
    y: int = 0
    width: int = 520
    height: int = 640

    def clamped(self) -> TileGeometry:
        """Clamp the size to something a window can actually have.

        A stored size of zero, or of a million, would make a window that cannot be
        shown or cannot be dismissed.
        """
        return TileGeometry(
            x=int(self.x),
            y=int(self.y),
            width=_clamp_int(self.width, MIN_DIMENSION, MAX_DIMENSION),
            height=_clamp_int(self.height, MIN_DIMENSION, MAX_DIMENSION),
        )


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Everything the application remembers between runs."""

    hotkey: str = DEFAULT_HOTKEY
    backdrop: str = "none"
    opacity: float = 0.75
    click_through: bool = True
    vault_path: str = DEFAULT_VAULT_PATH
    layout: tuple[TileGeometry, ...] = ()

    def __post_init__(self) -> None:
        # Frozen, so validation replaces the whole value rather than assigning.
        object.__setattr__(self, "hotkey", _clean_hotkey(self.hotkey))
        object.__setattr__(self, "backdrop", _clean_backdrop(self.backdrop))
        object.__setattr__(self, "opacity", _clean_opacity(self.opacity))
        object.__setattr__(self, "click_through", bool(self.click_through))
        object.__setattr__(self, "vault_path", str(self.vault_path or ""))
        object.__setattr__(self, "layout", tuple(t.clamped() for t in self.layout))

    def with_vault_path(self, path: str) -> AppSettings:
        """A copy pointing at a different vault.

        Returns a new value rather than mutating, matching ``with_tile``, so a
        caller holding the old settings is unaffected by the change.
        """
        return replace(self, vault_path=str(path or ""))

    def with_tile(self, index: int, geometry: TileGeometry) -> AppSettings:
        """A copy with one tile's geometry replaced.

        Returns a new settings object rather than mutating, so a caller holding
        the old value is unaffected.
        """
        tiles = list(self.layout)
        while len(tiles) <= index:
            tiles.append(TileGeometry())
        tiles[index] = geometry.clamped()
        return replace(self, layout=tuple(tiles))

    def tile(self, index: int) -> TileGeometry:
        """The geometry for a tile, or a default when it is not stored yet."""
        if 0 <= index < len(self.layout):
            return self.layout[index]
        return TileGeometry()


@dataclass(frozen=True, slots=True)
class SettingsOutcome:
    """The result of a load, including whether a bad file was recovered."""

    settings: AppSettings
    recovered_from: Path | None = None
    used_defaults: bool = False


def config_directory() -> Path:
    """Where per-user application configuration lives on this platform.

    On Windows this is ``%APPDATA%``. The environment variable is used rather than
    a hard-coded path so the application follows a relocated profile.
    """
    override = os.environ.get("NODIFY_CONFIG_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "Nodify"
    return Path.home() / ".config" / "nodify"


def settings_path(directory: Path | None = None) -> Path:
    return (directory or config_directory()) / SETTINGS_FILENAME


def recovered_path(directory: Path | None = None) -> Path:
    return (directory or config_directory()) / RECOVERED_FILENAME


def _clamp_int(value: Any, low: int, high: int, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clean_opacity(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.75
    if number != number:  # NaN
        return 0.75
    return max(MIN_OPACITY, min(MAX_OPACITY, number))


def _clean_backdrop(value: Any) -> str:
    text = str(value or "none").strip().lower()
    return text if text in VALID_BACKDROPS else "none"


def _clean_hotkey(value: Any) -> str:
    """Canonicalise a hotkey, falling back to the default if it is unusable.

    A hotkey without a modifier is refused outright: a bare key would be swallowed
    by the whole desktop while the overlay is registered.
    """
    try:
        return str(Accelerator.parse(str(value)))
    except InvalidAccelerator:
        return DEFAULT_HOTKEY


def _parse_geometry(raw: Any) -> TileGeometry:
    if not isinstance(raw, dict):
        return TileGeometry()
    return TileGeometry(
        x=_clamp_int(raw.get("x", 0), -MAX_DIMENSION, MAX_DIMENSION),
        y=_clamp_int(raw.get("y", 0), -MAX_DIMENSION, MAX_DIMENSION),
        width=_clamp_int(raw.get("width", 520), MIN_DIMENSION, MAX_DIMENSION),
        height=_clamp_int(raw.get("height", 640), MIN_DIMENSION, MAX_DIMENSION),
    ).clamped()


def settings_from_dict(raw: Any) -> AppSettings:
    """Build settings from a parsed mapping, clamping everything.

    Never raises. A caller reading a hand-edited file should get usable settings
    and, if something was wrong, the clamp that was applied.
    """
    if not isinstance(raw, dict):
        return AppSettings()

    layout: list[TileGeometry] = []
    raw_layout = raw.get("layout")
    if isinstance(raw_layout, list):
        for entry in raw_layout[:16]:
            layout.append(_parse_geometry(entry))

    opacity = _clean_opacity(raw.get("opacity", 0.75))
    return AppSettings(
        hotkey=_clean_hotkey(raw.get("hotkey", DEFAULT_HOTKEY)),
        backdrop=_clean_backdrop(raw.get("backdrop")),
        opacity=opacity,
        click_through=bool(raw.get("click_through", True)),
        vault_path=str(raw.get("vault_path") or ""),
        layout=tuple(layout),
    )


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    """A JSON-serialisable mapping, with a version for future migrations."""
    return {
        "version": 1,
        "hotkey": settings.hotkey,
        "backdrop": settings.backdrop,
        "opacity": settings.opacity,
        "click_through": settings.click_through,
        "vault_path": settings.vault_path,
        "layout": [asdict(tile) for tile in settings.layout],
    }


def load_settings(directory: Path | None = None) -> SettingsOutcome:
    """Read the settings file, recovering it if it cannot be parsed.

    A missing file is not a failure: it is a first run, and defaults apply.
    """
    path = settings_path(directory)
    if not path.is_file():
        return SettingsOutcome(settings=AppSettings(), used_defaults=True)

    try:
        # utf-8-sig, not utf-8. Notepad on Windows writes a byte order mark by
        # default, and so does PowerShell's ``Set-Content -Encoding utf8``. Plain
        # utf-8 decoding leaves the mark in the text, ``json.loads`` rejects it,
        # and the file is treated as malformed: the user hand-edits their settings,
        # saves, and silently loses their vault, hotkey and layout. utf-8-sig
        # strips a mark when there is one and is identical to utf-8 when there is
        # not.
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return SettingsOutcome(settings=AppSettings(), used_defaults=True)

    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return _recover(path, directory)

    return SettingsOutcome(settings=settings_from_dict(raw))


def _recover(path: Path, directory: Path | None) -> SettingsOutcome:
    """Move a malformed file aside and continue with defaults.

    The file is moved rather than deleted, so a user who hand-edited it badly can
    still find what they wrote and fix the mistake.
    """
    target = recovered_path(directory)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # A second bad file on the same run must not overwrite the first, or the
        # user's last good attempt would be lost too.
        if target.exists():
            index = 1
            while (target.with_name(f"{RECOVERED_FILENAME}.{index}")).exists():
                index += 1
            target = target.with_name(f"{RECOVERED_FILENAME}.{index}")
        path.replace(target)
    except OSError:
        # If the move fails, carrying on with defaults is still better than
        # refusing to start.
        return SettingsOutcome(settings=AppSettings(), used_defaults=True)

    return SettingsOutcome(settings=AppSettings(), recovered_from=target, used_defaults=True)


def save_settings(settings: AppSettings, directory: Path | None = None) -> Path:
    """Write settings atomically, creating the directory if needed."""
    path = settings_path(directory)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings_to_dict(settings), indent=2, sort_keys=True)
    except OSError as exc:
        raise SettingsError(f"cannot write settings: {exc}") from exc

    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SettingsError(f"cannot write settings: {exc}") from exc
    return path


def load_or_create(
    directory: Path | None = None, defaults: AppSettings | None = None
) -> SettingsOutcome:
    """Load settings, writing the defaults out on a first run.

    Writing them out means the user has a real file to edit, which matters for the
    hand-editing workflow the config layer is built on. The defaults are written
    whether or not they were passed in, because "there is no file yet" is exactly
    when the user most needs one to look at.
    """
    outcome = load_settings(directory)
    if not outcome.used_defaults:
        return outcome

    chosen = defaults if defaults is not None else outcome.settings
    # Not being able to write them out is not fatal: the defaults still apply for
    # this run, and the file can be created by hand.
    with contextlib.suppress(SettingsError):
        save_settings(chosen, directory)
    return SettingsOutcome(
        settings=chosen, recovered_from=outcome.recovered_from, used_defaults=True
    )


__all__ = [
    "DEFAULT_HOTKEY",
    "MAX_DIMENSION",
    "MAX_OPACITY",
    "MIN_DIMENSION",
    "MIN_OPACITY",
    "RECOVERED_FILENAME",
    "SETTINGS_FILENAME",
    "VALID_BACKDROPS",
    "AppSettings",
    "SettingsError",
    "SettingsOutcome",
    "TileGeometry",
    "config_directory",
    "load_or_create",
    "load_settings",
    "recovered_path",
    "save_settings",
    "settings_from_dict",
    "settings_path",
    "settings_to_dict",
]
