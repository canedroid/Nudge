"""Settings persistence, clamping and recovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nodify.services.settings import (
    MAX_OPACITY,
    MIN_OPACITY,
    AppSettings,
    SettingsError,
    TileGeometry,
    config_directory,
    load_or_create,
    load_settings,
    recovered_path,
    save_settings,
    settings_from_dict,
    settings_path,
    settings_to_dict,
)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    directory = tmp_path / "config"
    directory.mkdir()
    return directory


def write_raw(config: Path, text: str) -> Path:
    path = settings_path(config)
    path.write_text(text, encoding="utf-8")
    return path


class TestDefaults:
    def test_a_missing_file_gives_defaults(self, config: Path) -> None:
        outcome = load_settings(config)
        assert outcome.used_defaults
        assert outcome.settings == AppSettings()
        assert outcome.recovered_from is None

    def test_default_hotkey(self) -> None:
        assert AppSettings().hotkey == "Ctrl+Space"

    def test_click_through_defaults_on(self) -> None:
        assert AppSettings().click_through is True

    def test_no_vault_by_default(self) -> None:
        assert AppSettings().vault_path == ""


class TestRoundTrip:
    def test_write_then_read(self, config: Path) -> None:
        original = AppSettings(
            hotkey="Ctrl+Shift+K",
            backdrop="acrylic",
            opacity=0.5,
            click_through=False,
            vault_path="C:/Users/x/Vault",
        )
        save_settings(original, config)
        assert load_settings(config).settings == original

    def test_layout_round_trips(self, config: Path) -> None:
        original = AppSettings(
            layout=(TileGeometry(10, 20, 300, 400), TileGeometry(0, 0, 500, 600))
        )
        save_settings(original, config)
        assert load_settings(config).settings.layout == original.layout

    def test_the_file_is_json(self, config: Path) -> None:
        save_settings(AppSettings(), config)
        raw = json.loads(settings_path(config).read_text(encoding="utf-8"))
        assert raw["version"] == 1
        assert raw["hotkey"] == "Ctrl+Space"

    def test_a_save_is_atomic_and_leaves_no_temporary(self, config: Path) -> None:
        save_settings(AppSettings(), config)
        assert sorted(p.name for p in config.iterdir()) == ["settings.json"]

    def test_a_failed_write_leaves_the_previous_file_intact(
        self, config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A write that fails must not damage the file already there."""
        save_settings(AppSettings(), config)
        original = settings_path(config).read_text(encoding="utf-8")

        def deny(*_args: object, **_kwargs: object) -> None:
            raise OSError("access denied")

        monkeypatch.setattr(Path, "write_text", deny)
        with pytest.raises(SettingsError):
            save_settings(AppSettings(hotkey="Ctrl+Shift+J"), config)

        monkeypatch.undo()
        assert settings_path(config).read_text(encoding="utf-8") == original
        # No temporary file was left behind.
        assert sorted(p.name for p in config.iterdir()) == ["settings.json"]


