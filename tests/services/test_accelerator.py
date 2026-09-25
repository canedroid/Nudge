"""Accelerator parsing and canonical form."""

from __future__ import annotations

import pytest

from nodify.services.accelerator import (
    DEFAULT_ACCELERATOR,
    Accelerator,
    InvalidAccelerator,
    canonicalise,
    is_valid,
    to_qt_sequence,
)


class TestCanonicalForm:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Ctrl+Shift+K", "Ctrl+Shift+K"),
            ("ctrl+shift+k", "Ctrl+Shift+K"),
            ("CTRL+SHIFT+K", "Ctrl+Shift+K"),
            ("shift+ctrl+k", "Ctrl+Shift+K"),
            ("Ctrl+Alt+Shift+Meta+K", "Ctrl+Alt+Shift+Meta+K"),
        ],
    )
    def test_modifier_order_and_case_are_canonical(self, text: str, expected: str) -> None:
        """Two spellings of the same key must produce an identical string.

        Otherwise rebinding to what is already set would look successful while
        registering a second, different combination.
        """
        assert canonicalise(text) == expected

    def test_modifier_order_is_fixed(self) -> None:
        assert canonicalise("Meta+Alt+Shift+Ctrl+J") == "Ctrl+Alt+Shift+Meta+J"

    def test_equivalent_spellings_compare_equal(self) -> None:
        assert Accelerator.parse("ctrl+shift+k") == Accelerator.parse("Shift+Ctrl+K")

    def test_whitespace_is_ignored(self) -> None:
        assert canonicalise("  Ctrl + Shift + K  ") == "Ctrl+Shift+K"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Ctrl+Return", "Ctrl+Enter"),
            ("Ctrl+Esc", "Ctrl+Esc"),
            ("Ctrl+Del", "Ctrl+Del"),
            ("Ctrl+PageUp", "Ctrl+PgUp"),
            ("Ctrl+plus", "Ctrl+Plus"),
        ],
    )
    def test_key_aliases(self, text: str, expected: str) -> None:
        assert canonicalise(text) == expected

    @pytest.mark.parametrize("text", ["Ctrl+F1", "Ctrl+F12", "Ctrl+F24", "ctrl+f5"])
    def test_function_keys(self, text: str) -> None:
        assert canonicalise(text) == "Ctrl+" + text.split("+")[1].upper()

    def test_numpad_keys(self) -> None:
        assert canonicalise("Ctrl+Num5") == "Ctrl+Num5"

    def test_out_of_range_function_key_is_refused(self) -> None:
        assert not is_valid("Ctrl+F25")

    def test_out_of_range_numpad_is_refused(self) -> None:
        assert not is_valid("Ctrl+Num10")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Ctrl+!", "Ctrl+1"),
            ("Ctrl+@", "Ctrl+2"),
            ("Ctrl+?", "Ctrl+Slash"),
            ("Ctrl+/", "Ctrl+Slash"),
        ],
    )
    def test_shifted_punctuation(self, text: str, expected: str) -> None:
        assert canonicalise(text) == expected

    def test_a_bare_letter_key_is_upper_cased(self) -> None:
        assert canonicalise("Ctrl+k") == "Ctrl+K"

    def test_a_digit_key(self) -> None:
        assert canonicalise("Ctrl+7") == "Ctrl+7"


class TestRejection:
    def test_a_bare_key_is_refused(self) -> None:
        """A bare key would be swallowed by the whole desktop while registered."""
        with pytest.raises(InvalidAccelerator, match="needs a modifier"):
            Accelerator.parse("K")

    def test_a_punctuation_key_is_refused(self) -> None:
        with pytest.raises(InvalidAccelerator, match="needs a modifier"):
            Accelerator.parse("Space")

    @pytest.mark.parametrize("text", ["", "   "])
    def test_empty_is_refused(self, text: str) -> None:
        with pytest.raises(InvalidAccelerator, match="must not be empty"):
            Accelerator.parse(text)

    def test_modifiers_only_is_refused(self) -> None:
        with pytest.raises(InvalidAccelerator, match="must end with a key"):
            Accelerator.parse("Ctrl+Shift")

    def test_two_keys_is_refused(self) -> None:
        with pytest.raises(InvalidAccelerator, match="two keys"):
            Accelerator.parse("Ctrl+A+B")

    @pytest.mark.parametrize("text", ["Ctrl+", "+K", "Ctrl++"])
    def test_malformed_separators_are_refused(self, text: str) -> None:
        with pytest.raises(InvalidAccelerator):
            Accelerator.parse(text)

    def test_an_unrecognised_key_is_refused(self) -> None:
        with pytest.raises(InvalidAccelerator, match="unrecognised key"):
            Accelerator.parse("Ctrl+Banana")

    def test_a_non_string_is_refused(self) -> None:
        with pytest.raises(InvalidAccelerator, match="must be a string"):
            Accelerator.parse(None)  # type: ignore[arg-type]

    def test_duplicate_modifiers_collapse(self) -> None:
        assert canonicalise("Ctrl+Ctrl+Shift+K") == "Ctrl+Shift+K"


class TestHelpers:
    def test_is_valid(self) -> None:
        assert is_valid("Ctrl+Shift+K")
        assert not is_valid("K")

    def test_default(self) -> None:
        assert DEFAULT_ACCELERATOR == "Ctrl+Space"
        assert is_valid(DEFAULT_ACCELERATOR)

    def test_requires_modifier(self) -> None:
        assert Accelerator.parse("Ctrl+K").requires_modifier()

    def test_display_form(self) -> None:
        assert Accelerator.parse("ctrl+shift+k").display == "Ctrl + Shift + K"

    def test_accelerator_is_hashable_and_comparable(self) -> None:
        first = Accelerator.parse("Ctrl+K")
        second = Accelerator.parse("ctrl+k")
        assert first == second
        assert len({first, second}) == 1

    def test_accelerator_is_immutable(self) -> None:
        parsed = Accelerator.parse("Ctrl+K")
        with pytest.raises(AttributeError):
            parsed.key = "J"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "text",
        [
            "Ctrl+Shift+K",
            "Ctrl+Space",
            "Ctrl+Return",
            "Ctrl+PageUp",
            "Ctrl+Num5",
            "Ctrl+F12",
            "Alt+F4",
            "Win+J",
            "Ctrl+?",
            "Ctrl+Plus",
        ],
    )
    def test_canonical_form_round_trips(self, text: str) -> None:
        """Re-parsing the canonical string must give back the same accelerator.

        Without this, a hotkey read from settings could not be compared against a
        freshly bound one, and the service would register a duplicate.
        """
        once = Accelerator.parse(text)
        twice = Accelerator.parse(str(once))
        assert once == twice
        assert str(once) == str(twice)


class TestQtConversion:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Ctrl+Shift+K", "Ctrl+Shift+K"),
            ("Ctrl+Space", "Ctrl+Space"),
            ("Win+J", "Meta+J"),
            ("Alt+F4", "Alt+F4"),
            ("Ctrl+Return", "Ctrl+Enter"),
            ("Ctrl+Num5", "Ctrl+Num5"),
        ],
    )
    def test_conversion(self, text: str, expected: str) -> None:
        assert to_qt_sequence(text) == expected

    def test_conversion_refuses_an_invalid_accelerator(self) -> None:
        with pytest.raises(InvalidAccelerator):
            to_qt_sequence("K")
