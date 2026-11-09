"""Read-only views over Claude Code settings: effective values, `status` data, `doctor` checks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .engine import ALLOW_WRITE, PERM_ALLOW, PERM_ASK, PERM_DENY
from .pathsafety import PathPolicy
from .state import load_state

TRACKED_ARRAYS = (PERM_ALLOW, PERM_ASK, PERM_DENY, ALLOW_WRITE)


def user_settings(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def local_settings(project: Path) -> Path:
    return project / ".claude" / "settings.local.json"


def shared_settings(project: Path) -> Path:
    return project / ".claude" / "settings.json"


def dig(data: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    node: Any = data
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
    return True, node


@dataclass
class Source:
    label: str
    path: Path
    data: dict[str, Any] | None = None  # None: missing
    error: str | None = None


@dataclass
class Effective:
    """Settings sources in precedence order (highest first). Arrays merge, scalars: first wins."""

    sources: list[Source] = field(default_factory=list)

    @property
    def errors(self) -> list[Source]:
        return [s for s in self.sources if s.error]

    def scalar(self, dotted: str) -> tuple[bool, Any, str | None]:
        for src in self.sources:
            if src.data is not None:
                found, val = dig(src.data, dotted)
                if found:
                    return True, val, src.label
        return False, None, None

    def array(self, dotted: str) -> list[str]:
        out: list[str] = []
        for src in self.sources:
            if src.data is not None:
                found, val = dig(src.data, dotted)
                if found and isinstance(val, list):
                    out.extend(v for v in val if isinstance(v, str))
        return out


def _read(label: str, path: Path, override: dict[Path, str | None]) -> Source:
    try:
        if path in override:
            text = override[path]
            if text is None:
                return Source(label, path)
        else:
            text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Source(label, path)
    except OSError as e:
        return Source(label, path, error=str(e))
    if not text.strip():
        return Source(label, path, data={})
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return Source(label, path, error=f"invalid JSON: {e}")
    if not isinstance(data, dict):
        return Source(label, path, error="top level is not an object")
    return Source(label, path, data=data)


def load_effective(
    home: Path,
    project: Path,
    managed_dir: Path,
    override: dict[Path, str | None] | None = None,
) -> Effective:
    """``override`` substitutes planned file contents (None = file deleted) for "what if" checks."""
    ov = override or {}
    managed = [managed_dir / "managed-settings.json"]
    drop_in = managed_dir / "managed-settings.d"
    if drop_in.is_dir():
        managed = sorted(drop_in.glob("*.json"), reverse=True) + managed
    srcs = [_read("managed", p, ov) for p in managed]
    srcs += [
        _read("local", local_settings(project), ov),
        _read("project", shared_settings(project), ov),
        _read("user", user_settings(home), ov),
    ]
    return Effective(srcs)


# ---- status ----------------------------------------------------------------------------------


def _managed_values(frec: dict[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for erec in frec["entries"].values():
        if erec["kind"] == "array" and not erec["preexisting"] and isinstance(erec["value"], str):
            out.setdefault(erec["path"], set()).add(erec["value"])
    return out


def build_status(home: Path, project: Path, state_file: Path) -> dict[str, Any]:
    state = load_state(state_file)
    wanted = [("user", user_settings(home)), ("local", local_settings(project))]
    seen = {str(p) for _, p in wanted}
    extra = sorted({g["settings_file"] for g in state["grants"].values()} - seen)
    files = []
    for scope, path in [*wanted, *[("other", Path(p)) for p in extra]]:
        src = _read(scope, path, {})
        frec = state["files"].get(str(path), {"entries": {}})
        grants = []
        for gid, g in state["grants"].items():
            if g["settings_file"] != str(path):
                continue
            prefix = f"Edit(//{g['folder'].lstrip('/')}/" if g["folder"] else None
            entries, protected = [], []
            for f, key in g["entries"]:
                erec = state["files"][f]["entries"][key]
                entries.append(
                    {
                        "path": erec["path"],
                        "value": erec["value"],
                        "shared": len(erec["refs"]) > 1,
                        "preexisting": erec["preexisting"],
                    }
                )
                v = erec["value"]
                if (
                    prefix
                    and erec["path"] == PERM_DENY
                    and isinstance(v, str)
                    and v.startswith(prefix)
                ):
                    protected.append(v[len(prefix) : -1])
            grants.append(
                {
                    "id": gid,
                    "folder": g["folder"],
                    "profiles": g["profiles"],
                    "protect": g["protect"],
                    "protected_paths": protected,
                    "entries": entries,
                }
            )
        unmanaged: dict[str, list[Any]] = {}
        if src.data is not None and scope != "other":
            managed = _managed_values(frec)
            for dotted in TRACKED_ARRAYS:
                found, arr = dig(src.data, dotted)
                if found and isinstance(arr, list):
                    rest = [v for v in arr if v not in managed.get(dotted, set())]
                    if rest:
                        unmanaged[dotted] = rest
        files.append(
            {
                "scope": scope,
                "path": str(path),
                "exists": src.data is not None,
                "error": src.error,
                "grants": grants,
                "unmanaged": unmanaged,
            }
        )
    return {"project": str(project), "files": files}


def _fmt_value(v: Any) -> str:
    if isinstance(v, dict) and "hooks" in v:
        cmds = " | ".join(
            " ".join([h.get("command", ""), *map(str, h.get("args", []))]) for h in v["hooks"]
        )
        return f"hook {v.get('matcher', '')} -> {cmds}".replace("hook ", "hook PreToolUse ", 1)
    return str(v)


def render_status(status: dict[str, Any]) -> str:
    out = [f"Project: {status['project']}", ""]
    titles = {"user": "User settings", "local": "Local settings", "other": "Other managed file"}
    for f in status["files"]:
        note = "" if f["exists"] else "  [file not present]"
        if f["error"]:
            note = f"  [ERROR: {f['error']}]"
        out.append(f"{titles[f['scope']]}: {f['path']}{note}")
        if not f["grants"]:
            out.append("  (no grants managed by claude-guard)")
        for g in f["grants"]:
            out.append(f"  {g['folder'] or '(global)'}   profiles: {', '.join(g['profiles'])}")
            if g["protected_paths"]:
                out.append(f"    protected: {', '.join(g['protected_paths'])}")
            for e in g["entries"]:
                tag = " (shared)" if e["shared"] else ""
                tag += " (already present, not removed on revoke)" if e["preexisting"] else ""
                out.append(f"    {e['path']}: {_fmt_value(e['value'])}{tag}")
        for dotted, vals in f["unmanaged"].items():
            out.append(f"  unmanaged {dotted}:")
            out.extend(f"    {v}" for v in vals)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---- doctor ----------------------------------------------------------------------------------


@dataclass
class Check:
    level: str  # "ok" | "warn" | "fail"
    name: str
    detail: str = ""


def _managed_hooks(state_file: Path) -> list[dict[str, Any]]:
    try:
        state = load_state(state_file)
    except Exception:  # noqa: BLE001 - reported by the state check
        return []
    return [
        e["value"]
        for frec in state["files"].values()
        for e in frec["entries"].values()
        if e["kind"] == "array" and e["path"] == "hooks.PreToolUse" and isinstance(e["value"], dict)
    ]


def _hook_mode(entry: dict[str, Any]) -> str:
    for h in entry.get("hooks", []):
        args = h.get("args", [])
        if "--unlisted" in args:
            return str(args[args.index("--unlisted") + 1])
    return "?"


def run_doctor(
    home: Path,
    project: Path,
    state_file: Path,
    managed_dir: Path,
    policy: PathPolicy,
    which: Any,
) -> list[Check]:
    checks: list[Check] = []

    def add(level: str, name: str, detail: str = "") -> None:
        checks.append(Check(level, name, detail))

    for tool in ("bwrap", "socat"):
        if which(tool):
            add("ok", f"{tool} installed")
        else:
            add(
                "fail",
                f"{tool} installed",
                "needed by the native sandbox: apt install bubblewrap socat",
            )

    eff = load_effective(home, project, managed_dir)
    for src in eff.errors:
        add("fail", f"settings JSON valid: {src.path}", src.error or "")
    if not eff.errors:
        add("ok", "settings JSON valid", "user, project, local and managed files parse")

    found, val, where = eff.scalar("sandbox.enabled")
    sandbox_on = found and val is True
    if sandbox_on:
        add("ok", "sandbox enabled", f"set in {where} settings")
    else:
        add(
            "fail",
            "sandbox enabled",
            "not enabled in effective settings; Bash writes are NOT "
            "confined. Fix: guard allow --global lock",
        )
    found, val, where = eff.scalar("sandbox.allowUnsandboxedCommands")
    if found and val is False:
        add("ok", "allowUnsandboxedCommands is false", f"set in {where} settings")
    else:
        add(
            "warn",
            "allowUnsandboxedCommands is false",
            "unset or true: Claude may retry commands "
            "outside the sandbox (prompts in most modes). Fix: guard allow --global lock",
        )
    found, val, where = eff.scalar("permissions.disableBypassPermissionsMode")
    if found and val == "disable":
        add("ok", "bypass-permissions mode disabled", f"set in {where} settings")
    else:
        add(
            "warn",
            "bypass-permissions mode disabled",
            "not disabled. Fix: guard allow --global lock",
        )
    hook_entries = _managed_hooks(state_file)
    if hook_entries:
        modes = sorted({_hook_mode(h) for h in hook_entries})
        add("ok", "Edit/Write hook", f"registered ({', '.join(modes)})")
    found, val, where = eff.scalar("permissions.defaultMode")
    if found and val == "auto" and not hook_entries:
        add(
            "warn",
            "Edit/Write tools in auto mode",
            "defaultMode is auto: the Edit/Write tools are not sandboxed and can write outside "
            "granted folders without a prompt; only Edit deny rules protect other paths. "
            "Fix: guard allow --global confine-edits",
        )
    for h in hook_entries:
        for cmd in {c.get("command", "") for c in h.get("hooks", [])}:
            if not (os.path.isfile(cmd) and os.access(cmd, os.X_OK)):
                add(
                    "fail",
                    "Edit/Write hook command",
                    f"{cmd} is missing or not executable. The hook is fail-closed, so every "
                    "Edit/Write is blocked until this is fixed: re-run guard allow --global "
                    "confine-edits from the installed guard, or revoke it",
                )

    # state vs settings
    try:
        state = load_state(state_file)
    except Exception as e:  # noqa: BLE001 - report, don't crash
        add("fail", "state file valid", str(e))
        return checks
    add("ok", "state file valid", f"{len(state['grants'])} grant(s)")

    problems = []
    for gid, g in state["grants"].items():
        for f, key in g["entries"]:
            erec = state["files"].get(f, {}).get("entries", {}).get(key)
            if erec is None or gid not in erec["refs"]:
                problems.append(f"grant {gid}: entry missing from state bookkeeping")
    for f, frec in state["files"].items():
        for key, erec in frec["entries"].items():
            for ref in erec["refs"]:
                if ref not in state["grants"]:
                    problems.append(f"{f}: entry {erec['value']!r} references unknown grant {ref}")
    for p in problems:
        add("fail", "state consistent", p)
    if not problems:
        add("ok", "state consistent")

    edited = []
    for f, frec in state["files"].items():
        src = _read("x", Path(f), {})
        if src.error:
            continue  # already reported above if effective; otherwise a stale file
        if src.data is None:
            edited.append(f"{f}: file is missing but has managed entries")
            continue
        for erec in frec["entries"].values():
            found, val = dig(src.data, erec["path"])
            if erec["kind"] == "array":
                ok = found and isinstance(val, list) and erec["value"] in val
            else:
                ok = found and val == erec["value"]
            if not ok:
                edited.append(f"{f}: {erec['path']} {erec['value']!r} changed or removed by hand")
    for e in edited:
        add("warn", "managed entries intact", e)
    if not edited:
        add("ok", "managed entries intact")

    for g in state["grants"].values():
        folder = g["folder"]
        if not folder:
            continue
        p = Path(folder)
        if not p.exists():
            add("warn", f"granted folder exists: {folder}", "missing: the sandbox cannot create it")
        if policy.system_drive in p.parents or p == policy.system_drive:
            add("warn", f"granted folder on system drive: {folder}", "sensitive: double-check")

    if not sandbox_on:
        risky = [
            e["value"]
            for frec in state["files"].values()
            for e in frec["entries"].values()
            if e["kind"] == "array" and e["path"] == PERM_ALLOW and e["value"].startswith("Bash(")
        ]
        if risky:
            add(
                "warn",
                "Bash allow rules without sandbox",
                f"{len(risky)} Bash allow rule(s) are "
                "global and only safe when the sandbox bounds writes; the sandbox is off",
            )
    return checks


def render_doctor(checks: list[Check]) -> str:
    tag = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]"}
    lines = []
    for c in checks:
        lines.append(f"{tag[c.level]} {c.name}" + (f": {c.detail}" if c.detail else ""))
    n_fail = sum(c.level == "fail" for c in checks)
    n_warn = sum(c.level == "warn" for c in checks)
    lines.append("")
    lines.append(f"{n_fail} failure(s), {n_warn} warning(s)")
    return "\n".join(lines) + "\n"
