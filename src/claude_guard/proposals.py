"""Propose and review: how Claude Code settings change (design section 9).

Settings files are locked, so Claude never writes them. Claude runs ``guard propose``, which saves a
list of operations on one file under ``$TMPDIR`` (writable inside the sandbox, VB-S4). You run
``guard review`` in a separate terminal: it applies the operations to the file *as it is now*,
shows the diff and a plain-English note for each security-relevant change, and refuses changes to
entries guard manages. Accepting backs the file up and writes it atomically.

Proposals are untrusted input (Claude wrote them): every operation is shown before anything is
written, and only files owned by you are read.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .applied import BACKUP_DIR, Applied, atomic_write, backup_file
from .effective import dig
from .errors import GuardError, SettingsError
from .settingsfile import SettingsDoc

PROPOSAL_VERSION = 1
OP_KINDS = ("set", "unset", "add", "remove")


@dataclass(frozen=True)
class Op:
    kind: str  # "set" | "unset" | "add" | "remove"
    key: str  # dotted path
    value: Any = None

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "key": self.key}
        if self.kind != "unset":
            d["value"] = self.value
        return d

    def describe(self) -> str:
        if self.kind == "unset":
            return f"unset {self.key}"
        return f"{self.kind} {self.key} = {json.dumps(self.value, ensure_ascii=False)}"


@dataclass
class Proposal:
    id: str
    target: Path
    ops: list[Op]
    reason: str
    created: str


@dataclass
class Review:
    proposal: Proposal
    old_text: str | None
    new_text: str
    diff: str
    notes: list[str] = field(default_factory=list)  # security-relevant changes, in plain English
    refusals: list[str] = field(default_factory=list)  # changes to guard-owned entries


def _check_key(key: str) -> str:
    if not key or "" in key.split("."):
        raise GuardError(f"bad key {key!r}: use a dotted path such as statusLine.command")
    return key


def parse_op(kind: str, text: str) -> Op:
    """``KEY=VALUE`` (VALUE is JSON if it parses, else a string), or ``KEY`` for unset."""
    if kind not in OP_KINDS:
        raise GuardError(f"unknown operation {kind!r}")
    if kind == "unset":
        return Op("unset", _check_key(text.strip()))
    key, sep, raw = text.partition("=")
    if not sep:
        raise GuardError(f"--{kind} needs KEY=VALUE, got {text!r}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return Op(kind, _check_key(key.strip()), value)


def check_target(raw: str, home: Path) -> Path:
    """Only Claude Code settings files can be proposed for (design section 9)."""
    p = Path(raw).expanduser() if not raw.startswith("~") else home / raw[2:]
    if not p.is_absolute():
        raise GuardError(f"target {raw!r} must be an absolute path")
    p = Path(os.path.realpath(p))
    ok = (
        (p.parent.name == ".claude" and p.name in ("settings.json", "settings.local.json"))
        or p.name == ".mcp.json"
        or p == Path(os.path.realpath(home)) / ".claude.json"
    )
    if not ok:
        raise GuardError(
            f"target {p} is not a Claude Code settings file (.claude/settings.json, "
            ".claude/settings.local.json, .mcp.json or ~/.claude.json)"
        )
    return p


def proposal_dirs() -> list[Path]:
    """Where proposals are queued: Claude's ``$TMPDIR`` (``/tmp/claude-<uid>``, VB-S4) as seen from
    inside a session, and this process's temp dir."""
    dirs = [
        Path(tempfile.gettempdir()) / "guard-proposals",
        Path(f"/tmp/claude-{os.getuid()}") / "guard-proposals",
    ]
    return list(dict.fromkeys(dirs))


def apply_ops(doc: SettingsDoc, ops: list[Op]) -> None:
    for op in ops:
        match op.kind:
            case "set":
                doc.set_scalar(op.key, op.value)
            case "unset":
                doc.delete_scalar(op.key)
            case "add":
                doc.add_to_array(op.key, op.value)
            case "remove":
                if not doc.remove_from_array(op.key, op.value):
                    raise SettingsError(f"{op.key} has no item {json.dumps(op.value)}")


