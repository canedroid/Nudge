"""Windows backdrop effects: real blur behind the tile windows.

The tiles have to be genuinely see-through. A translucent tint alone only dims
whatever is behind it, which reads as flat black over a dark desktop, and the
project's own stylesheet comment says as much: "Opaque colours would punch a hole
in the glass effect the overlay is built around." Real glass needs the desktop
itself blurred, and on Windows that blur belongs to the compositor, not to Qt.
Qt can fake it by screenshotting the desktop and painting the picture back, but a
screenshot is a stale snapshot, costs a full-screen capture per tile per refresh,
and lags whatever moves behind it. The compositor's version is free, live, and
correct, so this module asks for it.

**Why a module and not a call site.** Two unrelated Windows APIs can produce
this effect, they were introduced a decade apart, and neither is a stable public
contract for this use:

``SetWindowCompositionAttribute`` with ``WCA_ACCENT_POLICY`` is undocumented.
It has shipped unchanged since Windows 8, it is what the community's translucent
taskbar tools use, and it is the only one known to composite on a layered
window. It can be missing from a future ``user32``, so its absence is treated as
"no effect", never as a crash.

``DwmSetWindowAttribute`` with ``DWMWA_SYSTEMBACKDROP_TYPE`` is the supported
Win11 22H2+ route and is the only one Microsoft documents. It is also reported
not to composite while ``WS_EX_LAYERED`` is set, which is precisely what Qt sets
for a translucent widget, so it is attempted but not relied upon.

Both are therefore tried, most-likely-first, and the first one that actually
changes the pixels is the one that gets used. :func:`probe` settles that
empirically for the machine in front of us rather than trusting a comment on the
internet, because the two differ per Windows build and getting it wrong looks
identical to "implemented but invisible".

**Applied in ``showEvent``, never in ``__init__``.** The handle a widget draws
into does not exist until the native window is created, and Qt may recreate it,
so a backdrop applied once at construction is silently lost. Callers pass the
handle in from ``showEvent`` for exactly that reason.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes
from enum import StrEnum
from typing import Protocol

from PyQt6.QtWidgets import QWidget

if sys.platform != "win32":  # pragma: no cover - the product is Windows-only
    raise ImportError("nodify.ui.win_backdrop is Windows-only")


class Backdrop(StrEnum):
    """Which effect the compositor should apply behind a window."""

    NONE = "none"
    BLUR = "blur"
    ACRYLIC = "acrylic"
    MICA = "mica"


#: ``WCA_ACCENT_POLICY``: the undocumented composition attribute that carries an
#: ``ACCENT_POLICY`` struct.
_WCA_ACCENT_POLICY = 19

#: ``AccentState`` values. Only the two that blur are named; the gradient states
#: are ignored because they tint without blurring, which is not what is wanted.
_ACCENT_ENABLE_BLURBEHIND = 3
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_ACCENT_ENABLE_HOSTBACKDROP = 5
_ACCENT_ENABLE_ADVANCEDHOSTBACKDROP = 6

#: ``DWMWA_SYSTEMBACKDROP_TYPE``, Win11 22H2 and later.
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_TRANSIENTWINDOW = 3  # Acrylic, for a window rather than a main frame
_DWMSBT_MAINWINDOW = 2  # Mica, only correct for a window with a title bar

#: ``ACCENT_POLICY.AccentState``. Kept distinct from the ``_ACCENT_`` constants
#: above so the struct reads in the order the Win32 header declares.
_ACCENT_DISABLED = 0


class _AccentPolicy(ctypes.Structure):
    """``ACCENT_POLICY``: which effect, and the tint to apply over it."""

    _fields_ = [
        ("state", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("gradient_color", wintypes.DWORD),
        ("animation_id", wintypes.DWORD),
    ]


class _CompositionAttributeData(ctypes.Structure):
    """``WINDOWCOMPOSITIONATTRIBDATA``: the argument to the undocumented call.

    ``DWORD`` is 32-bit even on 64-bit Windows while the pointers and the size
    are 64-bit, which is why the field types are not all the same. Getting this
    wrong writes a struct the size of a pointer into a field expecting a word.
    """

    _fields_ = [
        ("attribute", wintypes.DWORD),
        ("data", ctypes.c_void_p),
        ("size_of_data", ctypes.c_size_t),
    ]


def _load_user32() -> ctypes.CDLL | None:
    return ctypes.WinDLL("user32", use_last_error=True)


def _load_dwmapi() -> ctypes.CDLL | None:
    try:
        return ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:  # pragma: no cover - present on every supported Windows
        return None


def _set_composition_attribute(
    hwnd: int, attribute: int, value: ctypes.Structure, size: int
) -> bool:
    """Call the undocumented ``SetWindowCompositionAttribute``.

    Returns whether the call was accepted. The function is resolved by name on
    every use rather than cached, because a ``user32`` without it raises
    ``AttributeError`` and that has to be survivable rather than fatal.
    """
    user32 = _load_user32()
    if user32 is None:  # pragma: no cover - user32 always exists
        return False
    function = getattr(user32, "SetWindowCompositionAttribute", None)
    if function is None:
        return False

    function.argtypes = [wintypes.HWND, ctypes.POINTER(_CompositionAttributeData)]
    function.restype = wintypes.BOOL

    data = _CompositionAttributeData(
        attribute=attribute,
        data=ctypes.cast(ctypes.pointer(value), ctypes.c_void_p),
        size_of_data=size,
    )
    return bool(function(wintypes.HWND(hwnd), ctypes.byref(data)))


def _apply_accent(hwnd: int, state: int, tint: int) -> bool:
    """Apply an ``ACCENT_POLICY`` with a zero animation id.

    ``gradient_color`` is ignored for the blur states on most builds, so it is
    passed as opaque black rather than guessed at; the window's own translucent
    paint is what supplies the tint, which keeps one source of truth for colour
    instead of two that can disagree.
    """
    policy = _AccentPolicy(
        state=state,
        flags=0,
        gradient_color=tint,
        animation_id=0,
    )
    return _set_composition_attribute(hwnd, _WCA_ACCENT_POLICY, policy, ctypes.sizeof(policy))


def _apply_system_backdrop(hwnd: int, kind: int) -> bool:
    """Ask DWM for Mica or Acrylic, the documented Win11 22H2+ route."""
    dwmapi = _load_dwmapi()
    if dwmapi is None:  # pragma: no cover
        return False
    function = dwmapi.DwmSetWindowAttribute
    function.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    function.restype = ctypes.c_long  # HRESULT

    value = wintypes.DWORD(kind)
    result = function(
        wintypes.HWND(hwnd),
        wintypes.DWORD(_DWMWA_SYSTEMBACKDROP_TYPE),
        ctypes.byref(value),
        ctypes.sizeof(value),
    )
    return result == 0


def _apply_disabled(hwnd: int) -> bool:
    """Explicitly turn any backdrop off.

    Needed because these settings are per-window and sticky: a recycled handle
    that had a backdrop applied keeps it, so turning it off is a real operation
    rather than a no-op.
    """
    return _apply_accent(hwnd, _ACCENT_DISABLED, 0)


def _candidates_for(backdrop: Backdrop) -> tuple[tuple[str, Callable[[int], bool]], ...]:
    """The mechanisms to try for ``backdrop``, most preferred first.

    Exposed so that :mod:`nodify.backdrop_probe` measures the same list that
    :func:`apply` will use. A probe that tested its own guesses could report a
    working mechanism that the application never calls.

    Plain blur is listed first for every backdrop on purpose. It is the one
    mechanism measured to work on a layered Qt window on Windows 11 25H2, while
    both of the nicer-looking APIs were measured to do nothing there. On a build
    where the acrylic states do work they are still tried first, so a machine
    that can do better gets better.
    """
    if backdrop is Backdrop.NONE:
        return (("disabled", _apply_disabled),)

    accent: Callable[[int], bool] = {
        Backdrop.BLUR: lambda hwnd: _apply_accent(hwnd, _ACCENT_ENABLE_BLURBEHIND, 0),
        Backdrop.ACRYLIC: lambda hwnd: _apply_accent(hwnd, _ACCENT_ENABLE_ACRYLICBLURBEHIND, 0),
        Backdrop.MICA: lambda hwnd: _apply_accent(hwnd, _ACCENT_ENABLE_HOSTBACKDROP, 0),
    }[backdrop]

    return (
        (f"accent {backdrop.value}", accent),
        (
            "accent blur fallback",
            lambda hwnd: _apply_accent(hwnd, _ACCENT_ENABLE_BLURBEHIND, 0),
        ),
        (
            "dwm system backdrop",
            lambda hwnd: _apply_system_backdrop(
                hwnd, _DWMSBT_MAINWINDOW if backdrop is Backdrop.MICA else _DWMSBT_TRANSIENTWINDOW
            ),
        ),
    )


class BackdropTarget(Protocol):
    """The part of a widget this module needs.

    ``winId`` is declared as returning an ``int`` because that is what the code
    actually does with it. Qt's own stubs say ``voidptr``, so a real widget does
    not structurally satisfy this and :func:`apply` takes a ``QWidget`` directly
    instead. The protocol stays as the statement of the requirement.
    """

    def winId(self) -> int: ...  # noqa: N802 (Qt naming)


def apply(widget: QWidget, backdrop: Backdrop) -> str | None:
    """Apply ``backdrop`` to ``widget``'s native window.

    Returns the name of the mechanism that was accepted, or ``None`` if none
    was. ``None`` means the tiles fall back to a plain translucent tint, which is
    still see-through, so a refusal is a cosmetic loss and not a failure.
    """
    if sys.platform != "win32":  # pragma: no cover
        return None
    try:
        hwnd = int(widget.winId())
    except (AttributeError, TypeError, ValueError):
        return None
    if hwnd == 0:
        # winId() forces native handle creation, so a zero here means the widget
        # has no native window yet and there is nothing to attach an effect to.
        return None

    for name, mechanism in _candidates_for(backdrop):
        if mechanism(hwnd):
            return name
    return None


__all__ = ["Backdrop", "BackdropTarget", "apply"]
