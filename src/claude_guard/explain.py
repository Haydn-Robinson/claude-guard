"""``guard explain``: what would happen to a path or a Bash command, and which layer decides.

Read-only. It evaluates the *effective* settings (managed, local, project, user: hand-written rules
count, and each rule is labelled with whether guard manages it) and, for the Edit/Write hook, guard's
own decision logic. It does not run anything and cannot see the auto-mode classifier, which may
block more than it reports.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hook import _active, _real, _rule_glob, _within, decide, glob_match
from .settings_view import Effective, dig, load_effective
from .state import empty_state, load_state

LAYERS = {
    "rule": "Claude Code rule",
    "hook": "guard hook",
    "sandbox": "sandbox, kernel-enforced",
}
_DENY_WRITE = "sandbox.filesystem.denyWrite"
_ALLOW_WRITE = "sandbox.filesystem.allowWrite"


@dataclass(frozen=True)
class Finding:
    layer: str  # "rule" | "hook" | "sandbox"
    effect: str  # "deny" | "ask" | "allow" | "none"
    text: str
    origin: str = ""  # settings scope the rule came from
    managed: bool = False  # added by guard


@dataclass(frozen=True)
class Verdict:
    code: str
    text: str


@dataclass
class Section:
    id: str  # "edit" | "sandbox" | "bash"
    title: str
    findings: list[Finding]
    verdict: Verdict


@dataclass
class Explanation:
    kind: str  # "path" | "command"
    subject: str
    sections: list[Section]
    notes: list[str] = field(default_factory=list)


# ---- helpers ---------------------------------------------------------------------------------


def _managed_set(state: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (e["path"], e["value"])
        for frec in state["files"].values()
        for e in frec["entries"].values()
        if e["kind"] == "array" and isinstance(e["value"], str)
    }


def _rules(eff: Effective, dotted: str) -> list[tuple[str, str]]:
    out = []
    for src in eff.sources:
        if src.data is not None:
            found, val = dig(src.data, dotted)
            if found and isinstance(val, list):
                out.extend((v, src.label) for v in val if isinstance(v, str))
    return out


def _safe_state(state_file: Path) -> dict[str, Any]:
    try:
        return load_state(state_file)
    except Exception:  # noqa: BLE001 - explain is best effort; doctor reports a broken state
        return empty_state()


def bash_match(spec: str, command: str) -> bool:
    """Match a ``Bash(spec)`` pattern against a command. ``cmd *`` also matches bare ``cmd``
    (VB-R1); a rule without ``*`` matches only the exact command (VB-R2)."""
    spec = spec.strip()
    if spec.endswith(":*"):
        spec = spec[:-2] + " *"
    trailing = spec.endswith(" *")
    body = spec[:-2] if trailing else spec
    rx = ".*".join(re.escape(part) for part in body.split("*"))
    if trailing:
        rx += "(?: .*)?"
    return re.fullmatch(rx, command.strip(), re.DOTALL) is not None


def _edit_rule_findings(
    eff: Effective,
    kind: str,
    path: str,
    home: Path,
    managed: set[tuple[str, str]],
    notes: list[str],
) -> list[tuple[str, str, str, bool]]:
    """Edit rules of one kind (deny/ask/allow) that match ``path``: (rule, origin, glob, managed)."""
    hits = []
    for rule, origin in _rules(eff, f"permissions.{kind}"):
        if not rule.startswith("Edit("):
            continue
        pat = _rule_glob(rule, home)
        if pat is None:
            note = f"cannot evaluate {rule} ({origin} settings): path form not supported by explain"
            if note not in notes:
                notes.append(note)
            continue
        if glob_match(pat, path):  # wildcard-free rule semantics: VB-U11
            hits.append((rule, origin, pat, (f"permissions.{kind}", rule) in managed))
    return hits


def _sandbox_entries(
    eff: Effective, dotted: str, home: Path, notes: list[str]
) -> list[tuple[str, str, str]]:
    out = []
    for raw, origin in _rules(eff, dotted):
        e = raw.removesuffix("/**")
        if e.startswith("//"):
            e = e[1:]
        elif e.startswith("~/"):
            e = str(home) + e[1:]
        if not e.startswith("/"):
            note = f"cannot evaluate {dotted} entry {raw!r} ({origin} settings): relative path"
            if note not in notes:
                notes.append(note)
            continue
        out.append((raw, origin, _real(e)))
    return out


# ---- paths -----------------------------------------------------------------------------------


def explain_path(
    raw: str, cwd: Path, home: Path, project: Path, state_file: Path, managed_dir: Path
) -> Explanation:
    eff = load_effective(home, project, managed_dir)
    state = _safe_state(state_file)
    managed = _managed_set(state)
    notes: list[str] = [f"cannot read {s.path}: {s.error}" for s in eff.errors]
    session_cwd = _real(project)
    path = _real(os.path.normpath(os.path.join(cwd, os.path.expanduser(raw))))
    in_project = _within(path, session_cwd)

    # -- Edit / Write tools
    ef: list[Finding] = []
    for kind in ("deny", "ask", "allow"):
        for rule, origin, _pat, m in _edit_rule_findings(eff, kind, path, home, managed, notes):
            ef.append(Finding("rule", kind, rule, origin, m))
    modes = sorted(
        {
            _hook_mode(e["value"])
            for f, frec in state["files"].items()
            if _active(f, home, session_cwd)
            for e in frec["entries"].values()
            if e["kind"] == "array"
            and e["path"] == "hooks.PreToolUse"
            and isinstance(e["value"], dict)
        }
    )
    if modes:
        mode = "deny" if "deny" in modes else "ask"
        d = decide(
            {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": session_cwd},
            state,
            home,
            mode,
        )
        ef.append(
            Finding("hook", d.action, d.reason, "", True)
            if d
            else Finding(
                "hook",
                "none",
                "no opinion: inside the project or a folder granted with guard",
                "",
                True,
            )
        )
    else:
        notes.append(
            "No guard hook registered for this project: in auto mode the Edit/Write tools write "
            "outside the project without a prompt unless a rule matches. "
            "Fix: guard allow --global confine-edits"
        )
    effects = {f.effect for f in ef}
    first = lambda eff_: next(f for f in ef if f.effect == eff_)
    if "deny" in effects:
        edit_v = Verdict(
            "deny", f"DENIED: the Edit/Write tools cannot write here ({_why(first('deny'))})"
        )
    elif "ask" in effects:
        edit_v = Verdict("ask", f"ASKS before writing, even in auto mode ({_why(first('ask'))})")
    elif "allow" in effects:
        edit_v = Verdict("allow", f"allowed without a prompt ({_why(first('allow'))})")
    elif in_project:
        edit_v = Verdict(
            "none", "no rule applies; inside the project, so the permission mode decides"
        )
    else:
        edit_v = Verdict(
            "none",
            "no rule applies and this is outside the project: default and acceptEdits mode prompt, "
            "auto mode writes with no prompt",
        )

    # -- Bash writes (sandbox)
    sf: list[Finding] = []
    for raw_e, origin, real in _sandbox_entries(eff, _DENY_WRITE, home, notes):
        if _within(path, real):
            sf.append(
                Finding(
                    "sandbox", "deny", f"denyWrite {raw_e}", origin, (_DENY_WRITE, raw_e) in managed
                )
            )
    for f in ef:
        if f.layer == "rule" and f.effect == "deny":
            sf.append(
                Finding(
                    "rule",
                    "deny",
                    f"{f.text} (Edit deny rules also become denyWrite)",
                    f.origin,
                    f.managed,
                )
            )
    for raw_e, origin, real in _sandbox_entries(eff, _ALLOW_WRITE, home, notes):
        if _within(path, real):
            sf.append(
                Finding(
                    "sandbox",
                    "allow",
                    f"allowWrite {raw_e}",
                    origin,
                    (_ALLOW_WRITE, raw_e) in managed,
                )
            )
    for f in ef:
        if f.layer == "rule" and f.effect == "allow":
            sf.append(
                Finding(
                    "rule",
                    "allow",
                    f"{f.text} (Edit allow rules also grant Bash write access)",
                    f.origin,
                    f.managed,
                )
            )
    if in_project:
        sf.append(
            Finding(
                "sandbox",
                "allow",
                "project directory (writable by default, per Claude Code docs; not tested here: VB-S4)",
            )
        )
    found, val, _ = eff.scalar("sandbox.enabled")
    sandbox_on = found and val is True
    seffects = {f.effect for f in sf}
    if not sandbox_on:
        sb_v = Verdict(
            "unconfined",
            "the sandbox is off, so Bash is not confined by file paths at all "
            "(fix: guard allow --global lock); only Bash rules apply",
        )
    elif "deny" in seffects:
        d0 = next(f for f in sf if f.effect == "deny")
        sb_v = Verdict("blocked", f"BLOCKED: Bash cannot write here ({d0.text})")
    elif "allow" in seffects:
        sb_v = Verdict("writable", "Bash can write here")
    else:
        sb_v = Verdict(
            "blocked", "BLOCKED: read-only, not inside any folder the sandbox allows writing"
        )

    notes.append(_LIMITS)
    return Explanation(
        "path",
        path,
        [
            Section("edit", "Edit/Write tools", ef, edit_v),
            Section("sandbox", "Bash writes (sandbox)", sf, sb_v),
        ],
        notes,
    )


def _hook_mode(entry: dict[str, Any]) -> str:
    for h in entry.get("hooks", []):
        args = h.get("args", [])
        if "--unlisted" in args:
            return str(args[args.index("--unlisted") + 1])
    return "ask"


def _why(f: Finding) -> str:
    return f"{LAYERS[f.layer]}: {f.text}"


_LIMITS = (
    "explain reads the settings files and guard's state. It cannot see the auto-mode classifier "
    "(which may block more), session-only permissions, or rules from other tools."
)


# ---- commands --------------------------------------------------------------------------------


def explain_command(
    command: str, home: Path, project: Path, managed_dir: Path, state_file: Path | None = None
) -> Explanation:
    eff = load_effective(home, project, managed_dir)
    state = _safe_state(state_file) if state_file else empty_state()
    notes: list[str] = [f"cannot read {s.path}: {s.error}" for s in eff.errors]
    managed = _managed_set(state)
    findings: list[Finding] = []
    for kind in ("deny", "ask", "allow"):
        for rule, origin in _rules(eff, f"permissions.{kind}"):
            if rule.startswith("Bash(") and rule.endswith(")") and bash_match(rule[5:-1], command):
                findings.append(
                    Finding("rule", kind, rule, origin, (f"permissions.{kind}", rule) in managed)
                )
    found, val, _ = eff.scalar("sandbox.enabled")
    sandbox_on = found and val is True
    effects = [f.effect for f in findings]
    if "deny" in effects:
        v = Verdict("deny", f"DENIED: {next(f for f in findings if f.effect == 'deny').text}")
    elif "ask" in effects:
        v = Verdict(
            "ask", f"ASKS every time: {next(f for f in findings if f.effect == 'ask').text}"
        )
    elif "allow" in effects:
        v = Verdict(
            "allow", f"auto-approved by {next(f for f in findings if f.effect == 'allow').text}"
        )
    else:
        _, auto_val, auto_where = eff.scalar("sandbox.autoAllowBashIfSandboxed")
        auto_on = auto_where is None or auto_val is not False
        extra = (
            " With the sandbox on, sandboxed commands are auto-approved by default."
            if sandbox_on and auto_on
            else ""
        )
        v = Verdict("none", "no Bash rule matches: the permission mode decides." + extra)
    notes.append(
        "Bash rules match the command text only. A rephrased command (variables, bash -c, scripts, "
        "a different spelling) can slip past a rule; the sandbox is what bounds where it can write "
        "(see: guard explain <path>). Compound commands (&&, ;, |) are checked part by part by "
        "Claude Code; explain checks the text as one string."
    )
    notes.append(_LIMITS)
    return Explanation("command", command, [Section("bash", "Bash command", findings, v)], notes)


# ---- rendering -------------------------------------------------------------------------------


def render_explanation(e: Explanation) -> str:
    out = [("Path: " if e.kind == "path" else "Command: ") + e.subject, ""]
    for s in e.sections:
        out.append(s.title)
        for f in s.findings:
            who = ", ".join(
                x
                for x in (f.origin + " settings" if f.origin else "", "guard" if f.managed else "")
                if x
            )
            out.append(
                f"  {f.effect:<5} [{LAYERS[f.layer]}] {f.text}" + (f"  ({who})" if who else "")
            )
        if not s.findings:
            out.append("  (nothing matches)")
        out.append(f"  => {s.verdict.text}")
        out.append("")
    if e.notes:
        out.append("Notes")
        out.extend(f"  - {n}" for n in e.notes)
    return "\n".join(out).rstrip() + "\n"


def to_json(e: Explanation) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(e)
