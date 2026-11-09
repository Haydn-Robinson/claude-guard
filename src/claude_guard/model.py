"""Typed data for claude-guard v2: the policy as written, and what it compiles to.

Two stages of policy data:

* ``Scope`` / ``Policy``: what ``policy.toml`` says, with paths as written (``~``, placeholders such
  as ``{drives}``). Platform-independent.
* ``View``: one scope after preset expansion and path resolution on this machine. Paths are absolute
  and real. ``compile`` works on views.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Access(Enum):
    """Access levels, most to least permissive (design section 6)."""

    FULL = "full"
    BASH = "bash"
    EDIT = "edit"
    MODIFY = "modify"
    ASK = "ask"
    DENY = "deny"

    @property
    def rank(self) -> int:
        """Higher is more permissive."""
        return len(_ORDER) - _ORDER.index(self)

    def __lt__(self, other: Access) -> bool:
        """``a < b``: ``a`` is stricter (less permissive) than ``b``."""
        return self.rank < other.rank

    @property
    def bash_writes(self) -> bool:
        """Bash may create, change, rename and delete inside the folder."""
        return self in (Access.FULL, Access.BASH)

    @property
    def tools(self) -> str:
        """What the Edit/Write tools get: "allow", "closed", "ask" or "deny"."""
        return {
            Access.FULL: "allow",
            Access.BASH: "closed",
            Access.EDIT: "allow",
            Access.MODIFY: "allow",
            Access.ASK: "ask",
            Access.DENY: "deny",
        }[self]

    @classmethod
    def parse(cls, value: str) -> Access:
        try:
            return cls(value)
        except ValueError:
            names = ", ".join(a.value for a in cls)
            raise ValueError(f"unknown access level {value!r} (one of: {names})") from None


_ORDER = list(Access)


class Closed(Enum):
    """What the hook does for an Edit/Write outside the project and every grant."""

    ASK = "ask"
    DENY = "deny"


# ---- the policy as written ------------------------------------------------------------------


@dataclass(frozen=True)
class Grant:
    path: str
    access: Access
    protect: tuple[str, ...] = ()
    broad: bool = False  # allow a very broad folder (v1's --force-broad)


@dataclass(frozen=True)
class Protected:
    path: str
    ceiling: Access


@dataclass(frozen=True)
class Commands:
    allow: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()


@dataclass(frozen=True)
class Raw:
    """Passthrough entries: validated and owned, but not interpreted."""

    allow: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    allow_write: tuple[str, ...] = ()
    deny_write: tuple[str, ...] = ()
    settings: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class Scope:
    presets: tuple[str, ...] = ()
    write: tuple[Grant, ...] = ()
    protected: tuple[Protected, ...] = ()
    locked: tuple[str, ...] = ()
    read_deny: tuple[str, ...] = ()
    commands: Commands = Commands()
    raw: Raw = Raw()
    closed: Closed | None = None


@dataclass(frozen=True)
class Policy:
    everywhere: Scope = Scope()
    projects: dict[str, Scope] = field(default_factory=dict)  # key: the directory as written
    presets: dict[str, Scope] = field(default_factory=dict)  # the user's own presets


# ---- resolved on this machine ---------------------------------------------------------------


@dataclass(frozen=True)
class RGrant:
    path: Path
    access: Access
    protect: tuple[str, ...] = ()
    origin: str = ""  # where it came from, for error messages: "[everywhere]", "preset baseline"


@dataclass(frozen=True)
class RProtected:
    path: Path
    ceiling: Access
    origin: str = ""


@dataclass(frozen=True)
class View:
    """One scope (presets expanded, paths resolved). Lists are de-duplicated, order kept."""

    grants: tuple[RGrant, ...] = ()
    protected: tuple[RProtected, ...] = ()
    locked: tuple[str, ...] = ()  # absolute paths or ``**/`` globs
    read_deny: tuple[str, ...] = ()  # absolute paths or ``**/`` globs
    commands: Commands = Commands()
    raw: Raw = Raw()
    closed: Closed | None = None


@dataclass(frozen=True)
class Platform:
    """Facts about this machine that compilation depends on. Detected by ``platform.detect``."""

    home: Path
    guard_home: Path  # where policy.toml and applied.json live
    hook_command: tuple[str, ...]  # executable (+ leading args) that runs ``guard``
    system_drive: Path | None = None  # WSL: the Windows system drive, e.g. /mnt/c
    drives: tuple[Path, ...] = ()  # WSL: every mounted /mnt/<letter>
    caches: tuple[Path, ...] = ()  # tool cache folders, asked from each tool
    settings_scripts: tuple[Path, ...] = ()  # files that settings run (hooks, statusLine ...)
    managed_settings: Path = Path("/etc/claude-code")

    @property
    def user_settings(self) -> Path:
        return self.home / ".claude" / "settings.json"


# ---- compiled output ------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One settings entry: an item of an array, or a scalar value."""

    kind: str  # "array" | "scalar"
    path: str  # dotted key path, e.g. "permissions.allow"
    value: Any

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "value": self.value}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Entry:
        return cls(d["kind"], d["path"], d["value"])

    def same_slot(self, other: Entry) -> bool:
        """Scalars occupy a key; array entries are identified by their value too."""
        if self.kind != other.kind or self.path != other.path:
            return False
        return self.kind == "scalar" or self.value == other.value


@dataclass
class Compiled:
    files: dict[Path, list[Entry]]  # desired entries per settings file
    hook: dict[str, Any]  # what the hook needs (stored in applied.json)
    warnings: list[str] = field(default_factory=list)