class TestRecovery:
    def test_malformed_json_is_moved_aside(self, config: Path) -> None:
        write_raw(config, "{ this is not json ")
        outcome = load_settings(config)

        assert outcome.used_defaults
        assert outcome.recovered_from == recovered_path(config)
        assert recovered_path(config).read_text(encoding="utf-8") == "{ this is not json "
        assert not settings_path(config).exists()

    def test_a_recovered_file_is_not_destroyed(self, config: Path) -> None:
        """The user may have hand-edited it and can still recover their work."""
        write_raw(config, "{ broken")
        load_settings(config)
        assert recovered_path(config).exists()

    def test_a_second_recovery_does_not_overwrite_the_first(self, config: Path) -> None:
        """Two bad saves must not cost the user both attempts."""
        write_raw(config, "{ first broken")
        first = load_settings(config).recovered_from
        assert first is not None

        write_raw(config, "{ second broken")
        second = load_settings(config).recovered_from
        assert second is not None
        assert second != first
        assert first.exists() and second.exists()
        assert first.read_text(encoding="utf-8") == "{ first broken"
        assert second.read_text(encoding="utf-8") == "{ second broken"

    def test_valid_json_but_wrong_shape_is_accepted(self, config: Path) -> None:
        write_raw(config, "[1, 2, 3]")
        outcome = load_settings(config)
        assert outcome.settings == AppSettings()
        assert outcome.recovered_from is None

    def test_a_byte_order_mark_does_not_discard_the_settings(self, config: Path) -> None:
        """The bug a real run found, and Notepad makes it easy to hit.

        Notepad on Windows writes a UTF-8 byte order mark by default, as does
        PowerShell's ``Set-Content -Encoding utf8``. Decoding as plain utf-8
        leaves the mark in the text, the JSON parser rejects it, and the file is
        treated as malformed: the user hand-edits their settings, saves, and
        silently loses their vault path, hotkey and layout.
        """
        payload = json.dumps(settings_to_dict(AppSettings(vault_path="C:/vault")))
        # Written as bytes, because the mark is not really a character in the
        # text and encoding it by hand is how it goes missing in a test.
        path = settings_path(config)
        path.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))

        outcome = load_settings(config)

        assert outcome.recovered_from is None, "a BOM was treated as corruption"
        assert outcome.settings.vault_path == "C:/vault"

    def test_settings_written_by_notepad_survive_a_reload(self, config: Path) -> None:
        """Round trip through a BOM-prefixed file, as a hand-edit would produce."""
        save_settings(AppSettings(hotkey="Ctrl+Shift+K", vault_path="C:/notes"), config)
        path = settings_path(config)
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

        reloaded = load_settings(config)

        assert reloaded.settings.hotkey == "Ctrl+Shift+K"
        assert reloaded.settings.vault_path == "C:/notes"


class TestClamping:
    def test_opacity_is_clamped_high(self) -> None:
        assert settings_from_dict({"opacity": 5.0}).opacity == MAX_OPACITY

    def test_opacity_is_clamped_low(self) -> None:
        assert settings_from_dict({"opacity": 0.0}).opacity == MIN_OPACITY

    def test_garbage_opacity_falls_back(self) -> None:
        assert settings_from_dict({"opacity": "lots"}).opacity == 0.75

    def test_nan_opacity_falls_back(self) -> None:
        assert settings_from_dict({"opacity": float("nan")}).opacity == 0.75

    def test_an_invalid_backdrop_falls_back(self) -> None:
        assert settings_from_dict({"backdrop": "hologram"}).backdrop == "none"

    def test_a_valid_backdrop_is_kept(self) -> None:
        assert settings_from_dict({"backdrop": "Mica"}).backdrop == "mica"

    def test_a_bare_hotkey_falls_back_to_the_default(self) -> None:
        """A hotkey with no modifier would be swallowed by the whole desktop."""
        assert settings_from_dict({"hotkey": "K"}).hotkey == "Ctrl+Space"

    def test_an_empty_hotkey_falls_back(self) -> None:
        assert settings_from_dict({"hotkey": ""}).hotkey == "Ctrl+Space"

    def test_a_valid_hotkey_is_canonicalised(self) -> None:
        assert settings_from_dict({"hotkey": "ctrl+shift+k"}).hotkey == "Ctrl+Shift+K"

    def test_tile_dimensions_are_clamped(self) -> None:
        raw = {"layout": [{"width": 0, "height": -5, "x": 0, "y": 0}]}
        tile = settings_from_dict(raw).layout[0]
        assert tile.width >= 160
        assert tile.height >= 160

    def test_an_absurd_tile_size_is_clamped(self) -> None:
        raw = {"layout": [{"width": 10_000_000, "height": 10_000_000}]}
        tile = settings_from_dict(raw).layout[0]
        assert tile.width <= 20_000
        assert tile.height <= 20_000

    def test_a_malformed_geometry_entry_becomes_a_default(self, config: Path) -> None:
        """A corrupt entry is replaced by a usable default, not dropped.

        Dropping it would shift every later tile, so a tile the user had sized
        would suddenly be the wrong size.
        """
        raw = {"layout": ["not a dict", {"width": 300, "height": 300}]}
        layout = settings_from_dict(raw).layout
        assert len(layout) == 2
        assert layout[0] == TileGeometry()
        assert layout[1] == TileGeometry(0, 0, 300, 300)

    def test_too_many_tiles_are_ignored(self) -> None:
        raw = {"layout": [{"width": 200, "height": 200}] * 40}
        assert len(settings_from_dict(raw).layout) <= 16

    def test_a_non_mapping_gives_defaults(self) -> None:
        assert settings_from_dict("nonsense") == AppSettings()

    def test_a_list_layout_is_ignored(self) -> None:
        assert settings_from_dict({"layout": "nope"}).layout == ()


