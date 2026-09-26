"""Tests for the Windows backdrop fallbacks.

The interesting behaviour is not "does blur appear", which is a property of the
machine and is settled by ``nodify.backdrop_probe``. It is the fallback ladder:
which mechanism is tried, in what order, and what happens when one of them is
missing or refuses. Getting that wrong produces a window that looks identical to
"implemented but invisible", so it is worth pinning.

Every test here is offline. The real functions are replaced with recorders, so the
suite is deterministic on any machine and proves the ordering without depending on
a particular Windows build.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Iterator
from ctypes import wintypes

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from nodify.ui import win_backdrop
from nodify.ui.win_backdrop import (
    _ACCENT_DISABLED,
    _ACCENT_ENABLE_ACRYLICBLURBEHIND,
    _ACCENT_ENABLE_BLURBEHIND,
    _ACCENT_ENABLE_HOSTBACKDROP,
    _WCA_ACCENT_POLICY,
    Backdrop,
    _AccentPolicy,
    _candidates_for,
    _CompositionAttributeData,
    apply,
)


@pytest.fixture
def widget(qapp: QApplication) -> Iterator[QWidget]:
    """A real widget, so ``winId()`` and the handle plumbing are genuine.

    Only the Windows calls are faked. Testing against a stub object with a stub
    ``winId`` would pass while the real path was broken.
    """
    plain = QWidget()
    try:
        yield plain
    finally:
        plain.close()
        plain.deleteLater()


class TestCandidateOrder:
    def test_none_asks_for_nothing_and_turns_it_off(self) -> None:
        """One entry, and it disables, because the setting is sticky per handle."""
        candidates = _candidates_for(Backdrop.NONE)

        assert len(candidates) == 1
        assert candidates[0][0] == "disabled"

    def test_the_named_effect_is_tried_before_the_fallback(self) -> None:
        """A machine that can do better should be allowed to do better."""
        names = [name for name, _ in _candidates_for(Backdrop.ACRYLIC)]

        assert names[0] == "accent acrylic"
        assert "accent blur fallback" in names
        assert "dwm system backdrop" in names

    def test_mica_asks_for_a_main_window_backdrop(self) -> None:
        """Mica is only correct for a titled frame; acrylic is not.

        Sending Mica's value to a tool window shows nothing at all, which is the
        failure this distinction prevents.
        """
        mica = _candidates_for(Backdrop.MICA)
        acrylic = _candidates_for(Backdrop.ACRYLIC)

        assert mica[0][0] == "accent mica"
        assert acrylic[0][0] == "accent acrylic"
        assert acrylic[0][0] != mica[0][0]

    def test_every_glassy_backdrop_keeps_a_plain_blur_fallback(self) -> None:
        """Plain blur is the only mechanism measured to work on a layered window.

        Every option must be able to reach it, or a machine that supports nothing
        else ends up with a flat tint and no explanation.
        """
        for backdrop in (Backdrop.BLUR, Backdrop.ACRYLIC, Backdrop.MICA):
            names = [name for name, _ in _candidates_for(backdrop)]
            assert "accent blur fallback" in names, f"{backdrop} has no blur fallback"


class TestMechanismCalls:
    """The exact Win32 values, because they are not guessable from the name."""

    def test_acrylic_uses_state_four(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[tuple[int, int, int]] = []

        def fake(hwnd: int, attribute: int, value: ctypes.Structure, size: int) -> bool:
            assert isinstance(value, _AccentPolicy)
            seen.append((attribute, value.state, size))
            return True

        monkeypatch.setattr(win_backdrop, "_set_composition_attribute", fake)

        assert _candidates_for(Backdrop.ACRYLIC)[0][1](123) is True
        assert seen == [(_WCA_ACCENT_POLICY, _ACCENT_ENABLE_ACRYLICBLURBEHIND, 16)]

    def test_blur_uses_state_three(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[int] = []
        monkeypatch.setattr(
            win_backdrop,
            "_set_composition_attribute",
            lambda _h, _a, value, _s: seen.append(value.state) or True,  # type: ignore[attr-defined]
        )

        _candidates_for(Backdrop.BLUR)[0][1](1)

        assert seen == [_ACCENT_ENABLE_BLURBEHIND]

    def test_mica_uses_host_backdrop_state_five(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[int] = []
        monkeypatch.setattr(
            win_backdrop,
            "_set_composition_attribute",
            lambda _h, _a, value, _s: seen.append(value.state) or True,  # type: ignore[attr-defined]
        )

        _candidates_for(Backdrop.MICA)[0][1](1)

        assert seen == [_ACCENT_ENABLE_HOSTBACKDROP]

    def test_disabling_uses_state_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[int] = []
        monkeypatch.setattr(
            win_backdrop,
            "_set_composition_attribute",
            lambda _h, _a, value, _s: seen.append(value.state) or True,  # type: ignore[attr-defined]
        )

        _candidates_for(Backdrop.NONE)[0][1](1)

        assert seen == [_ACCENT_DISABLED]

    def test_a_missing_api_is_a_refusal_not_a_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A future user32 without the undocumented call must be survivable.

        The whole reason the function is resolved by name on every call is this.
        """

        class Empty:
            def __getattr__(self, _name: str) -> None:
                raise AttributeError("SetWindowCompositionAttribute")

        monkeypatch.setattr(win_backdrop, "_load_user32", lambda: Empty())

        assert win_backdrop._set_composition_attribute(1, 19, _AccentPolicy(), 16) is False


