"""``guard explain``: what would happen to one path or one Bash command, and which layer decides.

Starts from the effective settings (every source, not only what guard wrote) and the applied hook
data. It cannot see the auto-mode classifier (VB-C1) or permissions granted for one session.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..applied import load_applied
from ..context import Ctx
from ..effective import Effective
from ..hook import decide, within
from ..rules import bash_match, glob_matches, has_glob

_RULE = re.compile(r"^(\w+)\((.*)\)$", re.DOTALL)
# Inside the working directory the sandbox keeps these read-only to Bash by itself (VB-S15).
BUILTIN_READONLY = (
    ".claude",
    ".mcp.json",
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zprofile",
    ".zshrc",
    ".gitconfig",
    ".gitmodules",
    ".ripgreprc",
    ".idea",
    ".vscode",
)


@dataclass
class Finding:
    effect: str  # allow | ask | deny | write | readonly | info
    layer: str  # "rule", "hook", "sandbox", "built-in"
    detail: str
    source: str = ""


@dataclass
class Section:
    title: str
    findings: list[Finding] = field(default_factory=list)
    verdict: str = ""


@dataclass
class Explanation:
    subject: str
    sections: list[Section]
    notes: list[str] = field(default_factory=list)


def _rule_pattern(spec: str, home: Path) -> str | None:
    """A rule's path pattern as an absolute glob, or None if it is relative (cannot evaluate)."""
    if spec.startswith("//"):
        return spec[1:]
    if spec == "~" or spec.startswith("~/"):
        return str(home) + spec[1:]
    return None


def _concrete(pattern: str) -> bool:
    """Concrete patterns reach the sandbox; `**/` globs do not (VB-S7, VB-S13, VB-S14)."""
    head = pattern.removesuffix("/**")
    return not has_glob(head)


def _matching_rules(
    eff: Effective, tool: str, path: Path, home: Path, notes: list[str]
) -> list[tuple[str, str, str, str]]:
    """(effect, rule, pattern, source) for every ``Tool(...)`` rule that covers ``path``."""
    out = []
    for effect in ("deny", "ask", "allow"):
        for rule, src in eff.strings(f"permissions.{effect}"):
            m = _RULE.match(rule.strip())
            if not m or m.group(1) != tool:
                continue
            pat = _rule_pattern(m.group(2), home)
            if pat is None:
                notes.append(f"cannot evaluate {rule} ({src.label} settings): relative path form")
                continue
            if glob_matches(pat, path):
                out.append((effect, rule, pat, src.label))
    return out


def _hook_registered(eff: Effective) -> bool:
    for item, _ in eff.array("hooks.PreToolUse"):
        for h in item.get("hooks", []) if isinstance(item, dict) else []:
            if isinstance(h, dict) and "hook" in h.get("args", []) and "edit" in h.get("args", []):
                return True
    return False


