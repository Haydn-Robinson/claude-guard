"""applied.json: the record of what the last apply wrote, plus locking, atomic writes and backups.

Lives in guard's own folder (``~/.config/claude-guard``), which the baseline locks. Format::

    {
      "version": 2,
      "files": {
        "<settings file>": {
          "file_created": true,          # did not exist before guard wrote it: deleted when emptied
          "created": ["permissions", "permissions.allow"],   # containers guard created (pruned)
          "entries": [
            {"kind": "array", "path": "permissions.allow", "value": "Edit(//x/**)",
             "preexisting": false},      # already there before guard: never removed
            {"kind": "scalar", "path": "sandbox.enabled", "value": true,
             "preexisting": false, "prior": {"present": false, "value": null}}  # restored on removal
          ]
        }
      },
      "hook": { ... }                    # what the hook enforces (compile.py, hook data)
    }

Ownership is per file, not per grant: the compiler produces the complete desired set for each
file, so there is nothing to reference count.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import GuardError
from .model import Entry

APPLIED_VERSION = 2
APPLIED_FILE = "applied.json"
LOCK_FILE = "applied.lock"
BACKUP_DIR = "backups"


@dataclass(frozen=True)
class Owned:
    entry: Entry
    preexisting: bool = False
    prior: tuple[bool, Any] | None = None  # scalars: (present, value) before guard set it

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {**self.entry.to_json(), "preexisting": self.preexisting}
        if self.prior is not None:
            d["prior"] = {"present": self.prior[0], "value": self.prior[1]}
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Owned:
        prior = d.get("prior")
        return cls(
            Entry.from_json(d),
            bool(d.get("preexisting", False)),
            (bool(prior["present"]), prior["value"]) if prior is not None else None,
        )


@dataclass
class FileRecord:
    file_created: bool
    created: list[str] = field(default_factory=list)
    entries: list[Owned] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "file_created": self.file_created,
            "created": self.created,
            "entries": [o.to_json() for o in self.entries],
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> FileRecord:
        return cls(
            bool(d["file_created"]),
            list(d.get("created", [])),
            [Owned.from_json(e) for e in d.get("entries", [])],
        )


@dataclass
class Applied:
    files: dict[str, FileRecord] = field(default_factory=dict)
    hook: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": APPLIED_VERSION,
            "files": {f: r.to_json() for f, r in self.files.items()},
            "hook": self.hook,
        }


def load_applied(guard_home: Path) -> Applied:
    path = guard_home / APPLIED_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Applied()
    except json.JSONDecodeError as e:
        raise GuardError(f"{path}: invalid JSON ({e}); refusing to continue") from None
    if not isinstance(data, dict) or data.get("version") != APPLIED_VERSION:
        raise GuardError(f"{path}: unsupported applied.json (expected version {APPLIED_VERSION})")
    try:
        return Applied(
            {f: FileRecord.from_json(r) for f, r in data.get("files", {}).items()},
            dict(data.get("hook", {})),
        )
    except (KeyError, TypeError, AttributeError) as e:
        raise GuardError(f"{path}: malformed applied.json ({e!r})") from None


def save_applied(guard_home: Path, applied: Applied) -> None:
    atomic_write(
        guard_home / APPLIED_FILE,
        json.dumps(applied.to_json(), indent=2, ensure_ascii=False) + "\n",
    )


@contextmanager
def lock(guard_home: Path) -> Iterator[None]:
    """Exclusive lock on guard's records for the duration of an apply. Does not wait."""
    guard_home.mkdir(parents=True, exist_ok=True)
    fd = os.open(guard_home / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GuardError("another guard command is applying changes; try again") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---- file writing ---------------------------------------------------------------------------------


def timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")


def backup_file(path: Path, backup_dir: Path, now: datetime | None = None) -> Path | None:
    """Copy ``path`` into ``backup_dir`` with a timestamped name. No-op if it does not exist."""
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe = str(path).strip("/").replace("/", "__").replace(" ", "_")
    dest = backup_dir / f"{timestamp(now)}-{safe}.bak"
    shutil.copy2(path, dest)
    return dest


def atomic_write(path: Path, text: str) -> None:
    """Write via temp file + rename in the same directory; follows symlinks; keeps file mode."""
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if target.exists():
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