def propose(target: Path, ops: list[Op], reason: str, qdir: Path | None = None) -> Path:
    if not reason.strip():
        raise GuardError("a proposal needs a --reason the user can read")
    if not ops:
        raise GuardError(
            "a proposal needs at least one operation (--set, --unset, --add, --remove)"
        )
    apply_ops(SettingsDoc.load(target), ops)  # fail now, not at review time
    qdir = qdir or proposal_dirs()[0]
    qdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = datetime.now(UTC)
    pid = f"{now.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
    data = {
        "version": PROPOSAL_VERSION,
        "id": pid,
        "target": str(target),
        "ops": [o.to_json() for o in ops],
        "reason": reason.strip(),
        "created": now.isoformat(timespec="seconds"),
    }
    path = qdir / f"{pid}.json"
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def _load(path: Path) -> Proposal:
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("version") != PROPOSAL_VERSION:
        raise ValueError("unsupported proposal version")
    ops = []
    for o in d["ops"]:
        if o["kind"] not in OP_KINDS:
            raise ValueError(f"unknown operation {o['kind']!r}")
        ops.append(Op(o["kind"], _check_key(o["key"]), o.get("value")))
    return Proposal(str(d["id"]), Path(d["target"]), ops, str(d["reason"]), str(d["created"]))


def list_proposals(dirs: list[Path] | None = None) -> list[tuple[Path, Proposal]]:
    out = []
    for d in dirs or proposal_dirs():
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.json")):
            try:
                if path.stat().st_uid != os.getuid():
                    continue  # never act on a file someone else could have written
                out.append((path, _load(path)))
            except OSError, ValueError, KeyError, TypeError, GuardError:
                continue
    return out


# ---- security notes ------------------------------------------------------------------------------