class TestStructLayout:
    def test_the_accent_policy_is_four_dwords(self) -> None:
        assert ctypes.sizeof(_AccentPolicy) == 16

    def test_the_composition_argument_carries_a_pointer_and_a_size(self) -> None:
        """A word where a pointer belongs corrupts the call rather than failing it.

        ``DWORD`` stays 32-bit on 64-bit Windows, so the mixed widths are the
        point rather than an accident. The declared field types are checked
        instead of ``sizeof`` on a value, because a Python ``int`` stored in a
        ``c_size_t`` field is still a 4-byte Python int and would measure the
        wrong thing.
        """
        fields = dict(_CompositionAttributeData._fields_)

        assert fields["attribute"] is wintypes.DWORD
        assert fields["data"] is ctypes.c_void_p
        assert fields["size_of_data"] is ctypes.c_size_t
        assert ctypes.sizeof(_CompositionAttributeData) == 24
        assert ctypes.sizeof(ctypes.c_void_p) == 8
        assert ctypes.sizeof(ctypes.c_size_t) == 8


class TestApply:
    def test_a_refused_everywhere_returns_none(
        self, widget: QWidget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The tiles then fall back to a plain tint, which is a cosmetic loss.

        Returning ``None`` rather than raising is what keeps a window that cannot
        do glass still usable.
        """
        monkeypatch.setattr(win_backdrop, "_candidates_for", lambda _b: (("x", lambda _h: False),))

        assert apply(widget, Backdrop.ACRYLIC) is None

    def test_the_first_accepted_mechanism_wins(
        self, widget: QWidget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tried: list[str] = []

        def make(name: str, result: bool) -> tuple[str, Callable[[int], bool]]:
            def mechanism(_hwnd: int) -> bool:
                tried.append(name)
                return result

            return (name, mechanism)

        monkeypatch.setattr(
            win_backdrop,
            "_candidates_for",
            lambda _b: (
                make("accent acrylic", False),
                make("accent blur fallback", True),
                make("dwm system backdrop", True),
            ),
        )

        assert apply(widget, Backdrop.ACRYLIC) == "accent blur fallback"
        assert tried == ["accent acrylic", "accent blur fallback"], (
            "it kept trying after a mechanism was accepted"
        )

    def test_the_real_handle_is_used(
        self, widget: QWidget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[int] = []
        monkeypatch.setattr(
            win_backdrop, "_candidates_for", lambda _b: (("x", lambda h: seen.append(h) or True),)
        )

        apply(widget, Backdrop.ACRYLIC)

        assert seen and seen[0] == int(widget.winId())

    def test_a_widget_with_no_handle_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zero means Qt has not made a native window yet; there is nothing to
        attach an effect to, and guessing would apply it to the wrong window."""

        class Handleless:
            def winId(self) -> int:  # noqa: N802 (Qt naming)
                return 0

        monkeypatch.setattr(win_backdrop, "_candidates_for", lambda _b: (("x", lambda _h: True),))

        assert apply(Handleless(), Backdrop.ACRYLIC) is None  # type: ignore[arg-type]
