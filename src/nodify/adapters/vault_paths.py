"""Vault path resolution and validation.

Every user-facing path in the application goes through this module. The rules are:

- no path may resolve outside the vault root, whether by ``..`` segments, an
  absolute path, or a symlink pointing elsewhere;
- the three top-level areas are fixed and are created on demand;
- a category or file name that would break the folder contract is rejected
  before it reaches the filesystem.

Symlinks are the subtle case. A purely lexical check passes ``link -> ..\\..``,
because the *name* is inside the vault, so paths are resolved to their real
location and re-checked. A symlink that escapes is rejected rather than followed.
"""

from __future__ import annotations

import re
from pathlib import Path

from nodify.domain.ports import (
    ALL_AREAS,
    PathOutsideVaultError,
    VaultArea,
    VaultError,
)

#: Characters that are illegal in a Windows file name, plus the reserved device
#: names. A trailing dot or space is also rejected because Windows silently strips
#: it, which would make the path on disk differ from the path we validated.
ILLEGAL_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

MAX_NAME_LENGTH = 120


class InvalidNameError(VaultError):
    """A proposed file or folder name is not usable on Windows."""


def validate_name(name: str) -> str:
    """Return ``name`` stripped, or raise if it cannot be used as a file name."""
    cleaned = name.strip()
    if not cleaned:
        raise InvalidNameError("name must not be empty")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise InvalidNameError(f"name is longer than {MAX_NAME_LENGTH} characters")
    if ILLEGAL_NAME_CHARS.search(cleaned):
        raise InvalidNameError(f"name contains an illegal character: {name!r}")
    if cleaned.endswith((".", " ")):
        raise InvalidNameError("name must not end with a dot or a space")
    if cleaned.split(".")[0].upper() in RESERVED_NAMES:
        raise InvalidNameError(f"name is a reserved Windows device name: {name!r}")
    return cleaned


def slugify(title: str) -> str:
    """Turn a human title into a safe file-name stem.

    Spaces, punctuation and non-ASCII characters are preserved where Windows
    allows them, because these names are meant to stay readable in Obsidian.
    Only genuinely illegal characters are replaced.
    """
    cleaned = title.strip()
    if not cleaned:
        raise InvalidNameError("cannot build a file name from an empty title")
    safe = ILLEGAL_NAME_CHARS.sub("-", cleaned)
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    if not safe:
        raise InvalidNameError(f"title has no usable characters: {title!r}")
    if len(safe) > MAX_NAME_LENGTH:
        safe = safe[:MAX_NAME_LENGTH].rstrip(" .")
    if safe.split(".")[0].upper() in RESERVED_NAMES:
        safe = f"{safe}-note"
    return safe


class FileSystemVaultPathResolver:
    """Resolves logical names to real paths beneath a vault root."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def __repr__(self) -> str:
        return f"FileSystemVaultPathResolver({str(self._root)!r})"

    # ------------------------------------------------------------- validation

    def _is_inside(self, candidate: Path) -> bool:
        try:
            candidate.relative_to(self._root)
        except ValueError:
            return False
        return True

    def _real(self, candidate: Path) -> Path:
        """Resolve symlinks as far as the path exists, without requiring it to.

        ``Path.resolve()`` is non-strict on 3.13 and does not raise for a missing
        tail, so this works for files that are about to be created.
        """
        try:
            return candidate.resolve()
        except OSError:  # pragma: no cover - only on a broken filesystem link
            return candidate.absolute()

    def contains(self, candidate: Path) -> bool:
        """Whether ``candidate`` lies within the vault, following symlinks."""
        return self._is_inside(self._real(candidate))

    # ---------------------------------------------------------------- resolve

    def resolve(self, *parts: str) -> Path:
        """Resolve a path below the vault, or raise.

        Rejects absolute components, ``..`` traversal, and any component that
        resolves outside the vault once symlinks are followed.
        """
        if not parts:
            raise PathOutsideVaultError("a vault path needs at least one component")

        for part in parts:
            if not part or part in (".", ".."):
                raise PathOutsideVaultError(f"illegal path component: {part!r}")
            if Path(part).is_absolute() or ":" in part:
                raise PathOutsideVaultError(f"absolute path component rejected: {part!r}")

        candidate = self._root.joinpath(*parts)
        if not self.contains(candidate):
            raise PathOutsideVaultError(f"path escapes the vault: {'/'.join(parts)!r}")
        return candidate

    def resolve_dir(self, *parts: str) -> Path:
        """Resolve an existing-or-future directory below the vault."""
        return self.resolve(*parts)

    # ------------------------------------------------------------------ areas

    def area_path(self, area: VaultArea) -> Path:
        return self._root / area.directory

    def ensure_area(self, area: VaultArea) -> Path:
        """Create the area directory if it is missing and return it."""
        path = self.area_path(area)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_structure(self) -> None:
        """Create the fixed vault structure, including the year-month level."""
        for area in ALL_AREAS:
            self.ensure_area(area)

    def area_of(self, path: Path) -> VaultArea | None:
        """Which top-level area a path belongs to, if any."""
        try:
            relative = self._real(path).relative_to(self._root)
        except ValueError:
            return None
        if not relative.parts:
            return None
        name = relative.parts[0]
        return next((a for a in ALL_AREAS if a.directory == name), None)

    def relative_to_vault(self, path: Path) -> Path:
        """The path relative to the vault root, for display."""
        return self._real(path).relative_to(self._root)