def _flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Leaves by dotted path; array items keyed by their JSON so additions and removals show."""
    out: dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                out.update(_flatten(v, key))
            elif isinstance(v, list):
                for item in v:
                    out[f"{key}[]{json.dumps(item, sort_keys=True)}"] = item
                if not v:
                    out[key] = v
            else:
                out[key] = v
    return out


def _hook_commands(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return []
    cmds = []
    for h in item.get("hooks", []):
        if isinstance(h, dict) and h.get("command"):
            cmds.append(" ".join([str(h["command"]), *map(str, h.get("args", []))]))
    return cmds


def _note(change: str, key: str, value: Any) -> str:
    """One plain-English line for one changed leaf."""
    base, _, _ = key.partition("[]")
    top = base.split(".")[0]
    verb = {"added": "adds", "removed": "removes", "changed": "sets"}[change]
    v = json.dumps(value, ensure_ascii=False)
    if top == "hooks":
        event = base.split(".")[1] if "." in base else "?"
        matcher = value.get("matcher") if isinstance(value, dict) else None
        when = f"{event} ({matcher})" if matcher else event
        cmds = ", ".join(f"`{c}`" for c in _hook_commands(value)) or v
        if change == "removed":
            return f"removes a hook on {when} that ran {cmds}"
        return f"{verb} a hook that runs {cmds} on {when}, outside the sandbox"
    if base in ("apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "otelHeadersHelper"):
        return f"{verb} {base} to run `{value}` outside the sandbox"
    if base.startswith("statusLine"):
        return f"{verb} the status line ({base} = {v}); its command runs outside the sandbox"
    if top == "env":
        name = base.split(".", 1)[1] if "." in base else base
        return f"{verb} environment variable {name} = {v} for every session (it changes how tools behave)"
    if base == "permissions.defaultMode":
        return f"{verb} the default permission mode to {v}"
    if base.startswith("permissions.") and "[]" in key:
        kind = base.split(".")[1]
        if kind == "allow":
            return (
                f"{verb} rule: allows `{value}` without asking"
                if change == "added"
                else f"removes the allow rule `{value}`"
            )
        return (
            f"{verb} {kind} rule `{value}`"
            if change == "added"
            else f"removes the {kind} rule `{value}`"
        )
    if top == "permissions":
        return f"{verb} permission setting {base} = {v}"
    if top == "sandbox":
        if base == "sandbox.filesystem.allowWrite" and change == "added":
            return f"lets Bash write to {value} (sandbox allowWrite)"
        return f"{verb} sandbox setting {base} = {v}"
    if top == "mcpServers":
        name = base.split(".")[1] if "." in base else "?"
        return f"{verb} MCP server {name} ({v}); MCP tools are not sandboxed"
    if top in ("enabledPlugins", "extraKnownMarketplaces"):
        return (
            f"{verb} plugin setting {base} = {v}; a plugin can add hooks, commands and MCP servers"
        )
    return f"changes {base} to {v}" if change != "removed" else f"removes {base}"


def security_notes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    a, b = _flatten(old), _flatten(new)
    notes = []
    for key in b:
        if key not in a:
            notes.append(_note("added", key, b[key]))
        elif a[key] != b[key]:
            notes.append(_note("changed", key, b[key]))
    for key in a:
        if key not in b:
            notes.append(_note("removed", key, a[key]))
    return list(dict.fromkeys(notes))


# ---- review and accept ---------------------------------------------------------------------------


def _touches(op: Op, owned_path: str) -> bool:
    """Does the operation change the key ``owned_path`` (or a container above it)?"""
    return op.key == owned_path or owned_path.startswith(op.key + ".")


def _refusals(
    prop: Proposal, applied: Applied, before: dict[str, Any], after: dict[str, Any]
) -> list[str]:
    rec = applied.files.get(str(prop.target))
    if rec is None:
        return []
    out = []
    for o in rec.entries:
        e = o.entry
        if e.kind == "scalar":
            had, was = dig(before, e.path)
            has, now = dig(after, e.path)
            if (had, was) != (has, now) and any(_touches(op, e.path) for op in prop.ops):
                out.append(f"{e.path} is managed by guard")
        else:
            was_in = isinstance(dig(before, e.path)[1], list) and e.value in dig(before, e.path)[1]
            now_in = isinstance(dig(after, e.path)[1], list) and e.value in dig(after, e.path)[1]
            if was_in and not now_in:
                out.append(f"{e.path} item {json.dumps(e.value)} is managed by guard")
    if out:
        out.append(
            "change these with `guard allow`, `guard revoke`, `guard protect` or `guard edit`"
        )
    return out


def review(prop: Proposal, applied: Applied, home: Path) -> Review:
    target = check_target(str(prop.target), home)
    doc = SettingsDoc.load(target)
    before = copy.deepcopy(doc.data)
    apply_ops(doc, prop.ops)
    new_text = doc.render()
    diff = "".join(
        difflib.unified_diff(
            (doc.original_text or "").splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(target) if doc.exists else "/dev/null",
            tofile=str(target),
        )
    )
    return Review(
        prop,
        doc.original_text,
        new_text,
        diff,
        security_notes(before, doc.data),
        _refusals(prop, applied, before, doc.data),
    )


def accept(r: Review, proposal_file: Path, guard_home: Path) -> Path | None:
    """Write the reviewed change. Returns the backup. Refuses if the file changed since review."""
    if r.refusals:
        raise GuardError("this proposal changes entries guard manages: " + "; ".join(r.refusals))
    target = r.proposal.target
    try:
        current = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    if current != r.old_text:
        raise GuardError(f"{target} changed since it was reviewed; review again")
    json.loads(r.new_text)
    backup = backup_file(target, guard_home / BACKUP_DIR)
    atomic_write(target, r.new_text)
    proposal_file.unlink(missing_ok=True)
    guard_home.mkdir(parents=True, exist_ok=True)
    with (guard_home / "proposals.log").open("a", encoding="utf-8") as fh:
        ops = "; ".join(o.describe() for o in r.proposal.ops)
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        fh.write(f"{stamp}\taccepted\t{target}\t{ops}\t{r.proposal.reason}\n")
    return backup
