"""Keyboard accelerators: parsing, canonical form, and validation.

A hotkey is registered with the operating system and stays registered for the
lifetime of the process, so a bad one is not a cosmetic problem. Two rules follow
from that, and both are enforced here rather than at the registration site.

**A bare key is refused.** ``K`` on its own would be swallowed by the entire
desktop while the overlay holds it, and the user would have to sign out to get it
back. A modifier is not optional.

**The form is canonical.** ``ctrl+shift+k``, ``Shift+Ctrl+K`` and ``CTRL+SHIFT+K``
all mean the same thing, and they must compare equal, or rebinding a hotkey to
what is already set would appear to succeed while silently registering a second
combination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: Modifier order in the canonical string. Fixed so the form is comparable.
MODIFIER_ORDER: Final[tuple[str, ...]] = ("ctrl", "alt", "shift", "meta")

#: Accepted spellings mapped to the canonical modifier name.
_MODIFIER_ALIASES: Final[dict[str, str]] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctl": "ctrl",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "shift": "shift",
    "meta": "meta",
    "win": "meta",
    "super": "meta",
    "windows": "meta",
    "cmd": "meta",
    "command": "meta",
}

#: Key names mapped to the canonical spelling, so ``ctrl+return`` and
#: ``Ctrl+Enter`` are the same accelerator.
_KEY_ALIASES: Final[dict[str, str]] = {
    "return": "enter",
    "enter": "enter",
    "esc": "escape",
    "escape": "escape",
    "space": "space",
    "spacebar": "space",
    "del": "delete",
    "delete": "delete",
    "ins": "insert",
    "insert": "insert",
    "pgup": "pageup",
    "pageup": "pageup",
    "pgdn": "pagedown",
    "pagedown": "pagedown",
    "plus": "plus",
    "add": "plus",
    "minus": "minus",
    "subtract": "minus",
}

#: A single printable character is its own key name, upper-cased.
_SINGLE_CHAR = re.compile(r"^[A-Za-z0-9]$")

#: Punctuation that Windows accepts in a hotkey, with canonical spellings.
_PUNCTUATION: Final[dict[str, str]] = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "`": "grave",
    "~": "grave",
    "-": "minus",
    "_": "minus",
    "=": "plus",
    "{": "leftbrace",
    "[": "leftbrace",
    "}": "rightbrace",
    "]": "rightbrace",
    "\\": "backslash",
    "|": "backslash",
    ";": "semicolon",
    ":": "semicolon",
    "'": "apostrophe",
    '"': "apostrophe",
    ",": "comma",
    "<": "comma",
    ".": "period",
    ">": "period",
    "/": "slash",
    "?": "slash",
}

DEFAULT_ACCELERATOR: Final = "Ctrl+Space"


class InvalidAccelerator(ValueError):
    """The accelerator is empty, unparseable, or has no modifier."""


#: How named keys are written in the canonical text form. Named keys are stored
#: internally in lowercase so that comparison is trivial, and rendered through
#: this table, so ``Ctrl+pageup`` and ``Ctrl+PageUp`` produce the same string.
_KEY_DISPLAY: Final[dict[str, str]] = {
    "enter": "Enter",
    "escape": "Esc",
    "space": "Space",
    "delete": "Del",
    "insert": "Ins",
    "pageup": "PgUp",
    "pagedown": "PgDn",
    "home": "Home",
    "end": "End",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
    "tab": "Tab",
    "backspace": "Backspace",
    "capslock": "CapsLock",
    "print": "Print",
    "pause": "Pause",
    "menu": "Menu",
    "plus": "Plus",
    "minus": "Minus",
    "grave": "Grave",
    "backslash": "Backslash",
    "semicolon": "Semicolon",
    "apostrophe": "Apostrophe",
    "comma": "Comma",
    "period": "Period",
    "slash": "Slash",
    "leftbrace": "LeftBrace",
    "rightbrace": "RightBrace",
}


def _display_key(key: str) -> str:
    """A key name as it should be written in an accelerator string.

    ``str.capitalize`` is wrong here: it lowercases everything after the first
    character, which turns ``PageUp`` into ``Pageup`` and ``Num5`` into ``Num5``
    for the wrong reason. A canonical accelerator has to survive a round trip
    through its own text form, so named keys go through a table and only single
    characters are ever case-folded.
    """
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        return key.upper()
    if key in _KEY_DISPLAY:
        return _KEY_DISPLAY[key]
    if key.startswith("f") and key[1:].isdigit():
        return f"F{key[1:]}"
    if key.startswith("num") and key[3:].isdigit():
        return f"Num{key[3:]}"
    return key


@dataclass(frozen=True, slots=True, order=True)
class Accelerator:
    """A parsed, canonical keyboard accelerator."""

    modifiers: tuple[str, ...]
    key: str

    def __str__(self) -> str:
        """The canonical text form, e.g. ``Ctrl+Shift+K``.

        Modifiers are title-cased and the key is canonical, so two spellings of
        the same combination produce an identical string and compare equal.
        """
        parts = [m.capitalize() for m in self.modifiers]
        parts.append(_display_key(self.key))
        return "+".join(parts)

    @property
    def display(self) -> str:
        """A form suitable for showing on a chip, e.g. ``Ctrl + Shift + K``."""
        parts = [m.capitalize() for m in self.modifiers]
        parts.append(_display_key(self.key))
        return " + ".join(parts)

    def requires_modifier(self) -> bool:
        return bool(self.modifiers)

    @classmethod
    def parse(cls, text: str) -> Accelerator:
        """Parse an accelerator, raising :class:`InvalidAccelerator` if unusable."""
        if not isinstance(text, str):
            raise InvalidAccelerator("accelerator must be a string")

        raw = text.strip()
        if not raw:
            raise InvalidAccelerator("accelerator must not be empty")

        parts = [part.strip() for part in raw.split("+")]
        # A trailing "+" leaves an empty final part, which means the key itself is
        # a plus sign. Splitting on "+" cannot express that, so it is rejected
        # rather than silently misinterpreted as "plus with no key".
        if any(not part for part in parts):
            raise InvalidAccelerator(f"malformed accelerator: {text!r}")

        modifiers: set[str] = set()
        key: str | None = None

        for index, part in enumerate(parts):
            lowered = part.lower()
            is_last = index == len(parts) - 1

            if lowered in _MODIFIER_ALIASES:
                if is_last:
                    raise InvalidAccelerator(
                        f"accelerator must end with a key, not a modifier: {text!r}"
                    )
                modifiers.add(_MODIFIER_ALIASES[lowered])
                continue

            if key is not None:
                raise InvalidAccelerator(f"accelerator names two keys: {text!r}")

            key = _canonical_key(part)
            if key is None:
                raise InvalidAccelerator(f"unrecognised key: {part!r}")

        if key is None:
            raise InvalidAccelerator(f"accelerator names no key: {text!r}")

        if not modifiers:
            raise InvalidAccelerator(f"a global hotkey needs a modifier: {text!r}")

        ordered = tuple(m for m in MODIFIER_ORDER if m in modifiers)
        return cls(modifiers=ordered, key=key)


def _canonical_key(part: str) -> str | None:
    """The canonical name for a key, or ``None`` if it is not recognised.

    Accepts the display spellings as well as the raw characters, because the
    canonical output of :meth:`Accelerator.__str__` must be parseable again. That
    round trip is what lets a hotkey read from settings be compared against one
    bound in this session.
    """
    lowered = part.lower()

    if _SINGLE_CHAR.match(lowered):
        return lowered.upper()

    if lowered in _PUNCTUATION:
        return _PUNCTUATION[lowered]

    if lowered in _KEY_ALIASES:
        return _KEY_ALIASES[lowered]

    if lowered in _KEY_DISPLAY:
        # The display spelling of a named key, e.g. "PgUp" or "Backslash".
        return lowered

    if lowered.startswith("f") and lowered[1:].isdigit():
        number = int(lowered[1:])
        if 1 <= number <= 24:
            return f"f{number}"

    if lowered.startswith("num") and lowered[3:].isdigit():
        number = int(lowered[3:])
        if 0 <= number <= 9:
            return f"num{number}"

    return None


def canonicalise(text: str) -> str:
    """Parse and return the canonical text form.

    Raises :class:`InvalidAccelerator` rather than returning a fallback, because
    the caller needs to tell the user their hotkey was refused instead of
    silently giving them a different one.
    """
    return str(Accelerator.parse(text))


def is_valid(text: str) -> bool:
    """Whether an accelerator can be registered."""
    try:
        Accelerator.parse(text)
    except InvalidAccelerator:
        return False
    return True


def to_qt_sequence(text: str) -> str:
    """Convert to the form Qt's ``QKeySequence`` expects.

    Qt spells modifiers ``Ctrl``, ``Alt``, ``Shift``, ``Meta`` and separators with
    ``+``, which matches the canonical form closely enough that most accelerators
    pass through unchanged. ``Meta`` has to be spelled ``Meta`` rather than
    ``Super`` or ``Win``, and the numeric keypad has its own ``Num`` prefix.
    """
    accelerator = Accelerator.parse(text)
    parts: list[str] = [
        {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "meta": "Meta"}[modifier]
        for modifier in accelerator.modifiers
    ]
    # The canonical key is already in the form Qt wants, once rendered through the
    # shared display helper. Deriving it in one place keeps the two spellings from
    # drifting apart.
    parts.append(_display_key(accelerator.key))
    return "+".join(parts)


def default_accelerator() -> str:
    """The default hotkey."""
    return DEFAULT_ACCELERATOR


__all__ = [
    "DEFAULT_ACCELERATOR",
    "MODIFIER_ORDER",
    "Accelerator",
    "InvalidAccelerator",
    "canonicalise",
    "default_accelerator",
    "is_valid",
    "to_qt_sequence",
]
