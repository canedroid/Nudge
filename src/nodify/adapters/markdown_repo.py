"""Markdown and YAML persistence.

Two guarantees matter here.

**Unknown keys survive.** The frontmatter is parsed and re-emitted with
``ruamel.yaml`` in round-trip mode, so keys Nodify does not understand — Obsidian
metadata, third-party plugins, hand-added notes — are written back exactly as they
were read. The plan requires this, and it is the difference between a usable
Obsidian vault and a lossy one.

**Writes are atomic.** A document is written to a temporary file in the same
directory and then moved into place with ``os.replace``. An interrupted write
leaves the original file intact rather than a truncated one.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from uuid import uuid4

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from nodify.domain.ports import DocumentFormatError

FRONTMATTER_FENCE = "---"


def _new_yaml() -> YAML:
    """Build a configured round-trip YAML instance.

    A fresh instance is returned per operation rather than sharing one module
    level singleton. A ruamel ``YAML`` object is stateful: once a representer
    raises, that instance is left unusable and every later ``dump`` through it
    silently emits an empty document. With a shared instance, a single malformed
    save would therefore blank the frontmatter of every subsequent write for the
    life of the process. Constructing one costs well under a millisecond, which is
    irrelevant next to a disk write.

    ``preserve_quotes`` stops Obsidian's quoting style being rewritten, and
    ``allow_unicode`` keeps non-Latin titles intact.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.allow_unicode = True
    return yaml


class MarkdownCodec:
    """Parses and serialises a Markdown document with YAML frontmatter."""

    def parse(self, text: str) -> tuple[dict[str, object], str]:
        """Split ``text`` into a frontmatter mapping and a Markdown body.

        A document with no frontmatter is valid: it yields an empty mapping and
        the whole text as the body, so a hand-written note is never rejected.
        """
        return self._parse(text)

    def parse_document(self, text: str) -> tuple[dict[str, object], str]:
        """Explicitly named alias for :meth:`parse`, for call-site clarity."""
        return self._parse(text)

    def _parse(self, text: str) -> tuple[dict[str, object], str]:
        # Tolerate a UTF-8 BOM, which Windows Notepad adds.
        if text.startswith("\ufeff"):
            text = text[1:]

        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != FRONTMATTER_FENCE:
            return {}, text

        for index in range(1, len(lines)):
            if lines[index].strip() == FRONTMATTER_FENCE:
                block = "".join(lines[1:index])
                body = "".join(lines[index + 1 :])
                return self._load_yaml(block), body

        # An opening fence with no closing fence is malformed. Report it rather
        # than guessing, so the UI can offer recovery.
        raise DocumentFormatError("frontmatter block is not closed")

    def _load_yaml(self, block: str) -> dict[str, object]:
        if not block.strip():
            return {}
        try:
            loaded = _new_yaml().load(block)
        except YAMLError as exc:
            raise DocumentFormatError(f"invalid YAML frontmatter: {exc}") from exc
        if loaded is None:
            return {}
        if not isinstance(loaded, Mapping):
            raise DocumentFormatError("frontmatter must be a mapping")
        if isinstance(loaded, dict):
            # A ruamel CommentedMap is a dict subclass that carries comments and
            # key order. It is returned as-is rather than copied with dict(), which
            # would silently discard comments attached to keys.
            return loaded
        return dict(loaded)

    def serialise(self, frontmatter: Mapping[str, object], body: str) -> str:
        """Render a document. A mapping with no keys omits the fence entirely."""
        if not frontmatter:
            return body

        # A mapping that already came from the parser is a ruamel CommentedMap
        # holding comment tokens and key order. It is passed through unchanged:
        # re-wrapping it in a new CommentedMap would copy the items but silently
        # drop every comment, which is exactly the metadata loss this codec exists
        # to prevent.
        data: Mapping[str, object] = (
            frontmatter if isinstance(frontmatter, dict) else CommentedMap(frontmatter)
        )
        buffer = StringIO()
        try:
            _new_yaml().dump(data, buffer)
        except YAMLError as exc:
            raise DocumentFormatError(f"frontmatter could not be serialised: {exc}") from exc

        rendered = buffer.getvalue()
        # Guarantee a single trailing newline before the closing fence.
        if not rendered.endswith("\n"):
            rendered += "\n"
        return f"{FRONTMATTER_FENCE}\n{rendered}{FRONTMATTER_FENCE}\n{body}"


class AtomicMarkdownRepository:
    """Reads and writes Markdown documents beneath a resolved vault root."""

    def __init__(self, resolver: object) -> None:
        # Typed loosely to avoid a circular import; the resolver is a
        # VaultPathResolver in practice.
        self._resolver = resolver
        self._codec = MarkdownCodec()

    @property
    def root(self) -> Path:
        return self._resolver.root  # type: ignore[attr-defined]

    def read_text(self, path: Path) -> str:
        """Read a file, or raise a recoverable error."""
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise DocumentFormatError(f"no such document: {path.name}") from exc
        except PermissionError as exc:
            raise DocumentFormatError(f"document is locked: {path.name}") from exc
        except UnicodeDecodeError as exc:
            raise DocumentFormatError(f"document is not valid UTF-8: {path.name}") from exc

    def read(self, path: Path) -> tuple[dict[str, object], str]:
        return self._codec.parse_document(self.read_text(path))

    def write(self, path: Path, frontmatter: Mapping[str, object], body: str) -> None:
        """Atomically replace the document at ``path``."""
        text = self._codec.serialise(frontmatter, body)
        self._atomic_write(path, text)

    def _atomic_write(self, path: Path, text: str) -> None:
        """Write via a temporary file in the same directory, then rename.

        ``os.replace`` is atomic on Windows and POSIX, so a reader either sees the
        old file or the new one, never a partial write.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = None
        temp_path: Path | None = None
        try:
            # The temporary file must share the destination directory: os.replace
            # is only atomic within a volume.
            fd, temp_name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=f".{uuid4().hex[:8]}.tmp"
            )
            temp_path = Path(temp_name)
            handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None
            temp_path.replace(path)
            temp_path = None
        finally:
            if handle is not None:
                handle.close()
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def exists(self, path: Path) -> bool:
        return path.is_file()

    def list_files(self, directory: Path, pattern: str = "*.md") -> list[Path]:
        """Markdown files directly inside ``directory``, sorted by name."""
        if not directory.is_dir():
            return []
        return sorted(directory.glob(pattern), key=lambda p: p.name.lower())
