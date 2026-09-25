"""Vault path resolution: traversal, absolute paths and symlink escapes."""

from __future__ import annotations

from pathlib import Path

import pytest

from nodify.adapters.vault_paths import (
    FileSystemVaultPathResolver,
    InvalidNameError,
    slugify,
    validate_name,
)
from nodify.domain.ports import ALL_AREAS, NOTES, PathOutsideVaultError


@pytest.fixture
def resolver(tmp_path: Path) -> FileSystemVaultPathResolver:
    root = tmp_path / "vault"
    root.mkdir()
    return FileSystemVaultPathResolver(root)


def test_resolves_inside_vault(resolver: FileSystemVaultPathResolver) -> None:
    resolved = resolver.resolve("notes", "Ideas.md")
    assert resolved == resolver.root / "notes" / "Ideas.md"
    assert resolver.contains(resolved)


@pytest.mark.parametrize("part", ["..", ".", ""])
def test_rejects_dot_components(resolver: FileSystemVaultPathResolver, part: str) -> None:
    with pytest.raises(PathOutsideVaultError):
        resolver.resolve("notes", part)


def test_rejects_parent_traversal(resolver: FileSystemVaultPathResolver) -> None:
    with pytest.raises(PathOutsideVaultError):
        resolver.resolve("notes", "..", "..", "secrets.md")


def test_rejects_absolute_component(resolver: FileSystemVaultPathResolver) -> None:
    with pytest.raises(PathOutsideVaultError):
        resolver.resolve("C:/Windows/System32/drivers/etc/hosts")
    with pytest.raises(PathOutsideVaultError):
        resolver.resolve("notes", "C:\\evil.md")


def test_rejects_drive_relative_component(resolver: FileSystemVaultPathResolver) -> None:
    # A single letter plus colon is a drive-relative path on Windows and would
    # otherwise be treated as an ordinary file name.
    with pytest.raises(PathOutsideVaultError):
        resolver.resolve("notes", "C:evil.md")


def test_rejects_no_components(resolver: FileSystemVaultPathResolver) -> None:
    with pytest.raises(PathOutsideVaultError):
        resolver.resolve()


def test_rejects_path_escaping_via_symlink(resolver: FileSystemVaultPathResolver) -> None:
    """A lexically innocent name that resolves outside the vault is still rejected."""
    outside = resolver.root.parent / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x", encoding="utf-8")
    try:
        link = resolver.root / "notes" / "escape"
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert not resolver.contains(link / "secret.md")
    with pytest.raises(PathOutsideVaultError):
        resolver.resolve("notes", "escape", "secret.md")


def test_allows_symlink_that_stays_inside_vault(resolver: FileSystemVaultPathResolver) -> None:
    target = resolver.root / "notes"
    target.mkdir()
    try:
        link = resolver.root / "alias"
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert resolver.contains(link)


class TestContains:
    """Direct coverage of the containment check.

    These run everywhere, including where creating a symlink needs administrator
    privilege, so the escape logic is never silently untested.
    """

    def test_accepts_path_inside(self, resolver: FileSystemVaultPathResolver) -> None:
        assert resolver.contains(resolver.root / "notes" / "A.md")

    def test_accepts_root_itself(self, resolver: FileSystemVaultPathResolver) -> None:
        assert resolver.contains(resolver.root)

    def test_rejects_lexical_escape(self, resolver: FileSystemVaultPathResolver) -> None:
        escaping = resolver.root / "notes" / ".." / ".." / "outside.md"
        assert not resolver.contains(escaping)

    def test_rejects_sibling_with_shared_prefix(
        self, resolver: FileSystemVaultPathResolver, tmp_path: Path
    ) -> None:
        # A folder whose name merely starts with the vault name must not pass.
        sibling = tmp_path / "vault-backup"
        sibling.mkdir()
        assert not resolver.contains(sibling / "notes.md")

    def test_rejects_absolute_outside_path(
        self, resolver: FileSystemVaultPathResolver, tmp_path: Path
    ) -> None:
        outside = tmp_path / "elsewhere" / "notes.md"
        outside.parent.mkdir()
        outside.write_text("x", encoding="utf-8")
        assert not resolver.contains(outside)

    def test_missing_file_inside_vault_is_still_contained(
        self, resolver: FileSystemVaultPathResolver
    ) -> None:
        # A file about to be created is contained even though it does not exist.
        assert resolver.contains(resolver.root / "notes" / "New.md")


def test_ensure_creates_areas(resolver: FileSystemVaultPathResolver) -> None:
    for area in ALL_AREAS:
        path = resolver.ensure_area(area)
        assert path.is_dir()
        assert path.name == area.directory


def test_ensure_structure_is_idempotent(resolver: FileSystemVaultPathResolver) -> None:
    resolver.ensure_structure()
    resolver.ensure_structure()
    assert (resolver.root / "notes").is_dir()
    assert (resolver.root / "todos").is_dir()
    assert (resolver.root / "timer").is_dir()


def test_area_of_identifies_area(resolver: FileSystemVaultPathResolver) -> None:
    resolver.ensure_structure()
    assert resolver.area_of(resolver.root / "notes" / "A.md") is NOTES
    assert resolver.area_of(resolver.root.parent / "elsewhere" / "A.md") is None
    assert resolver.area_of(resolver.root) is None


class TestNameValidation:
    @pytest.mark.parametrize("name", ["Note", "My Note", "Ünïcode", "note-1", "a.b.c"])
    def test_accepts_valid_names(self, name: str) -> None:
        assert validate_name(name) == name

    @pytest.mark.parametrize("name", ["", "   ", "a/b", "a\\b", "a:b", "a*b", "a|b", "a?b", 'a"b'])
    def test_rejects_illegal_characters(self, name: str) -> None:
        with pytest.raises(InvalidNameError):
            validate_name(name)

    @pytest.mark.parametrize("name", ["trailing.", "CON", "nul", "COM1", "LPT9"])
    def test_rejects_reserved_and_dotted_names(self, name: str) -> None:
        with pytest.raises(InvalidNameError):
            validate_name(name)

    def test_trailing_space_is_stripped_not_rejected(self) -> None:
        # Whitespace is trimmed before validation, so a padded title is usable
        # rather than rejected.
        assert validate_name("trailing ") == "trailing"

    def test_rejects_overlong_name(self) -> None:
        with pytest.raises(InvalidNameError):
            validate_name("x" * 200)

    def test_strips_surrounding_whitespace(self) -> None:
        assert validate_name("  Padded  ") == "Padded"


class TestSlugify:
    def test_keeps_readable_characters(self) -> None:
        assert slugify("My Great Note") == "My Great Note"

    def test_replaces_illegal_characters(self) -> None:
        assert slugify('Report: Q1/Q2 "final"') == "Report- Q1-Q2 -final-"

    def test_strips_trailing_dots(self) -> None:
        assert slugify("Odd...") == "Odd"

    def test_escapes_reserved_names(self) -> None:
        assert slugify("CON") == "CON-note"

    def test_truncates_long_titles(self) -> None:
        assert len(slugify("x" * 300)) <= 120

    def test_rejects_empty(self) -> None:
        with pytest.raises(InvalidNameError):
            slugify("   ")