def explain_path(ctx: Ctx, raw: str) -> Explanation:
    home = ctx.home
    path = Path(os.path.realpath(ctx.cwd / Path(raw).expanduser()))
    project = ctx.project
    eff = ctx.effective(project)
    notes: list[str] = []
    rules = _matching_rules(eff, "Edit", path, home, notes)

    # ---- Edit/Write tools
    tools = Section("Edit/Write tools")
    for effect, rule, _, src in rules:
        tools.findings.append(Finding(effect, "rule", rule, f"{src} settings"))
    hook = None
    if _hook_registered(eff):
        data = load_applied(ctx.guard_home).hook
        hook = decide(
            {"tool_name": "Write", "tool_input": {"file_path": str(path)}}, data, str(ctx.cwd)
        )
        if hook:
            tools.findings.append(Finding(hook.action, "hook", hook.reason))
        else:
            tools.findings.append(Finding("info", "hook", "no opinion: the rules decide"))
    else:
        notes.append("the guard hook is not registered: run `guard init` or `guard apply`")
    effects = {f.effect for f in tools.findings if f.layer in ("rule", "hook")}
    in_project = project is not None and within(str(path), str(project))
    if "deny" in effects:
        tools.verdict = "DENIED"
    elif "ask" in effects:
        tools.verdict = "ASKS every time (in every permission mode)"
    elif "allow" in effects:
        tools.verdict = "allowed without a prompt"
    elif in_project:
        tools.verdict = "allowed: inside the project (default mode may still prompt)"
    else:
        tools.verdict = "no rule: auto mode writes without a prompt; default mode prompts"

    # ---- Bash
    bash = Section("Bash writes (sandbox, kernel-enforced)")
    found, enabled, _ = eff.scalar("sandbox.enabled")
    if not (found and enabled is True):
        bash.verdict = "NOT CONFINED: the sandbox is off, so Bash can write anywhere you can"
    else:
        denied = [
            Finding("readonly", "sandbox", f"denyWrite {p}", f"{s.label} settings")
            for p, s in eff.strings("sandbox.filesystem.denyWrite")
            if within(str(path), os.path.expanduser(p))
        ] + [
            Finding(
                "readonly", "rule", f"{rule} (a deny rule reaches the sandbox)", f"{src} settings"
            )
            for effect, rule, pat, src in rules
            if effect == "deny" and _concrete(pat)
        ]
        builtin = []
        if in_project:
            rel = path.relative_to(project).parts  # type: ignore[arg-type]
            if rel and rel[0] in BUILTIN_READONLY:
                builtin.append(
                    Finding("readonly", "built-in", f"{rel[0]} in the project is read-only to Bash")
                )
        writable = [
            Finding("write", "sandbox", f"allowWrite {p}", f"{s.label} settings")
            for p, s in eff.strings("sandbox.filesystem.allowWrite")
            if within(str(path), os.path.expanduser(p))
        ] + [
            Finding(
                "write", "rule", f"{rule} (an allow rule reaches the sandbox)", f"{src} settings"
            )
            for effect, rule, pat, src in rules
            if effect == "allow" and _concrete(pat)
        ]
        tmp = f"/tmp/claude-{os.getuid()}"
        if in_project:
            writable.append(Finding("write", "sandbox", f"inside the project {project}"))
        if within(str(path), tmp):
            writable.append(Finding("write", "sandbox", f"inside Claude's $TMPDIR {tmp}"))
        bash.findings = denied + builtin + writable
        if denied or builtin:
            bash.verdict = (
                "BLOCKED: Bash cannot write, rename or delete here (Read-only file system)"
            )
        elif writable:
            bash.verdict = "writable: Bash can create, change, rename and delete here"
        else:
            bash.verdict = "read-only: outside the project, $TMPDIR and every grant"

    # ---- Read
    read = Section("Reading")
    for effect, rule, pat, src in _matching_rules(eff, "Read", path, home, notes):
        if effect == "deny":
            reach = "also hidden from Bash" if _concrete(pat) else "Bash can still read it"
            read.findings.append(Finding("deny", "rule", f"{rule} ({reach})", f"{src} settings"))
    read.verdict = "DENIED to the Read tool" if read.findings else "readable"
    if any("hidden" in f.detail for f in read.findings):
        notes.append("Bash sees a hidden file as `No such file or directory` (VB-S13)")
    if bash.verdict.startswith("BLOCKED"):
        notes.append("a sandbox refusal shows up as `Read-only file system` (VB-S3)")
    return Explanation(str(path), [tools, bash, read], list(dict.fromkeys(notes)))


def explain_command(ctx: Ctx, command: str) -> Explanation:
    eff = ctx.effective(ctx.project)
    sec = Section("Bash command rules (text matching only, VB-R5)")
    for effect in ("deny", "ask", "allow"):
        for rule, src in eff.strings(f"permissions.{effect}"):
            m = _RULE.match(rule.strip())
            if m and m.group(1) == "Bash" and bash_match(m.group(2), command):
                sec.findings.append(Finding(effect, "rule", rule, f"{src.label} settings"))
    effects = [f.effect for f in sec.findings]
    sec.verdict = (
        "DENIED"
        if "deny" in effects
        else "ASKS"
        if "ask" in effects
        else "allowed without a prompt"
        if "allow" in effects
        else "no rule: the permission mode decides (sandboxed commands run without a prompt)"
    )
    notes = [
        (
            "rules match text: a rephrased command (/bin/rm, sh -c, a script) is not matched;"
            " the sandbox still limits where it can write"
        ),
    ]
    return Explanation(command, [sec], notes)


def render(e: Explanation) -> str:
    out = [e.subject]
    for s in e.sections:
        out.append(f"\n{s.title}")
        for f in s.findings:
            src = f"  ({f.source})" if f.source else ""
            out.append(f"  {f.effect:<8} [{f.layer}] {f.detail}{src}")
        out.append(f"  => {s.verdict}")
    if e.notes:
        out.append("\nNotes")
        out.extend(f"  - {n}" for n in e.notes)
    return "\n".join(out) + "\n"


def to_json(e: Explanation) -> dict[str, Any]:
    return {
        "subject": e.subject,
        "sections": [
            {"title": s.title, "verdict": s.verdict, "findings": [f.__dict__ for f in s.findings]}
            for s in e.sections
        ],
        "notes": e.notes,
    }
