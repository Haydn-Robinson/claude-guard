"""Compile a resolved policy into settings entries per file, hook data and warnings.

Pure: reads the filesystem (does a folder exist, is it a folder) but writes nothing. Design
section 7. Each intent compiles to the fewest entries that enforce it:

=========  =====================================  =====================================
access     Edit/Write tools                       Bash (sandbox)
=========  =====================================  =====================================
full       ``Edit(//p/**)`` allow                 nothing more: the allow reaches the sandbox (VB-S7)
bash       closed: the hook asks                  ``allowWrite p`` (VB-S1)
edit       ``Edit(//p/**)`` allow                 ``denyWrite p`` (VB-S8)
modify     as edit; the hook refuses Write        ``denyWrite p``
ask        ``Edit(//p/**)`` ask (VB-E4)           ``denyWrite p``
deny       ``Edit(//p/**)`` deny (VB-E5)          covered by the same rule (VB-S7)
=========  =====================================  =====================================
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import CompileError
from .model import Access, Closed, Compiled, Entry, Platform, RGrant, RProtected, View
from .policy import Resolved
from .rules import concrete_patterns, glob_matches, has_glob, is_within, pattern_rules, rule_path

HOOK_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"  # NotebookEdit reaches the hook (VB-H8)
HOOK_DATA_VERSION = 1

ALLOW, ASK, DENY = "permissions.allow", "permissions.ask", "permissions.deny"
ALLOW_WRITE = "sandbox.filesystem.allowWrite"
# denyWrite beats every allow, with no sub-path exception (VB-S8); the plain folder form (VB-S9)
DENY_WRITE = "sandbox.filesystem.denyWrite"
HOOKS = "hooks.PreToolUse"

_TOOLS_RANK = {"deny": 0, "ask": 1, "allow": 2}
_NO = object()


def project_settings(project: Path) -> Path:
    """Read only by sessions in that project (VB-P1), and personal (not committed)."""
    return project / ".claude" / "settings.local.json"


@dataclass
class _Out:
    entries: list[Entry] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def add(self, kind: str, path: str, value: Any) -> None:
        key = json.dumps([kind, path, value if kind == "array" else None], sort_keys=True)
        if key not in self._seen:
            self._seen.add(key)
            self.entries.append(Entry(kind, path, value))

    def arr(self, path: str, values: Iterable[Any]) -> None:
        for v in values:
            self.add("array", path, v)


# ---- merging a project with everywhere ----------------------------------------------------------


def merge(ev: View, pv: View) -> View:
    """What applies in a project's sessions. A project can add protected paths or lower a ceiling,
    never raise one (design 6.1)."""
    protected: dict[Path, RProtected] = {p.path: p for p in ev.protected}
    for p in pv.protected:
        prev = protected.get(p.path)
        if prev is None or p.ceiling.rank < prev.ceiling.rank:
            protected[p.path] = p
    closed = [c for c in (ev.closed, pv.closed) if c]
    return View(
        grants=ev.grants + pv.grants,
        protected=tuple(protected.values()),
        locked=tuple(dict.fromkeys(ev.locked + pv.locked)),
        read_deny=tuple(dict.fromkeys(ev.read_deny + pv.read_deny)),
        commands=pv.commands,
        raw=pv.raw,
        closed=Closed.DENY if Closed.DENY in closed else (Closed.ASK if closed else None),
    )


# ---- checks (design 7.4) ----------------------------------------------------------------------------


def _describe(g: RGrant) -> str:
    return f"{g.path} (access {g.access.value}, {g.origin})"


def _nested_conflict(outer: RGrant, inner: RGrant) -> str | None:
    """Why ``inner`` (inside or equal to ``outer``) cannot get what it asks for, or None.

    Deny beats ask beats allow for rules, and denyWrite beats any allow (VB-R3, VB-S8), so a grant
    can only be stricter than a grant around it.
    """
    to, ti = outer.access.tools, inner.access.tools
    if ti in _TOOLS_RANK and to in _TOOLS_RANK and _TOOLS_RANK[ti] > _TOOLS_RANK[to]:
        return f"the Edit/Write tools would be '{to}' there, not '{ti}'"
    if inner.access.bash_writes and not outer.access.bash_writes and outer.access.tools != "closed":
        return "Bash would be read-only there (denyWrite beats any allow, VB-S8)"
    return None


def _check_grants(grants: Iterable[RGrant], protected: Iterable[RProtected], locked: Iterable[str]):
    grants, protected, locked = list(grants), list(protected), list(locked)
    for i, outer in enumerate(grants):
        for j, inner in enumerate(grants):
            if i == j or not is_within(inner.path, outer.path):
                continue
            if inner.path == outer.path and j < i:
                continue  # same folder in two scopes: the later (project) one is the inner one
            why = _nested_conflict(outer, inner)
            if why:
                raise CompileError(
                    f"grant {_describe(inner)} is inside grant {_describe(outer)}: {why}. "
                    "A grant can only be stricter than a grant around it."
                )
    for g in grants:
        for p in protected:
            if is_within(g.path, p.path) and g.access.rank > p.ceiling.rank:
                raise CompileError(
                    f"grant {_describe(g)} is above the ceiling '{p.ceiling.value}' of protected "
                    f"path {p.path} ({p.origin}). Lower the access, or raise the ceiling in "
                    "[everywhere]."
                )
        if g.access is Access.DENY:
            continue
        for pat in locked:
            if glob_matches(pat, g.path):
                raise CompileError(
                    f"grant {_describe(g)} is on a locked path ({pat}). Settings files change only "
                    "through `guard propose` and `guard review`."
                )


def _check_scalars(settings: Iterable[tuple[str, Any, str]]) -> dict[str, Any]:
    seen: dict[str, tuple[Any, str]] = {}
    for path, value, origin in settings:
        if path in seen and seen[path][0] != value:
            raise CompileError(
                f"setting {path} is set to {seen[path][0]!r} ({seen[path][1]}) and to {value!r} "
                f"({origin})"
            )
        seen.setdefault(path, (value, origin))
    return {k: v for k, (v, _) in seen.items()}


# ---- entries --------------------------------------------------------------------------------------


def _grant_entries(out: _Out, g: RGrant, warnings: list[str]) -> None:
    folder = f"Edit({rule_path(g.path)}/**)"
    match g.access:
        case Access.FULL:
            out.add("array", ALLOW, folder)  # reaches the sandbox too (VB-S7)
        case Access.BASH:
            out.add("array", ALLOW_WRITE, str(g.path))  # VB-S1
        case Access.EDIT | Access.MODIFY:
            out.add("array", ALLOW, folder)  # VB-E3
            out.add("array", DENY_WRITE, str(g.path))
        case Access.ASK:
            out.add("array", ASK, folder)  # prompts even in auto mode (VB-E4, VB-E10)
            out.add("array", DENY_WRITE, str(g.path))
        case Access.DENY:
            out.add("array", DENY, folder)  # VB-E5
    for glob in g.protect:
        out.add("array", DENY, f"Edit({rule_path(g.path)}/{glob})")
        if g.access.bash_writes and has_glob(glob):
            warnings.append(
                f"protect {glob!r} in {g.path}: a wildcard deny rule stops the Edit/Write tools, "
                "but may not stop Bash, which can write in this folder (VB-S14)."
            )
    if g.access is not Access.DENY and not g.path.exists():
        warnings.append(
            f"{g.path} does not exist. The sandbox cannot create it, so Bash writes there fail "
            "until it exists (VB-S5)."
        )


def _locked_in_grant(out: _Out, g: RGrant, locked: Iterable[str], warnings: list[str]) -> None:
    """`**/` deny globs do not reach the sandbox (VB-S14), and whether the built-in protection of
    `.claude/` covers granted folders is open (VB-U17), so name the files where Bash can write."""
    for pat in locked:
        if not pat.startswith("**/"):
            continue
        rest = pat[3:]
        if has_glob(rest):
            warnings.append(
                f"locked {pat!r} cannot be named exactly inside {g.path}, where Bash can write: "
                "the deny rule stops only the Edit/Write tools there (VB-S14)."
            )
        else:
            out.add("array", DENY_WRITE, str(g.path / rest))


def _protected_deny_write(
    p: RProtected, regions: list[Path], context: list[RGrant], warnings: list[str]
) -> bool:
    """Does protected path ``p`` need a ``denyWrite`` entry in this file?

    Needed when Bash can write around it (the project, or a grant with Bash write access) and no
    grant opens it. A grant on a parent folder never opens a protected path (design 6.1).
    """
    if p.ceiling is Access.DENY or not any(is_within(p.path, r) for r in regions):
        return False
    opening = [g for g in context if is_within(g.path, p.path)]
    if opening:
        if all(g.path != p.path for g in opening):
            warnings.append(
                f"protected {p.path} is partly opened by a grant inside it, and Bash can write "
                "around it, so Bash can write anywhere in it (denyWrite cannot make an exception, "
                "VB-S8). Grant the protected path itself, or narrow the grant around it."
            )
        return False
    readonly = [
        g for g in context if not g.access.bash_writes and is_within(p.path, g.path)
    ]  # already read-only to Bash through a stricter grant around it
    return not readonly


def _hook_entry(plat: Platform) -> dict[str, Any]:
    exe, *pre = plat.hook_command
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": exe,
                "args": [*pre, "hook", "edit", "--guard-home", str(plat.guard_home)],
                "onFailure": "block",  # without it a crashing hook lets the write through (VB-H3)
            }
        ],
    }


def _scope_entries(
    own: View,
    context: View,
    regions: list[Path],
    plat: Platform,
    hook: bool,
    skip: View | None,
    warnings: list[str],
) -> _Out:
    """Entries for one settings file.

    ``own`` is what this scope declares; ``context`` is everything in force in its sessions (for a
    project: merged with everywhere); ``skip`` is what the user file already covers.
    """
    out = _Out()
    for g in own.grants:
        _grant_entries(out, g, warnings)

    skip_deny = {p.path for p in skip.protected if p.ceiling is Access.DENY} if skip else set()
    for p in context.protected:
        if p.ceiling is Access.DENY and p.path not in skip_deny:
            out.arr(DENY, (f"Edit({x})" for x in concrete_patterns(p.path)))  # VB-E5
    for p in context.protected:
        if _protected_deny_write(p, regions, list(context.grants), warnings):
            out.add("array", DENY_WRITE, str(p.path))

    skip_locked = set(skip.locked) if skip else set()
    for pat in context.locked:
        if pat not in skip_locked:
            out.arr(DENY, (f"Edit({x})" for x in pattern_rules(pat)))
    for g in own.grants:
        if g.access.bash_writes:
            _locked_in_grant(out, g, context.locked, warnings)

    out.arr(ALLOW, (f"Bash({c})" for c in own.commands.allow))
    out.arr(ASK, (f"Bash({c})" for c in own.commands.ask))
    out.arr(DENY, (f"Bash({c})" for c in own.commands.deny))
    skip_read = set(skip.read_deny) if skip else set()
    for pat in context.read_deny:
        if pat not in skip_read:
            out.arr(DENY, (f"Read({x})" for x in pattern_rules(pat)))

    out.arr(ALLOW, own.raw.allow)
    out.arr(ASK, own.raw.ask)
    out.arr(DENY, own.raw.deny)
    out.arr(ALLOW_WRITE, own.raw.allow_write)
    out.arr(DENY_WRITE, own.raw.deny_write)
    for path, value in own.raw.settings:
        out.add("scalar", path, value)
    if hook:
        out.add("array", HOOKS, _hook_entry(plat))
    return out


# ---- hook data ------------------------------------------------------------------------------------


def _view_json(v: View) -> dict[str, Any]:
    return {
        "closed": v.closed.value if v.closed else None,
        "grants": [
            {"path": str(g.path), "access": g.access.value, "protect": list(g.protect)}
            for g in v.grants
        ],
        "protected": [{"path": str(p.path), "ceiling": p.ceiling.value} for p in v.protected],
        "locked": list(v.locked),
    }


# ---- entry point ----------------------------------------------------------------------------------


def compile_policy(res: Resolved, plat: Platform) -> Compiled:
    warnings: list[str] = []
    ev = res.everywhere
    merged = {proj: merge(ev, pv) for proj, pv in res.projects.items()}

    _check_grants(ev.grants, ev.protected, ev.locked)
    for m in merged.values():
        _check_grants(m.grants, m.protected, m.locked)
    ev_settings = _check_scalars((k, v, "[everywhere]") for k, v in ev.raw.settings)
    for proj, pv in res.projects.items():
        _check_scalars(
            [(k, v, "[everywhere]") for k, v in ev.raw.settings]
            + [(k, v, f'[projects."{proj}"]') for k, v in pv.raw.settings]
        )

    files: dict[Path, list[Entry]] = {}
    ev_regions = [g.path for g in ev.grants if g.access.bash_writes]
    user = _scope_entries(ev, ev, ev_regions, plat, ev.closed is not None, None, warnings)
    if user.entries:
        files[plat.user_settings] = user.entries
    user_deny_write = {e.value for e in user.entries if e.path == DENY_WRITE}

    for proj, pv in res.projects.items():
        for g in pv.grants:
            for q in user_deny_write:
                if g.access.bash_writes and is_within(g.path, Path(q)):
                    raise CompileError(
                        f"grant {_describe(g)} would stay read-only to Bash: the user settings "
                        f"make {q} read-only for every project (denyWrite beats any allow, VB-S8). "
                        "Open it in [everywhere] instead."
                    )
        regions = [proj] + [g.path for g in pv.grants if g.access.bash_writes]
        own = View(
            grants=pv.grants,
            commands=pv.commands,
            raw=replace(  # a value the user file already sets needs no second entry
                pv.raw,
                settings=tuple((k, v) for k, v in pv.raw.settings if ev_settings.get(k, _NO) != v),
            ),
        )
        # registered once: if the user file has it, the de-duplication below drops this copy
        hook = pv.closed is not None
        out = _scope_entries(own, merged[proj], regions, plat, hook, ev, warnings)
        already = {json.dumps(e.to_json(), sort_keys=True) for e in user.entries}
        entries = [e for e in out.entries if json.dumps(e.to_json(), sort_keys=True) not in already]
        if entries:
            files[project_settings(proj)] = entries

    hook_data = {
        "version": HOOK_DATA_VERSION,
        "home": str(plat.home),
        "scopes": {
            "*": _view_json(ev),
            **{str(proj): _view_json(m) for proj, m in merged.items()},
        },
    }
    return Compiled(files=files, hook=hook_data, warnings=list(dict.fromkeys(warnings)))