class TestTileGeometry:
    def test_clamping(self) -> None:
        clamped = TileGeometry(0, 0, 10, 10).clamped()
        assert clamped.width >= 160

    def test_with_tile_replaces(self) -> None:
        settings = AppSettings().with_tile(0, TileGeometry(5, 6, 300, 400))
        assert settings.tile(0) == TileGeometry(5, 6, 300, 400)

    def test_with_tile_does_not_mutate(self) -> None:
        original = AppSettings()
        original.with_tile(0, TileGeometry(5, 6, 300, 400))
        assert original.layout == ()

    def test_tile_beyond_the_stored_range_is_a_default(self) -> None:
        assert AppSettings().tile(9) == TileGeometry()

    def test_settings_are_immutable(self) -> None:
        settings = AppSettings()
        with pytest.raises(AttributeError):
            settings.opacity = 0.1  # type: ignore[misc]


class TestLoadOrCreate:
    def test_writes_the_defaults_on_a_first_run(self, config: Path) -> None:
        defaults = AppSettings(hotkey="Ctrl+Shift+J")
        outcome = load_or_create(config, defaults)
        assert outcome.settings == defaults
        assert settings_path(config).is_file()

    def test_writes_the_defaults_even_without_them_being_passed(self, config: Path) -> None:
        """A first run must leave a file behind.

        The application calls this with no explicit defaults on startup, so the
        guard that skipped writing in that case meant the very first launch of the
        packaged application produced no settings file at all. "There is no file
        yet" is exactly when the user most needs one to look at.
        """
        outcome = load_or_create(config)
        assert settings_path(config).is_file()
        assert outcome.used_defaults
        assert outcome.settings == AppSettings()

    def test_does_not_overwrite_an_existing_file(self, config: Path) -> None:
        save_settings(AppSettings(hotkey="Ctrl+Shift+K"), config)
        outcome = load_or_create(config, AppSettings(hotkey="Ctrl+Shift+J"))
        assert outcome.settings.hotkey == "Ctrl+Shift+K"

    def test_a_recovered_file_still_gets_defaults_written(self, config: Path) -> None:
        write_raw(config, "{ broken")
        load_or_create(config, AppSettings(hotkey="Ctrl+Shift+J"))
        assert settings_path(config).is_file()
        assert load_settings(config).settings.hotkey == "Ctrl+Shift+J"


class TestConfigDirectory:
    def test_honours_the_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NODIFY_CONFIG_DIR", str(tmp_path / "custom"))
        assert config_directory() == tmp_path / "custom"

    def test_uses_appdata_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NODIFY_CONFIG_DIR", raising=False)
        monkeypatch.setenv("APPDATA", "C:/Users/x/AppData/Roaming")
        assert "Nodify" in str(config_directory())

    def test_settings_path_is_inside_the_directory(self) -> None:
        assert settings_path().name == "settings.json"
