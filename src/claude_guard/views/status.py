"""``guard status``: what is in force, per scope, in terms of outcomes (design section 11)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..applied import load_applied
from ..apply import plan
from ..compile import ALLOW, ALLOW_WRITE, ASK, DENY, DENY_WRITE, HOOKS, project_settings
from ..context import Built, Ctx
from ..effective import dig, read_source
from ..model import Access, View
from ..proposals import list_proposals

MEANING = {
    Access.FULL: "Edit/Write and Bash, no prompt",
    Access.BASH: "Bash only; the Edit/Write tools ask",
    Access.EDIT: "Edit/Write, no prompt; Bash read-only (no delete or rename)",
    Access.MODIFY: "edit existing files only; Bash read-only",
    Access.ASK: "Edit/Write ask every time; Bash read-only",
    Access.DENY: "no writes",
}
TRACKED = (ALLOW, ASK, DENY, ALLOW_WRITE, DENY_WRITE, HOOKS)


def _scope(name: str, settings: Path, own: View, merged: View) -> dict[str, Any]:
    return {
        "scope": name,
        "settings": str(settings),
        "closed": merged.closed.value if merged.closed else None,
        "grants": [
            {
                "path": str(g.path),
                "access": g.access.value,
                "means": MEANING[g.access],
                "protect": list(g.protect),
                "from": g.origin,
            }
            for g in own.grants
        ],
        "protected": [
            {"path": str(p.path), "ceiling": p.ceiling.value, "from": p.origin}
            for p in own.protected
        ],
        "locked": list(own.locked),
    }


def build_status(ctx: Ctx, built: Built, verbose: bool = False) -> dict[str, Any]:
    from ..compile import merge

    applied = load_applied(ctx.guard_home)
    p = plan(built.compiled, applied)
    ev = built.resolved.everywhere
    scopes = [_scope("everywhere", built.platform.user_settings, ev, ev)]
    for proj, pv in built.resolved.projects.items():
        scopes.append(_scope(str(proj), project_settings(proj), pv, merge(ev, pv)))
    eff = ctx.effective(ctx.project)
    found, sandbox, _ = eff.scalar("sandbox.enabled")
    status: dict[str, Any] = {
        "policy": str(ctx.policy_path),
        "project": str(ctx.project) if ctx.project else None,
        "baseline": "baseline" in built.policy.everywhere.presets,
        "sandbox_enabled": bool(found and sandbox is True),
        "in_sync": p.noop,
        "drift": p.notes,
        "pending_changes": [str(c.path) for c in p.changes],
        "warnings": built.compiled.warnings,
        "scopes": scopes,
        "proposals": [
            {"id": prop.id, "target": str(prop.target), "reason": prop.reason}
            for _, prop in list_proposals(ctx.proposal_dirs())
        ],
    }
    if verbose:
        files: dict[str, Any] = {}
        for f in sorted({*map(str, built.compiled.files), *applied.files}):
            owned = applied.files.get(f)
            owned_values = {
                json.dumps([o.entry.path, o.entry.value], sort_keys=True)
                for o in (owned.entries if owned else [])
            }
            src = read_source("", Path(f))
            unmanaged = []
            for key in TRACKED:
                _, arr = dig(src.data or {}, key)
                for v in arr if isinstance(arr, list) else []:
                    if json.dumps([key, v], sort_keys=True) not in owned_values:
                        unmanaged.append({"path": key, "value": v})
            files[f] = {
                "managed": [o.entry.to_json() for o in owned.entries] if owned else [],
                "unmanaged": unmanaged,
            }
        status["files"] = files
    return status


def render(s: dict[str, Any]) -> str:
    out = [f"Policy: {s['policy']}"]
    out.append(f"Project here: {s['project'] or '(none: home folder or above)'}")
    if not s["baseline"]:
        out.append("warning: the baseline preset is not in [everywhere]")
    if not s["sandbox_enabled"]:
        out.append("warning: the sandbox is not enabled in the effective settings")
    if s["in_sync"]:
        out.append("Settings match the policy.")
    else:
        out.append(
            "Settings do NOT match the policy: run `guard apply` (or `guard plan` to see why)"
        )
        out.extend(f"  - {n}" for n in s["drift"])
    for w in s["warnings"]:
        out.append(f"warning: {w}")
    for sc in s["scopes"]:
        title = "[everywhere]" if sc["scope"] == "everywhere" else f'[projects."{sc["scope"]}"]'
        out.append(f"\n{title}  -> {sc['settings']}")
        if sc["closed"]:
            out.append(f"  elsewhere: the Edit/Write tools {sc['closed']}; Bash is read-only")
        if sc["grants"]:
            out.append("  grants:")
            for g in sc["grants"]:
                prot = f"  (protect: {', '.join(g['protect'])})" if g["protect"] else ""
                out.append(f"    {g['access']:<7} {g['path']}{prot}")
                out.append(f"            {g['means']}")
        if sc["protected"]:
            out.append("  protected (ceiling):")
            for p in sc["protected"]:
                out.append(f"    {p['ceiling']:<7} {p['path']}")
        if sc["locked"]:
            out.append("  locked:")
            out.extend(f"    {x}" for x in sc["locked"])
    if s["proposals"]:
        out.append(f"\nPending proposals ({len(s['proposals'])}): run `guard review` in a terminal")
        for p in s["proposals"]:
            out.append(f"  {p['id']}  {p['target']}: {p['reason']}")
    for f, d in s.get("files", {}).items():
        out.append(f"\n{f}")
        out.append("  managed by guard:")
        for e in d["managed"]:
            out.append(f"    {e['path']}: {json.dumps(e['value'], ensure_ascii=False)}")
        if d["unmanaged"]:
            out.append("  not managed by guard:")
            for e in d["unmanaged"]:
                out.append(f"    {e['path']}: {json.dumps(e['value'], ensure_ascii=False)}")
    return "\n".join(out) + "\n"
