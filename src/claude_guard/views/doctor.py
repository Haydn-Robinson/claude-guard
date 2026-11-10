"""``guard doctor``: health checks, report only (design section 11)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import claude_guard

from ..applied import load_applied
from ..apply import plan
from ..compile import HOOKS
from ..context import Built, Ctx
from ..errors import GuardError
from ..hook import within
from ..proposals import list_proposals


@dataclass
class Check:
    level: str  # ok | warn | fail
    name: str
    detail: str = ""


def _claude_writable(built: Built, ctx: Ctx) -> list[str]:
    """Folders Claude can write in some session: projects, grants that write, $TMPDIR."""
    out = [str(p) for p in built.resolved.projects]
    if ctx.project:
        out.append(str(ctx.project))
    for view in (built.resolved.everywhere, *built.resolved.projects.values()):
        out += [
            str(g.path) for g in view.grants if g.access.tools == "allow" or g.access.bash_writes
        ]
        out += list(view.raw.allow_write)
    out.append(str(ctx.claude_tmp))
    return out


def run_doctor(ctx: Ctx, built: Built | None, error: str | None = None) -> list[Check]:
    checks: list[Check] = []
    add = lambda level, name, detail="": checks.append(Check(level, name, detail))

    for tool in ("bwrap", "socat"):
        if ctx.which(tool):
            add("ok", f"{tool} installed")
        else:
            add(
                "fail",
                f"{tool} installed",
                "the sandbox needs it: sudo apt install bubblewrap socat",
            )

    if built is None:
        add("fail", "policy", error or "cannot load the policy")
        return checks
    add("ok", "policy", f"{ctx.policy_path} is valid and compiles")

    eff = ctx.effective(ctx.project)
    bad = eff.errors
    for src in bad:
        add("fail", f"settings JSON valid: {src.path}", src.error or "")
    if not bad:
        add("ok", "settings JSON valid", ", ".join(s.label for s in eff.present()) or "no files")

    for key, want, level, why in (
        ("sandbox.enabled", True, "fail", "Bash is not confined"),
        (
            "sandbox.allowUnsandboxedCommands",
            False,
            "warn",
            "commands may retry outside the sandbox",
        ),
        (
            "permissions.disableBypassPermissionsMode",
            "disable",
            "warn",
            "bypass-permissions mode can be turned on",
        ),
    ):
        found, val, where = eff.scalar(key)
        if found and val == want:
            add("ok", f"{key} = {json.dumps(want)}", f"from {where} settings")
        else:
            add(level, f"{key}", f"{why}. The baseline preset sets it")
    if any(c.name.startswith("permissions.disableBypass") and c.level == "ok" for c in checks):
        add("ok", "bypass requests", "fall back to your default mode silently, with no warning")

    wants_hook = any(
        v.closed for v in (built.resolved.everywhere, *built.resolved.projects.values())
    )
    registered = [
        h
        for item, _ in eff.array(HOOKS)
        if isinstance(item, dict)
        for h in item.get("hooks", [])
        if isinstance(h, dict) and "edit" in h.get("args", []) and "hook" in h.get("args", [])
    ]
    if wants_hook and not registered:
        add(
            "fail",
            "Edit/Write hook",
            "the policy wants it but it is not registered: run `guard apply`",
        )
    for h in registered:
        exe = h.get("command", "")
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            add(
                "fail",
                "Edit/Write hook",
                f"{exe} is missing: every Edit/Write is blocked (fail-closed)",
            )
            continue
        if h.get("onFailure") != "block":
            add(
                "fail",
                "Edit/Write hook",
                "registered without onFailure: block, so a crash lets writes through",
            )
            continue
        targets = {
            exe,
            os.path.realpath(exe),
            os.path.dirname(os.path.realpath(claude_guard.__file__)),
        }
        exposed = [t for t in targets for w in _claude_writable(built, ctx) if within(t, w)]
        if exposed:
            add(
                "fail",
                "Edit/Write hook",
                f"{exposed[0]} is where Claude can write: install guard with `uv tool install`",
            )
        else:
            add("ok", "Edit/Write hook", f"registered, fail-closed: {exe}")

    scripts = built.platform.settings_scripts
    add(
        "ok",
        "scripts that settings run",
        ", ".join(map(str, scripts)) + " (protected, ceiling ask)" if scripts else "none",
    )

    try:
        p = plan(built.compiled, load_applied(ctx.guard_home))
    except GuardError as e:
        add("fail", "settings match the policy", str(e))
    else:
        if p.noop:
            add("ok", "settings match the policy")
        else:
            detail = "; ".join(p.notes) or "the policy has unapplied changes"
            add("warn", "settings match the policy", f"{detail}. Run `guard apply`")
    for w in built.compiled.warnings:
        add("warn", "policy", w)
    n = len(list_proposals(ctx.proposal_dirs()))
    if n:
        add("warn", "pending proposals", f"{n}: run `guard review` in a terminal")
    return checks


def render(checks: list[Check]) -> str:
    tag = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]"}
    out = [f"{tag[c.level]} {c.name}" + (f": {c.detail}" if c.detail else "") for c in checks]
    fails = sum(c.level == "fail" for c in checks)
    warns = sum(c.level == "warn" for c in checks)
    out.append(f"\n{fails} failure(s), {warns} warning(s)")
    return "\n".join(out) + "\n"
