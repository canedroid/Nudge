"""Vault file enumeration.

The Files feature lists and filters every Markdown file in the vault, so it needs
a cheap listing that does not parse any document. Day is derived from the
conventional path shape rather than from frontmatter, because the folder name is
what makes a file visible to a human browsing the vault in Obsidian:

- ``todos/2026-09/25-2026.md``
- ``timer/2026-09/25-2026.md``

Anything that does not match is still listed, with ``day`` set to ``None``, so an
unexpected file is visible in the UI instead of silently disappearing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from nodify.domain.ports import ALL_AREAS, FileChanges, FileEntry

#: ``25-2026.md`` inside a ``2026-09`` folder.
DAY_FILE_PATTERN = re.compile(r"^(\d{1,2})-(\d{4})\.md$")
MONTH_FOLDER_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")

READ_SUFFIXES = frozenset({".md", ".markdown"})


def parse_day_from_path(relative: Path) -> datetime | None:
    """Derive the represented day from a relative vault path, or ``None``."""
    parts = relative.parts
    if len(parts) != 3:
        return None

    area, month_folder, file_name = parts
    if not any(a.directory == area for a in ALL_AREAS):
        return None

    month_match = MONTH_FOLDER_PATTERN.match(month_folder)
    day_match = DAY_FILE_PATTERN.match(file_name)
    if not month_match or not day_match:
        return None

    year = int(month_match.group(1))
    month = int(month_match.group(2))
    day = int(day_match.group(1))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None

    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def parse_category_from_path(relative: Path) -> str | None:
    """Derive a note's category from ``notes/<Category>/<Name>.md``."""
    parts = relative.parts
    if len(parts) != 3 or parts[0] != "notes":
        return None
    return parts[1]


def build_entry(root: Path, path: Path) -> FileEntry:
    """Describe a single vault file without reading it."""
    stat = path.stat()
    relative = path.relative_to(root)
    area = relative.parts[0] if relative.parts else None
    return FileEntry(
        relative_path=relative,
        name=path.stem,
        area=area,
        category=parse_category_from_path(relative),
        day=parse_day_from_path(relative),
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
    )


class FileSystemFileIndex:
    """Enumerates Markdown files beneath a vault root."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _walk(self) -> Iterable[Path]:
        """Yield every Markdown file, skipping symlinked directories.

        Following a directory symlink could leave the vault or loop forever, so
        a link whose real target is still inside the vault is allowed through but
        is not descended into twice.
        """
        for area in ALL_AREAS:
            area_root = self._root / area.directory
            if not area_root.is_dir():
                continue
            yield from self._walk_dir(area_root, visited=set())

    def _walk_dir(self, directory: Path, visited: set[Path]) -> Iterable[Path]:
        real = directory.resolve()
        if real in visited:
            return
        visited.add(real)

        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except (PermissionError, OSError):
            # An unreadable folder must not abort the whole listing.
            return

        for child in children:
            try:
                if child.is_dir():
                    yield from self._walk_dir(child, visited)
                elif child.suffix.lower() in READ_SUFFIXES:
                    yield child
            except OSError:
                continue

    def refresh(self) -> list[FileEntry]:
        """A full listing, sorted by area then path."""
        entries: list[FileEntry] = []
        for path in self._walk():
            try:
                entries.append(build_entry(self._root, path))
            except OSError:
                # A file deleted mid-listing simply does not appear this pass.
                continue
        return sorted(entries, key=lambda e: (e.area or "", str(e.relative_path).lower()))

    def changes_since(self, previous: Sequence[FileEntry]) -> FileChanges:
        """Diff a fresh listing against a previous one.

        ``watchdog`` events are lossy under load, so the index is authoritative
        and events are only a prompt to re-scan.
        """
        current = self.refresh()
        previous_by_path = {str(entry.relative_path): entry for entry in previous}
        current_by_path = {str(entry.relative_path): entry for entry in current}

        added = tuple(e for path, e in current_by_path.items() if path not in previous_by_path)

        modified: list[FileEntry] = []
        for path, entry in current_by_path.items():
            old = previous_by_path.get(path)
            if old is not None and (old.size != entry.size or old.modified_at != entry.modified_at):
                modified.append(entry)

        removed = tuple(sorted(Path(p) for p in previous_by_path if p not in current_by_path))
        return FileChanges(added=added, modified=tuple(modified), removed=removed)
