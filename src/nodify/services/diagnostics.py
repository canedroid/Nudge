"""Vault diagnostics: what is wrong, and whether it is safe to act on.

The plan's definition of done for hardening is "no data-loss path exists in the
supported failure cases" and "unsupported or malformed files remain available for
repair". Both are served by one thing: knowing precisely what is wrong with each
file before anything tries to write to it.

Three failure modes are handled explicitly, because each has a different safe
response.

**A malformed file is left alone.** Nodify does not parse it, does not rewrite it,
and does not delete it. The user's text is intact and visible in the Files view,
which is the only way they can fix it.

**A locked file is a warning, not an error.** OneDrive, Obsidian's sync and a
backup tool all hold transient locks. Retrying is correct; failing permanently is
not.

**An external edit is a conflict the user resolves.** If the file on disk changed
since Nodify last read it, saving would discard whatever the user did in Obsidian
in the meantime. The conflict is reported rather than silently resolved, because
the user's own text is the thing at risk.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from nodify.domain.ports import (
    CollisionError,
    DocumentFormatError,
    PathOutsideVaultError,
    VaultError,
)


class Problem(StrEnum):
    """What is wrong with a file."""

    MALFORMED = "malformed"
    LOCKED = "locked"
    UNREADABLE = "unreadable"
    CONFLICT = "conflict"
    OUTSIDE_VAULT = "outside_vault"


class Severity(StrEnum):
    """How much the problem should interrupt the user."""

    #: Show it, but carry on. A locked file is usually transient.
    WARNING = "warning"
    #: The user must decide. Silently overwriting would lose their work.
    BLOCKING = "blocking"


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """One problem found with one path, and what to do about it."""

    problem: Problem
    severity: Severity
    path: Path | None
    detail: str

    @property
    def is_blocking(self) -> bool:
        return self.severity is Severity.BLOCKING

    def __str__(self) -> str:
        where = self.path.name if self.path else "vault"
        return f"{self.problem.value}: {where} — {self.detail}"


@dataclass
class VaultReport:
    """Everything found in one pass over the vault."""

    findings: list[Diagnosis] = field(default_factory=list)

    def add(self, diagnosis: Diagnosis) -> None:
        self.findings.append(diagnosis)

    def of(self, problem: Problem) -> list[Diagnosis]:
        return [d for d in self.findings if d.problem is problem]

    @property
    def blocking(self) -> list[Diagnosis]:
        return [d for d in self.findings if d.is_blocking]

    @property
    def warnings(self) -> list[Diagnosis]:
        return [d for d in self.findings if not d.is_blocking]

    @property
    def is_clean(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        """A one-line summary for a status bar."""
        if self.is_clean:
            return "No problems found."
        parts: list[str] = []
        for problem in Problem:
            count = len(self.of(problem))
            if count:
                parts.append(f"{count} {problem.value}")
        return ", ".join(parts)


def is_locked(path: Path) -> bool:
    """Whether a file is currently held open by another process.

    Opening for append does not modify anything, and an exclusive share flag is the
    closest a plain Python open gets to "is somebody else using this".
    """
    if not path.exists():
        return False
    try:
        handle = os.open(path, os.O_RDWR | os.O_APPEND)
    except PermissionError:
        return True
    except OSError:
        # A directory, or something else entirely. Not a lock.
        return False
    try:
        os.close(handle)
    except OSError:  # pragma: no cover - the close cannot realistically fail here
        return False
    return False


def diagnose_path(path: Path) -> Diagnosis | None:
    """Check one file for the problems that are detectable without parsing it."""
    if not path.exists():
        return None

    if path.suffix.lower() not in (".md", ".markdown"):
        return None

    if is_locked(path):
        # A lock is a warning: the file is still there and still readable by the
        # user, and the lock will usually clear on its own.
        return Diagnosis(
            problem=Problem.LOCKED,
            severity=Severity.WARNING,
            path=path,
            detail="The file is open in another program. Saving may fail until it is closed.",
        )

    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return Diagnosis(
            problem=Problem.MALFORMED,
            severity=Severity.WARNING,
            path=path,
            detail=f"Not valid UTF-8 ({exc.reason}). The file has not been touched.",
        )
    except PermissionError:
        return Diagnosis(
            problem=Problem.UNREADABLE,
            severity=Severity.WARNING,
            path=path,
            detail="Permission denied. The file has not been touched.",
        )
    except OSError as exc:
        return Diagnosis(
            problem=Problem.UNREADABLE,
            severity=Severity.WARNING,
            path=path,
            detail=f"Could not be read ({exc.strerror or 'unknown error'}).",
        )

    return None


def diagnose_vault(root: Path) -> VaultReport:
    """Check every Markdown file in the vault, without modifying anything."""
    report = VaultReport()
    for area in ("notes", "todos", "timer"):
        base = root / area
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            diagnosis = diagnose_path(path)
            if diagnosis is not None:
                report.add(diagnosis)
    return report


def classify_vault_error(exc: Exception, path: Path | None = None) -> Diagnosis:
    """Turn a repository error into a diagnosis the UI can show.

    A ``CollisionError`` is blocking: proceeding would overwrite something. A
    format error is a warning, because the file is still on disk and still
    repairable by hand.
    """
    if isinstance(exc, CollisionError):
        return Diagnosis(
            problem=Problem.CONFLICT,
            severity=Severity.BLOCKING,
            path=path,
            detail="Something already exists at that name. Nothing was overwritten.",
        )
    if isinstance(exc, PathOutsideVaultError):
        return Diagnosis(
            problem=Problem.OUTSIDE_VAULT,
            severity=Severity.BLOCKING,
            path=path,
            detail="That path is outside the vault. Nothing was written.",
        )
    if isinstance(exc, DocumentFormatError):
        return Diagnosis(
            problem=Problem.MALFORMED,
            severity=Severity.WARNING,
            path=path,
            detail=f"{exc} The file has been left exactly as it was.",
        )
    if isinstance(exc, PermissionError):
        return Diagnosis(
            problem=Problem.LOCKED,
            severity=Severity.WARNING,
            path=path,
            detail="The file is in use or read-only. Nothing was written.",
        )
    return Diagnosis(
        problem=Problem.UNREADABLE,
        severity=Severity.WARNING,
        path=path,
        detail=f"{exc or 'Unknown error.'}",
    )


@dataclass(frozen=True, slots=True)
class FileStamp:
    """A cheap fingerprint used to detect an external edit.

    Size and modification time together are enough for the purpose, and far cheaper
    than reading the file. A same-second edit of the same length is possible but
    vanishingly unlikely, and the cost of a false positive is a warning the user
    can dismiss, against the cost of a false negative being lost work.
    """

    size: int
    modified_ns: int

    @classmethod
    def of(cls, path: Path) -> FileStamp | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return cls(size=stat.st_size, modified_ns=stat.st_mtime_ns)

    def conflicts_with(self, path: Path) -> bool:
        """Whether ``path`` has changed since this stamp was taken."""
        current = FileStamp.of(path)
        if current is None:
            # Gone. That is a change, and a write would recreate it.
            return True
        return current != self


class WriteGuard:
    """Detects an external edit before writing over it.

    This is the last line of defence against losing work. The user opens a note in
    Nodify, switches to Obsidian and edits it, comes back and saves: without this
    the Obsidian edit is gone. With it, the save is refused and reported.
    """

    def __init__(self) -> None:
        self._stamps: dict[Path, FileStamp] = {}

    def remember(self, path: Path) -> None:
        """Record the current state of a file we have just read."""
        stamp = FileStamp.of(path)
        if stamp is not None:
            self._stamps[path] = stamp

    def forget(self, path: Path) -> None:
        self._stamps.pop(path, None)

    def check(self, path: Path) -> Diagnosis | None:
        """Report a conflict, or ``None`` when the file is unchanged.

        A file we have never seen is not a conflict: it is one we did not read, so
        there is nothing to have diverged from.
        """
        remembered = self._stamps.get(path)
        if remembered is None:
            return None
        if not remembered.conflicts_with(path):
            return None
        return Diagnosis(
            problem=Problem.CONFLICT,
            severity=Severity.BLOCKING,
            path=path,
            detail=(
                "This file changed outside Nodify since it was opened. "
                "Saving would discard those changes."
            ),
        )

    def refresh(self, path: Path) -> None:
        """Accept the file's current state as the new baseline after a write."""
        self.remember(path)


