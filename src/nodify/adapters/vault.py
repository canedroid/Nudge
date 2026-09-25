"""Vault creation and selection.

The user picks a vault folder; Nodify never chooses one silently. Creating a vault
means creating the fixed area structure the plan defines, and refusing to treat an
arbitrary folder as a vault when it already contains an incompatible layout.

Selection is deliberately not a singleton. ``AppPaths`` owns the OS locations
(Phase I) and hands a root to :class:`Vault`, which keeps this module testable
with a temporary directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nodify.adapters.file_index import FileSystemFileIndex
from nodify.adapters.markdown_repo import AtomicMarkdownRepository, MarkdownCodec
from nodify.adapters.vault_paths import FileSystemVaultPathResolver
from nodify.domain.ports import ALL_AREAS, VaultArea, VaultError

VAULT_MARKER = ".nodify-vault"
VAULT_FORMAT_VERSION = 1


class VaultAlreadyExistsError(VaultError):
    """A vault is already present at that location."""


class NotAVaultError(VaultError):
    """The folder is not a Nodify vault and cannot be used as one."""


@dataclass(frozen=True, slots=True)
class VaultInfo:
    """What is known about a vault folder without opening it."""

    root: Path
    format_version: int
    exists: bool


def is_vault(path: Path) -> bool:
    """Whether ``path`` looks like a Nodify vault."""
    return (path / VAULT_MARKER).is_file()


def describe(path: Path) -> VaultInfo:
    """Inspect a candidate vault folder."""
    root = Path(path)
    marker = root / VAULT_MARKER
    if not marker.is_file():
        return VaultInfo(root=root, format_version=0, exists=False)

    version = VAULT_FORMAT_VERSION
    text = marker.read_text(encoding="utf-8").strip()
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "format_version" and value.strip().isdigit():
            version = int(value.strip())
    return VaultInfo(root=root, format_version=version, exists=True)


def create(root: Path, *, initial_year_month: str | None = None) -> VaultInfo:
    """Create the fixed vault structure at ``root``.

    Refuses to overwrite an existing vault. ``initial_year_month`` seeds the
    ``todos`` and ``timer`` year-month folders, in ``YYYY-MM`` form, so a new
    vault is immediately usable rather than showing two empty tiles.
    """
    root = Path(root)
    if is_vault(root):
        raise VaultAlreadyExistsError(f"a vault already exists at {root}")

    if root.exists() and not root.is_dir():
        raise NotAVaultError(f"not a folder: {root}")

    existing = [p for p in root.iterdir() if p.name not in (VAULT_MARKER,)] if root.is_dir() else []
    if existing:
        # Refuse to adopt a folder that already holds unrelated content, so a
        # mistyped target can never have files reorganised underneath the user.
        raise NotAVaultError(f"folder is not empty, refusing to create a vault here: {root}")

    root.mkdir(parents=True, exist_ok=True)
    resolver = FileSystemVaultPathResolver(root)
    resolver.ensure_structure()

    for area in ALL_AREAS:
        if area.key in ("todos", "timer") and initial_year_month:
            (root / area.directory / initial_year_month).mkdir(parents=True, exist_ok=True)

    (root / VAULT_MARKER).write_text(
        "\n".join(
            [
                "# Nodify vault marker. Delete this file to stop Nodify treating",
                "# the folder as a vault. The folder layout is documented in",
                "# PLAN.MD section 6; edit files directly, Nodify only adds",
                "# frontmatter it understands and preserves everything else.",
                f"format_version: {VAULT_FORMAT_VERSION}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return describe(root)


class Vault:
    """A selected vault and the adapters bound to it.

    Constructing this composes the whole storage kernel: a path resolver, a
    document mapper, a Markdown codec and a file index, all sharing one root.
    Repositories are added in later phases; this is the seam they attach to.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._resolver = FileSystemVaultPathResolver(self._root)
        self._codec = MarkdownCodec()
        self._repository = AtomicMarkdownRepository(self._resolver)
        self._index = FileSystemFileIndex(self._root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def info(self) -> VaultInfo:
        return describe(self._root)

    @property
    def resolver(self) -> FileSystemVaultPathResolver:
        return self._resolver

    @property
    def codec(self) -> MarkdownCodec:
        return self._codec

    @property
    def documents(self) -> AtomicMarkdownRepository:
        return self._repository

    @property
    def index(self) -> FileSystemFileIndex:
        return self._index

    def area(self, area: VaultArea) -> Path:
        return self._resolver.ensure_area(area)

    def open(self) -> None:
        """Select this folder as the active vault, creating the structure if needed."""
        if not self._root.exists():
            create(self._root)
        if not is_vault(self._root):
            raise NotAVaultError(f"not a Nodify vault: {self._root}")
        self._resolver.ensure_structure()
