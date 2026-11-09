"""``guard hook edit``: the PreToolUse hook for the Edit/Write tools (design section 8).

The sandbox confines Bash only (VB-S10); in auto mode the file tools write anywhere unless a rule
stops them (VB-E1), and rules cannot say "everywhere except these folders" (deny beats allow). This
hook can: the tool's path is structured data, so there is nothing to parse.

It reads the hook data written to applied.json by the last apply (so it enforces what was applied,
not an unapplied policy edit), and decides, first match wins:

1. no file path -> deny;
2. resolve the path (``..``, symlinks; Windows drive paths case-insensitively, VB-U12);
3. locked, inside a ceiling-``deny`` protected path, or a grant's ``protect`` glob -> deny (a
   backstop: deny rules are applied before hooks run, VB-E5, VB-H5);
4. protected: a grant naming it (or a folder inside it) and containing the path -> step 5 with that
   grant; else inside the project -> ask; else deny;
5. inside a grant (the most specific wins): ``modify`` refuses Write; ``deny`` refuses; ``bash``
   counts as closed; otherwise no opinion (the rules apply, so ``ask`` still prompts);
6. inside the project -> no opinion;
7. closed -> ask, or deny with ``closed = "deny"``.

It never allows, so it cannot widen anything. Registered with ``onFailure: "block"`` (VB-H3), so a
crash blocks the write. Decisions go to ``hook.log``, since the prompt does not show the reason
(VB-E7). Imports are kept light: this runs on every file-tool call.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .rules import find_project, glob_matches

FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_DRIVE = re.compile(r"^/mnt/[a-zA-Z](/|$)")


@dataclass(frozen=True)
class Decision:
    action: str  # "ask" | "deny"
    reason: str


def _key(p: str) -> str:
    """Comparison key: drvfs paths are case-insensitive (VB-U12)."""
    return p.casefold() if _DRIVE.match(p) else p


def within(path: str, folder: str) -> bool:
    p, f = _key(path.rstrip("/") or "/"), _key(folder.rstrip("/") or "/")
    return p == f or p.startswith(f if f == "/" else f + "/")


def _matches(pattern: str, path: str) -> bool:
    return glob_matches(_key(pattern), Path(_key(path)))


def _resolve(raw: str, cwd: str | None) -> str:
    p = raw if os.path.isabs(raw) else os.path.join(cwd or os.getcwd(), raw)
    return os.path.realpath(p)


def _scope(data: dict[str, Any], project: str | None) -> dict[str, Any]:
    scopes = data.get("scopes", {})
    if project is not None and project in scopes:
        return scopes[project]
    return scopes.get("*", {"closed": "ask", "grants": [], "protected": [], "locked": []})


def _innermost(items: list[dict[str, Any]], path: str) -> dict[str, Any] | None:
    best = None
    for item in items:  # later entries (the project's) win ties
        if within(path, item["path"]) and (best is None or len(item["path"]) >= len(best["path"])):
            best = item
    return best


def _by_grant(g: dict[str, Any], tool: str, path: str) -> Decision | None | str:
    access = g["access"]
    if access == "deny":
        return Decision("deny", f"{path} is in {g['path']}, granted with access deny")
    if access == "modify" and tool == "Write":
        return Decision(
            "deny",
            f"{g['path']} allows editing existing files only (access modify): use Edit, not Write",
        )
    if access == "bash":
        return "closed"  # the file tools get nothing from a bash grant
    return None


def decide(
    payload: dict[str, Any], data: dict[str, Any], project_dir: str | None
) -> Decision | None:
    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input") or {}
    raw = tin.get("file_path") or tin.get("notebook_path")
    if not raw or not isinstance(raw, str):
        return Decision("deny", f"{tool}: no file path in the tool call")
    path = _resolve(raw, payload.get("cwd") or project_dir)

    home = data.get("home") or str(Path.home())
    start = project_dir or payload.get("cwd")
    root = find_project(Path(start), Path(home)) if start else None
    project = str(root) if root is not None else None
    scope = _scope(data, project)
    grants: list[dict[str, Any]] = scope.get("grants", [])
    protected: list[dict[str, Any]] = scope.get("protected", [])

    for pat in scope.get("locked", []):
        if _matches(pat, path):
            return Decision("deny", f"{path} is locked ({pat}): use `guard propose`")
    for p in protected:
        if p["ceiling"] == "deny" and within(path, p["path"]):
            return Decision("deny", f"{path} is protected ({p['path']}), ceiling deny")
    for g in grants:
        if within(path, g["path"]):
            for glob in g.get("protect", []):  # same meaning as the Edit(//grant/glob) deny rule
                if _matches(g["path"].rstrip("/") + "/" + glob, path):
                    return Decision("deny", f"{path} is protected by {glob!r} in {g['path']}")

    in_project = project is not None and within(path, project)
    prot = _innermost([p for p in protected if p["ceiling"] != "deny"], path)
    candidates = grants
    if prot is not None:
        candidates = [g for g in grants if within(g["path"], prot["path"])]
    grant = _innermost(candidates, path)

    if prot is not None and grant is None:
        if in_project:
            return Decision("ask", f"{path} is protected ({prot['path']}), inside the project")
        return Decision("deny", f"{path} is protected ({prot['path']}) and no grant opens it")

    closed = False
    if grant is not None:
        verdict = _by_grant(grant, tool, path)
        if isinstance(verdict, Decision):
            return verdict
        closed = verdict == "closed"
        if not closed:
            return None
    if in_project and not closed:
        return None
    mode = scope.get("closed") or "ask"
    where = f"{path} is outside the project and every grant"
    if closed:
        where = f"{path} is in {grant['path']} (access bash: Bash only)"  # type: ignore[index]
    return Decision(mode, f"{where}; `guard explain {path}` says more")


def _output(d: Decision) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": d.action,
                "permissionDecisionReason": d.reason,
            }
        }
    )


def _log(guard_home: Path, tool: str, path: str, d: Decision | None) -> None:
    line = "\t".join(
        [
            datetime.now(UTC).isoformat(timespec="seconds"),
            d.action if d else "pass",
            tool,
            path,
            d.reason if d else "",
        ]
    )
    try:
        guard_home.mkdir(parents=True, exist_ok=True)
        with (guard_home / "hook.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # logging must never decide


def run_hook(stdin_text: str, guard_home: Path) -> str:
    """Process interface: stdin JSON in, hook JSON (or nothing) out. Raises on bad input."""
    payload = json.loads(stdin_text)
    if not isinstance(payload, dict):
        raise TypeError("hook input is not a JSON object")
    try:
        applied = json.loads((guard_home / "applied.json").read_text(encoding="utf-8"))
        data = applied.get("hook") or {}
    except FileNotFoundError:
        data = {}
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if not data:
        d: Decision | None = Decision(
            "ask", "claude-guard has no applied policy: run `guard apply`"
        )
    else:
        d = decide(payload, data, project_dir)
    tin = payload.get("tool_input") or {}
    _log(
        guard_home,
        str(payload.get("tool_name")),
        str(tin.get("file_path") or tin.get("notebook_path")),
        d,
    )
    return _output(d) if d else ""