def atomic_write_is_available(directory: Path) -> bool:
    """Whether atomic replacement works in ``directory``.

    ``os.replace`` needs the temporary file and the destination on the same volume.
    That holds on a normal filesystem and on most network shares, but it is worth
    checking rather than discovering mid-save, because the failure mode is a
    truncated document.
    """
    probe = directory / ".nodify-atomic-probe"
    temp = directory / ".nodify-atomic-probe.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temp.write_text("probe", encoding="utf-8")
        temp.replace(probe)
        probe.unlink(missing_ok=True)
    except OSError:
        temp.unlink(missing_ok=True)
        return False
    return True


def surviving_files(root: Path, broken: Iterable[Path]) -> list[Path]:
    """The files that are still on disk among the broken ones.

    Exposed so a repair action can be built on the guarantee that nothing was
    removed: the user's text is still there to be fixed by hand.
    """
    return [path for path in broken if path.exists()]


def scan_for_malformed(
    root: Path, reader: object, relative_of: object
) -> list[tuple[Path, DocumentFormatError]]:
    """Parse every file and return the ones that would not read.

    Used by the repair flow and by the tests, so the "still on disk" guarantee is
    checked against a real parse rather than assumed.
    """
    failures: list[tuple[Path, DocumentFormatError]] = []
    for area in ("notes", "todos", "timer"):
        base = root / area
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            try:
                reader.read(path)  # type: ignore[attr-defined]
            except DocumentFormatError as exc:
                failures.append((path, exc))
            except VaultError:
                continue
    return failures


__all__ = [
    "Diagnosis",
    "FileStamp",
    "Problem",
    "Severity",
    "VaultReport",
    "WriteGuard",
    "atomic_write_is_available",
    "classify_vault_error",
    "diagnose_path",
    "diagnose_vault",
    "is_locked",
    "scan_for_malformed",
    "surviving_files",
]
