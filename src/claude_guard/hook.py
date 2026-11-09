"""``guard hook edit``: a PreToolUse hook for the Edit/Write tools.

The sandbox confines Bash only (VB-S10). In auto mode the Edit/Write tools write anywhere with no
prompt unless a deny rule matches (VB-E1). Rules cannot express "everywhere except these folders",
because deny beats allow. This hook can: the path is structured data, so no command parsing is needed.

For every file-tool call it decides:

1. the path matches an ``Edit(...)`` deny rule that guard manages (protected paths, floor, guard's own
   settings and state)  -> **deny**;
2. the path is inside the current project or inside a folder granted with guard -> no opinion
   (normal permission rules and prompts apply, so ``edit-ask`` still asks);
3. anything else -> **ask** (default) or **deny** (``--unlisted deny``).

It never allows. It only tightens, so it cannot widen what Claude Code would permit. It is
registered with ``onFailure: "block"``: a crash blocks the write (without that a crashing hook lets
the write through: VB-H3). Deny rules are evaluated before hooks (VB-E5), so for protected paths this
hook is a backstop. The ask prompt does not show the reason (VB-E7), so it is also written to the log. Every decision is appended to ``<state dir>/hook.log``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import GuardError
from .state import load_state

FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
HOOK_MATCHER = "|".join(FILE_TOOLS)
_PERM_DENY = "permissions.deny"


@dataclass(frozen=True)
class Decision:
    action: str  # "ask" | "deny"
    reason: str


def glob_match(pattern: str, path: str) -> bool:
    """Match ``path`` against an Edit-rule glob: ``*`` stays in one segment, ``**`` crosses them.

    Paths on Windows drives (``/mnt/<letter>/``) are matched case-insensitively, because the
    filesystem is (an assumption, VB-U12)."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    flags = re.IGNORECASE if re.match(r"/mnt/[A-Za-z](/|$)", path) else 0
    return re.fullmatch("".join(out), path, flags) is not None


def _real(p: str | Path) -> str:
    return os.path.realpath(p)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _active(settings_file: str, home: Path, cwd: str) -> bool:
    """Is a grant recorded for this settings file in force for a session started in ``cwd``?"""
    project = Path(settings_file).parent.parent
    if _real(project) == _real(home):
        return True  # ~/.claude/settings*.json applies to every session
    return _within(cwd, _real(project))


def _rule_glob(rule: str, home: Path) -> str | None:
    if not (rule.startswith("Edit(") and rule.endswith(")")):
        return None
    inner = rule[5:-1]
    if inner.startswith("//"):
        return inner[1:]
    if inner.startswith("~/"):
        return str(home) + inner[1:]
    return None  # relative forms are never emitted by guard


def decide(
    payload: dict[str, Any], state: dict[str, Any], home: Path, unlisted: str = "ask"
) -> Decision | None:
    if payload.get("tool_name") not in FILE_TOOLS:
        return None
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return Decision("deny", "claude-guard: the tool call has no file path to check")
    cwd = _real(payload.get("cwd") or os.getcwd())
    lexical = os.path.normpath(os.path.join(cwd, os.path.expanduser(raw)))
    path = _real(lexical)

    active_files = {f for f in state["files"] if _active(f, home, cwd)}
    for f in active_files:
        for erec in state["files"][f]["entries"].values():
            if erec["kind"] != "array" or erec["path"] != _PERM_DENY:
                continue
            pat = _rule_glob(erec["value"], home) if isinstance(erec["value"], str) else None
            if pat and (glob_match(pat, path) or glob_match(pat, lexical)):
                return Decision(
                    "deny", f"claude-guard: {path} is protected by a rule guard manages ({pat})"
                )

    if _within(path, cwd):
        return None
    for g in state["grants"].values():
        if g["folder"] and g["settings_file"] in active_files and _within(path, _real(g["folder"])):
            return None

    reason = (
        f"claude-guard: {path} is outside the project and every folder granted with guard. "
        "To allow it, run: guard allow <folder> <profile>"
    )
    return Decision(unlisted if unlisted in ("ask", "deny") else "ask", reason)


def run_hook(
    stdin_text: str, state_dir: Path, unlisted: str, home: Path, now: datetime | None = None
) -> str:
    """Decide for one hook invocation. Returns the JSON to print ("" = no opinion)."""
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError as e:
        raise GuardError(f"hook input is not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise GuardError("hook input is not a JSON object")
    state = load_state(state_dir / "state.json")  # corrupt state raises: the hook then blocks
    decision = decide(payload, state, home, unlisted)
    _log(state_dir, payload, decision, now)
    if decision is None:
        return ""
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision.action,
                "permissionDecisionReason": decision.reason,
            }
        }
    )


def _log(
    state_dir: Path, payload: dict[str, Any], d: Decision | None, now: datetime | None
) -> None:
    ti = payload.get("tool_input") or {}
    entry = {
        "t": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "tool": payload.get("tool_name"),
        "path": ti.get("file_path") or ti.get("notebook_path"),
        "permission_mode": payload.get("permission_mode"),
        "decision": d.action if d else "none",
    }
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        with (state_dir / "hook.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # logging must never change the decision
