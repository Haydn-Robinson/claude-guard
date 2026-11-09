"""Folder validation and path-form conversion.

Two path conventions are in play (verified in Phase 0):

* permission *rule* paths: ``//abs/path`` is absolute (``/x`` would be relative to the settings source)
* *sandbox* paths: ``/abs/path`` is absolute
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from .errors import UnsafeFolderError

# Characters that are glob/pattern syntax in rule paths. We cannot escape them reliably, so refuse.
_UNSAFE_CHARS = re.compile(r"[*?\[\]\\\x00-\x1f]")

# Directories directly under the system drive that must never be granted, at, above or below.
_SYSTEM_DIRS = frozenset({"windows", "program files", "program files (x86)", "programdata"})

DEFAULT_SYSTEM_DRIVE = Path("/mnt/c")
MIN_DEPTH = 2  # e.g. /tmp/x ok; /tmp, /etc, /home refused unless --force-broad


def rule_path(path: Path) -> str:
    """Absolute path in permission-rule form: ``//mnt/d/x``."""
    return "/" + str(path)


def sandbox_path(path: Path) -> str:
    """Absolute path in sandbox form: ``/mnt/d/x``."""
    return str(path)


def detect_drive_roots(
    mountinfo: Path = Path("/proc/self/mountinfo"), mnt: Path = Path("/mnt")
) -> set[Path]:
    """Find ``/mnt/<letter>`` mount points from the live mount table (drives come and go)."""
    roots: set[Path] = set()
    try:
        for line in mountinfo.read_text().splitlines():
            fields = line.split()
            if len(fields) > 4:
                mp = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])
                if re.fullmatch(r"/mnt/[A-Za-z]", mp):
                    roots.add(Path(mp))
    except OSError:
        pass
    try:
        for child in mnt.iterdir():
            if len(child.name) == 1 and child.name.isalpha() and os.path.ismount(child):
                roots.add(child)
    except OSError:
        pass
    return roots


class PathPolicy:
    """Decides which folders may be granted. Pure given its constructor arguments."""

    def __init__(
        self,
        home: Path,
        drive_roots: Iterable[Path] = (),
        system_drive: Path = DEFAULT_SYSTEM_DRIVE,
    ):
        self.home = home.resolve()
        self.system_drive = system_drive
        self.drive_roots = {Path(p) for p in drive_roots} | {system_drive}

    @classmethod
    def detect(cls) -> PathPolicy:
        return cls(Path.home(), detect_drive_roots())

    def check_folder(self, raw: str | os.PathLike[str], *, force_broad: bool = False) -> Path:
        """Resolve ``raw`` to an absolute real path, or raise UnsafeFolderError."""
        p = Path(raw).expanduser().resolve(strict=False)

        if _UNSAFE_CHARS.search(str(p)):
            raise UnsafeFolderError(
                f"{p}: path contains glob or control characters (* ? [ ] \\) that cannot be "
                "safely written into permission rules"
            )
        if p == Path("/"):
            raise UnsafeFolderError("refusing to grant the filesystem root")
        if p == self.home or p in self.home.parents:
            raise UnsafeFolderError(f"refusing to grant the home directory or a parent: {p}")
        for root in self.drive_roots:
            if p == root or p in root.parents:
                raise UnsafeFolderError(f"refusing to grant a whole drive (or above): {p}")
        self._check_system_drive(p)

        if len(p.parts) - 1 < MIN_DEPTH and not force_broad:
            raise UnsafeFolderError(
                f"{p} is very broad (depth {len(p.parts) - 1}); pass --force-broad to override",
                overridable=True,
            )
        return p

    def _check_system_drive(self, p: Path) -> None:
        if p != self.system_drive and self.system_drive not in p.parents:
            return
        rel = [part.lower() for part in p.relative_to(self.system_drive).parts]
        if rel and rel[0] in _SYSTEM_DIRS:
            raise UnsafeFolderError(f"refusing to grant inside the Windows system area: {p}")
        if rel and rel[0] == "users" and len(rel) <= 2:
            raise UnsafeFolderError(
                f"refusing to grant {p}: Users, or the top level of a user profile. "
                "Grant a specific project folder under it instead."
            )
