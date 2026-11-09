"""policy.toml: parse and validate, expand presets, resolve paths, and edit it preserving format.

* ``parse_policy`` checks everything that does not depend on the machine: keys, types, access
  levels, path syntax. Errors name the location (``projects."/p".write[1].access``).
* ``resolve`` expands presets and placeholders, resolves ``~`` and symlinks, and refuses unsafe
  grant folders. Its output (``Resolved``) is what ``compile`` works on.
* ``PolicyFile`` makes the high-level edits (``guard allow``/``revoke``/``protect``) with tomlkit, so
  comments and layout survive.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import tomlkit
import tomlkit.exceptions
from tomlkit.items import AoT, Array, InlineTable, Table

from .errors import PolicyError, UnsafeFolderError
from .model import (
    Access,
    Closed,
    Commands,
    Grant,
    Platform,
    Policy,
    Protected,
    Raw,
    RGrant,
    RProtected,
    Scope,
    View,
)
from .pathsafety import DEFAULT_SYSTEM_DRIVE, PathPolicy
from .rules import has_glob, is_within

POLICY_VERSION = 1

PLACEHOLDERS = frozenset(
    {"system_drive", "drives", "caches", "settings_scripts", "guard_home", "managed_settings"}
)
_PLACEHOLDER = re.compile(r"^\{([a-z_]+)\}(/.*)?$")

_SCOPE_KEYS = {"presets", "write", "protected", "locked", "read", "commands", "raw", "closed"}

NEW_POLICY = """\
# claude-guard policy. Edit with `guard edit`, or let `guard allow` / `revoke` / `protect` do it.
# Design and schema: docs/design.md, section 5.
version = 1

# Applies to every session; compiled into ~/.claude/settings.json.
[everywhere]
presets = ["baseline"]
"""


# ---- parsing --------------------------------------------------------------------------------------


def _fail(where: str, msg: str) -> PolicyError:
    return PolicyError(f"{where}: {msg}" if where else msg)


def _table(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(where, "must be a table")
    return value


def _keys(d: dict[str, Any], allowed: Iterable[str], where: str) -> None:
    for k in d:
        if k not in allowed:
            raise _fail(where, f"unknown key {k!r} (expected one of: {', '.join(sorted(allowed))})")


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(where, "must be a list of strings")
    for i, v in enumerate(value):
        if not isinstance(v, str):
            raise _fail(f"{where}[{i}]", "must be a string")
    return tuple(value)


def _check_path(raw: Any, where: str, *, placeholders: bool = True, globs: bool = False) -> str:
    """Absolute, ``~``-relative or placeholder-prefixed (``{drives}/x``)."""
    if not isinstance(raw, str) or not raw:
        raise _fail(where, "must be a non-empty string")
    m = _PLACEHOLDER.match(raw)
    if m:
        if not placeholders or m.group(1) not in PLACEHOLDERS:
            raise _fail(where, f"unknown placeholder {{{m.group(1)}}} in {raw!r}")
    elif "{" in raw:
        raise _fail(where, f"bad placeholder in {raw!r} (it must start the path)")
    elif not (raw.startswith(("/", "~/")) or raw == "~"):
        raise _fail(where, f"{raw!r} must be an absolute path (or start with ~/)")
    if not globs and has_glob(raw):
        raise _fail(where, f"{raw!r} contains glob characters (* ? [ ]), which grants cannot use")
    return raw


def _check_pattern(raw: Any, where: str) -> str:
    """A path as in ``_check_path`` (globs allowed), or a ``**/`` glob matching at any depth."""
    if isinstance(raw, str) and raw.startswith("**/") and len(raw) > 3:
        return raw
    return _check_path(raw, where, globs=True)


def _check_protect_glob(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw or raw.startswith(("/", "~")) or ".." in raw.split("/"):
        raise _fail(where, f"{raw!r} must be relative to the granted folder, without '..'")
    return raw


def _access(raw: Any, where: str) -> Access:
    if not isinstance(raw, str):
        raise _fail(where, "must be a string access level")
    try:
        return Access.parse(raw)
    except ValueError as e:
        raise _fail(where, str(e)) from None


def _grant(raw: Any, where: str) -> Grant:
    d = _table(raw, where)
    _keys(d, {"path", "access", "protect", "broad"}, where)
    if "access" not in d:
        raise _fail(where, "missing 'access'")
    broad = d.get("broad", False)
    if not isinstance(broad, bool):
        raise _fail(f"{where}.broad", "must be true or false")
    protect = _strings(d.get("protect", []), f"{where}.protect")
    return Grant(
        path=_check_path(d.get("path"), f"{where}.path"),
        access=_access(d["access"], f"{where}.access"),
        protect=tuple(
            _check_protect_glob(g, f"{where}.protect[{i}]") for i, g in enumerate(protect)
        ),
        broad=broad,
    )


def _protected(raw: Any, where: str) -> Protected:
    d = _table(raw, where)
    _keys(d, {"path", "ceiling"}, where)
    if "ceiling" not in d:
        raise _fail(where, "missing 'ceiling'")
    return Protected(
        _check_path(d.get("path"), f"{where}.path"), _access(d["ceiling"], f"{where}.ceiling")
    )


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise _fail(where, "must be a list")
    return value


def _raw(d: dict[str, Any], where: str) -> Raw:
    _keys(d, {"allow", "ask", "deny", "sandbox", "settings"}, where)
    sandbox = _table(d.get("sandbox", {}), f"{where}.sandbox")
    _keys(sandbox, {"allow_write", "deny_write"}, f"{where}.sandbox")
    sb = {}
    for key in ("allow_write", "deny_write"):
        w = f"{where}.sandbox.{key}"
        sb[key] = tuple(
            _check_path(p, f"{w}[{i}]", placeholders=False, globs=True)
            for i, p in enumerate(_strings(sandbox.get(key, []), w))
        )
    settings = []
    for i, item in enumerate(_list(d.get("settings", []), f"{where}.settings")):
        w = f"{where}.settings[{i}]"
        s = _table(item, w)
        _keys(s, {"path", "value"}, w)
        path = s.get("path")
        if not isinstance(path, str) or not path or "" in path.split("."):
            raise _fail(f"{w}.path", "must be a dotted key path such as 'sandbox.enabled'")
        if "value" not in s:
            raise _fail(w, "missing 'value'")
        settings.append((path, s["value"]))
    return Raw(
        allow=_strings(d.get("allow", []), f"{where}.allow"),
        ask=_strings(d.get("ask", []), f"{where}.ask"),
        deny=_strings(d.get("deny", []), f"{where}.deny"),
        allow_write=sb["allow_write"],
        deny_write=sb["deny_write"],
        settings=tuple(settings),
    )


def _scope(raw: Any, where: str) -> Scope:
    d = _table(raw, where)
    _keys(d, _SCOPE_KEYS, where)
    read = _table(d.get("read", {}), f"{where}.read")
    _keys(read, {"deny"}, f"{where}.read")
    cmds = _table(d.get("commands", {}), f"{where}.commands")
    _keys(cmds, {"allow", "ask", "deny"}, f"{where}.commands")
    closed = d.get("closed")
    if closed is not None and closed not in ("ask", "deny"):
        raise _fail(f"{where}.closed", f"must be 'ask' or 'deny', not {closed!r}")
    w = f"{where}.write"
    p = f"{where}.protected"
    return Scope(
        presets=_strings(d.get("presets", []), f"{where}.presets"),
        write=tuple(_grant(g, f"{w}[{i}]") for i, g in enumerate(_list(d.get("write", []), w))),
        protected=tuple(
            _protected(x, f"{p}[{i}]") for i, x in enumerate(_list(d.get("protected", []), p))
        ),
        locked=tuple(
            _check_pattern(x, f"{where}.locked[{i}]")
            for i, x in enumerate(_strings(d.get("locked", []), f"{where}.locked"))
        ),
        read_deny=tuple(
            _check_pattern(x, f"{where}.read.deny[{i}]")
            for i, x in enumerate(_strings(read.get("deny", []), f"{where}.read.deny"))
        ),
        commands=Commands(
            allow=_strings(cmds.get("allow", []), f"{where}.commands.allow"),
            ask=_strings(cmds.get("ask", []), f"{where}.commands.ask"),
            deny=_strings(cmds.get("deny", []), f"{where}.commands.deny"),
        ),
        raw=_raw(_table(d.get("raw", {}), f"{where}.raw"), f"{where}.raw"),
        closed=Closed(closed) if closed else None,
    )


def _load_toml(text: str, source: str) -> dict[str, Any]:
    try:
        return tomlkit.parse(text).unwrap()
    except tomlkit.exceptions.ParseError as e:
        raise PolicyError(f"{source}: invalid TOML: {e}") from None


def parse_policy(text: str, source: str = "policy.toml") -> Policy:
    data = _load_toml(text, source)
    if data.get("version") != POLICY_VERSION:
        raise PolicyError(f"{source}: expected 'version = {POLICY_VERSION}' at the top")
    _keys(data, {"version", "everywhere", "projects", "presets"}, source)
    projects = {}
    for key, value in _table(data.get("projects", {}), "projects").items():
        where = f"projects.{json.dumps(key)}"
        _check_path(key, where, placeholders=False)
        projects[key] = _scope(value, where)
    presets = {
        name: _scope(value, f"presets.{name}")
        for name, value in _table(data.get("presets", {}), "presets").items()
    }
    return Policy(
        everywhere=_scope(data.get("everywhere", {}), "everywhere"),
        projects=projects,
        presets=presets,
    )


def load_policy(path: Path) -> Policy:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PolicyError(f"{path} does not exist: run `guard init`") from None
    return parse_policy(text, str(path))


@cache
def shipped_presets() -> dict[str, Scope]:
    text = resources.files("claude_guard").joinpath("presets.toml").read_text(encoding="utf-8")
    data = _load_toml(text, "presets.toml")
    return {name: _scope(value, f"shipped preset {name}") for name, value in data.items()}


# ---- resolution -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    everywhere: View
    projects: dict[Path, View]  # each project's own view, not merged with everywhere


def _real(p: str | Path) -> Path:
    return Path(os.path.realpath(p))


def _placeholder_values(name: str, plat: Platform) -> list[str]:
    values: dict[str, Iterable[Path | None]] = {
        "system_drive": [plat.system_drive],
        "drives": plat.drives,
        "caches": plat.caches,
        "settings_scripts": plat.settings_scripts,
        "guard_home": [plat.guard_home],
        "managed_settings": [plat.managed_settings],
    }
    return [str(v) for v in values[name] if v is not None]


def _expand(raw: str, plat: Platform) -> list[str]:
    """Placeholders and ``~``, as strings (not yet resolved)."""
    m = _PLACEHOLDER.match(raw)
    if m:
        return [v + (m.group(2) or "") for v in _placeholder_values(m.group(1), plat)]
    if raw == "~" or raw.startswith("~/"):
        return [str(plat.home) + raw[1:]]
    return [raw]


def expand_paths(raw: str, plat: Platform) -> list[Path]:
    return [_real(p) for p in _expand(raw, plat)]


def expand_pattern(raw: str, plat: Platform) -> list[str]:
    """Like ``expand_paths``, but keeps globs as written and a trailing ``/`` (a folder)."""
    if raw.startswith("**/"):
        return [raw]
    slash = "/" if raw.endswith("/") else ""
    return [p if has_glob(p) else str(_real(p)) + slash for p in _expand(raw, plat)]


def _dedupe(items: Iterable[Any]) -> tuple[Any, ...]:
    seen, out = set(), []
    for x in items:
        key = json.dumps(x, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return tuple(out)


@dataclass
class _Flat:
    grants: list[tuple[Grant, str]]
    protected: list[tuple[Protected, str, bool]]  # (entry, origin, from the scope itself)
    scopes: list[Scope]


def _flatten(scope: Scope, origin: str, presets: dict[str, Scope], stack: tuple[str, ...]) -> _Flat:
    flat = _Flat([], [], [])
    for name in scope.presets:
        if name in stack:
            raise PolicyError(f"preset cycle: {' -> '.join([*stack, name])}")
        if name not in presets:
            raise PolicyError(f"{origin}: unknown preset {name!r}")
        sub = _flatten(presets[name], f"preset {name}", presets, (*stack, name))
        flat.grants += sub.grants
        flat.protected += [(p, o, False) for p, o, _ in sub.protected]
        flat.scopes += sub.scopes
    flat.grants += [(g, origin) for g in scope.write]
    flat.protected += [(p, origin, True) for p in scope.protected]
    flat.scopes.append(scope)
    return flat


def _resolve_grants(flat: _Flat, plat: Platform, pathpolicy: PathPolicy) -> tuple[RGrant, ...]:
    out: dict[Path, RGrant] = {}
    for g, origin in flat.grants:
        for path in expand_paths(g.path, plat):
            if g.access is not Access.DENY:
                try:
                    pathpolicy.check_folder(path, force_broad=g.broad, system_area=False)
                except UnsafeFolderError as e:
                    hint = " (or set broad = true on the grant)" if e.overridable else ""
                    raise PolicyError(f"{origin}: {e}{hint}") from None
            prev = out.get(path)
            if prev is None:
                out[path] = RGrant(path, g.access, _dedupe(g.protect), origin)
            elif prev.access is not g.access:
                raise PolicyError(
                    f"{path} is granted twice with different access: {prev.access.value} "
                    f"({prev.origin}) and {g.access.value} ({origin})"
                )
            else:
                out[path] = RGrant(
                    path, g.access, _dedupe([*prev.protect, *g.protect]), prev.origin
                )
    return tuple(out.values())


def _resolve_protected(flat: _Flat, plat: Platform) -> tuple[RProtected, ...]:
    """The scope's own entries replace a preset's ceiling; otherwise the stricter one wins."""
    out: dict[Path, tuple[RProtected, bool]] = {}
    for p, origin, own in flat.protected:
        for path in expand_paths(p.path, plat):
            new = RProtected(path, p.ceiling, origin)
            prev = out.get(path)
            replaces = prev is None or (own and not prev[1])
            stricter = prev is not None and own == prev[1] and new.ceiling < prev[0].ceiling
            if replaces or stricter:
                out[path] = (new, own)
    return tuple(rp for rp, _ in out.values())


def _view(scope: Scope, origin: str, presets: dict[str, Scope], plat: Platform) -> View:
    flat = _flatten(scope, origin, presets, ())
    pathpolicy = PathPolicy(plat.home, plat.drives, plat.system_drive or DEFAULT_SYSTEM_DRIVE)
    scopes = flat.scopes

    def patterns(attr: str) -> tuple[str, ...]:
        return _dedupe(
            p for s in scopes for raw in getattr(s, attr) for p in expand_pattern(raw, plat)
        )

    def sandbox(attr: str) -> tuple[str, ...]:
        return _dedupe(
            p for s in scopes for raw in getattr(s.raw, attr) for p in _expand(raw, plat)
        )

    closed = [s.closed for s in scopes if s.closed]
    return View(
        grants=_resolve_grants(flat, plat, pathpolicy),
        protected=_resolve_protected(flat, plat),
        locked=patterns("locked"),
        read_deny=patterns("read_deny"),
        commands=Commands(
            allow=_dedupe(c for s in scopes for c in s.commands.allow),
            ask=_dedupe(c for s in scopes for c in s.commands.ask),
            deny=_dedupe(c for s in scopes for c in s.commands.deny),
        ),
        raw=Raw(
            allow=_dedupe(c for s in scopes for c in s.raw.allow),
            ask=_dedupe(c for s in scopes for c in s.raw.ask),
            deny=_dedupe(c for s in scopes for c in s.raw.deny),
            allow_write=sandbox("allow_write"),
            deny_write=sandbox("deny_write"),
            settings=_dedupe(kv for s in scopes for kv in s.raw.settings),
        ),
        closed=Closed.DENY if Closed.DENY in closed else (Closed.ASK if closed else None),
    )


def resolve_project_dir(raw: str, plat: Platform) -> Path:
    (path,) = expand_paths(raw, plat)
    home = _real(plat.home)
    if is_within(home, path):
        raise PolicyError(
            f'projects."{raw}": a project at or above the home folder ({path}) is not allowed'
        )
    return path


def resolve(policy: Policy, plat: Platform) -> Resolved:
    presets = {**shipped_presets(), **policy.presets}
    projects: dict[Path, View] = {}
    for raw, scope in policy.projects.items():
        path = resolve_project_dir(raw, plat)
        if path in projects:
            raise PolicyError(f'projects."{raw}": the same project ({path}) appears twice')
        projects[path] = _view(scope, f'[projects."{raw}"]', presets, plat)
    return Resolved(_view(policy.everywhere, "[everywhere]", presets, plat), projects)


# ---- format-preserving edits ----------------------------------------------------------------------


class PolicyFile:
    """policy.toml as a tomlkit document. Every edit is validated; a failed edit changes nothing."""

    def __init__(self, text: str, source: str = "policy.toml"):
        parse_policy(text, source)
        self.source = source
        self.doc = tomlkit.parse(text)

    @classmethod
    def new(cls) -> PolicyFile:
        return cls(NEW_POLICY)

    @classmethod
    def load(cls, path: Path) -> PolicyFile:
        try:
            return cls(path.read_text(encoding="utf-8"), str(path))
        except FileNotFoundError:
            raise PolicyError(f"{path} does not exist: run `guard init`") from None

    @property
    def text(self) -> str:
        return tomlkit.dumps(self.doc)

    @property
    def policy(self) -> Policy:
        return parse_policy(self.text, self.source)

    # ---- helpers ------------------------------------------------------------------------------
    def _edit(self, change) -> Any:
        before = self.text
        try:
            result = change()
            parse_policy(self.text, self.source)
        except Exception:
            self.doc = tomlkit.parse(before)
            raise
        return result

    def _scope(self, project: str | None, create: bool) -> Table | None:
        if project is None:
            if "everywhere" not in self.doc:
                if not create:
                    return None
                self.doc.add("everywhere", tomlkit.table())
            return self.doc["everywhere"]  # type: ignore[return-value]
        if "projects" not in self.doc:
            if not create:
                return None
            self.doc.add("projects", tomlkit.table(is_super_table=True))
        projects = self.doc["projects"]
        if project not in projects:  # type: ignore[operator]
            if not create:
                return None
            projects.add(project, tomlkit.table())  # type: ignore[union-attr]
        return projects[project]  # type: ignore[index]

    def _array(self, table: Table, key: str) -> Array:
        if key not in table:
            arr = tomlkit.array()
            arr.multiline(True)
            table.add(key, arr)
        arr = table[key]
        if isinstance(arr, AoT):
            raise PolicyError(f"{self.source}: {key} is written as [[...]]; edit it by hand")
        return arr  # type: ignore[return-value]

    def _drop_if_empty(self, project: str | None) -> None:
        if project is None:
            return
        projects = self.doc["projects"]
        if not any(len(v) if isinstance(v, list | dict) else 1 for v in projects[project].values()):  # type: ignore[index, union-attr]
            del projects[project]  # type: ignore[union-attr]
            if not projects:  # type: ignore[truthy-bool]
                del self.doc["projects"]

    @staticmethod
    def _find(arr: Array, path: str) -> int | None:
        for i, item in enumerate(arr):
            if isinstance(item, dict | InlineTable) and item.get("path") == path:
                return i
        return None

    # ---- edits --------------------------------------------------------------------------------
    def add_grant(
        self,
        project: str | None,
        path: str,
        access: Access,
        protect: Iterable[str] = (),
        broad: bool = False,
    ) -> None:
        """Add a grant, replacing any grant for the same path in the same scope."""

        def change() -> None:
            item = tomlkit.inline_table()
            item.update({"path": path, "access": access.value})
            if protect:
                item["protect"] = list(protect)
            if broad:
                item["broad"] = True
            arr = self._array(self._scope(project, create=True), "write")  # type: ignore[arg-type]
            i = self._find(arr, path)
            if i is None:
                arr.append(item)
            else:
                arr[i] = item

        self._edit(change)

    def remove_grant(self, project: str | None, path: str) -> bool:
        def change() -> bool:
            table = self._scope(project, create=False)
            if table is None or "write" not in table:
                return False
            arr = table["write"]
            i = self._find(arr, path)  # type: ignore[arg-type]
            if i is None:
                return False
            del arr[i]  # type: ignore[union-attr]
            if not arr:
                del table["write"]
            self._drop_if_empty(project)
            return True

        return self._edit(change)

    def set_protected(self, project: str | None, path: str, ceiling: Access) -> None:
        def change() -> None:
            item = tomlkit.inline_table()
            item.update({"path": path, "ceiling": ceiling.value})
            arr = self._array(self._scope(project, create=True), "protected")  # type: ignore[arg-type]
            i = self._find(arr, path)
            if i is None:
                arr.append(item)
            else:
                arr[i] = item

        self._edit(change)
