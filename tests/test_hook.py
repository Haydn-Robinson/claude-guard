"""The PreToolUse hook for the Edit/Write tools (`guard hook edit`) and its settings entry.

Phase 0b facts this relies on: the hook runs in auto mode, `ask` prompts, `deny` blocks, and with
``onFailure: "block"`` a crash blocks (without it a crash lets the write through)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_guard.engine import Guard
from claude_guard.errors import GuardError
from claude_guard.hook import Decision, decide, glob_match
from claude_guard.state import load_state


def allow(g: Guard, folder, profiles, settings: Path, **kw):
    plan = g.plan_allow(folder, profiles, settings, **kw)
    g.commit(plan)
    return plan


def read(p: Path) -> dict:
    return json.loads(p.read_text())


def payload(path: str | None, cwd: Path, tool: str = "Write", key: str = "file_path") -> dict:
    ti = {} if path is None else {key: path}
    return {"tool_name": tool, "tool_input": ti, "cwd": str(cwd), "permission_mode": "auto"}


def run(g: Guard, home: Path, pl: dict, unlisted: str = "ask") -> Decision | None:
    return decide(pl, load_state(g.state_file), home, unlisted)


@pytest.fixture
def granted(guard: Guard, target: Path, settings: Path) -> Path:
    allow(guard, target, ["edit-ask"], settings, protect=[".private/**", "archive/**"])
    return target


# ---- decisions ---------------------------------------------------------------------------------


def test_inside_project_is_left_alone(guard, home, proj, granted):
    assert run(guard, home, payload(str(proj / "src" / "a.py"), proj)) is None


def test_inside_granted_folder_is_left_alone(guard, home, proj, granted):
    assert run(guard, home, payload(str(granted / "note.md"), proj)) is None


def test_outside_everything_asks_by_default(guard, home, proj, granted, tmp_path):
    d = run(guard, home, payload(str(tmp_path / "elsewhere" / "x.txt"), proj))
    assert d == Decision("ask", d.reason) and "guard allow" in d.reason


def test_strict_denies_outside(guard, home, proj, granted, tmp_path):
    d = run(guard, home, payload(str(tmp_path / "elsewhere" / "x.txt"), proj), "deny")
    assert d is not None and d.action == "deny"


def test_sibling_with_same_prefix_is_outside(guard, home, proj, granted):
    d = run(guard, home, payload(str(granted) + "-evil/x.txt", proj))
    assert d is not None and d.action == "ask"


def test_dotdot_traversal_is_outside(guard, home, proj, granted):
    d = run(guard, home, payload(str(granted / ".." / "other" / "x.txt"), proj))
    assert d is not None and d.action == "ask"


def test_symlink_escaping_the_folder_is_outside(guard, home, proj, granted, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (granted / "link").symlink_to(target)
    d = run(guard, home, payload(str(granted / "link" / "x.txt"), proj))
    assert d is not None and d.action == "ask"


def test_protected_path_inside_granted_folder_is_denied(guard, home, proj, granted):
    for rel in (".private/app.json", "archive/book.md"):
        d = run(guard, home, payload(str(granted / rel), proj))
        assert d is not None and d.action == "deny" and "protect" in d.reason.lower()


def test_protected_wins_even_in_ask_mode_outside_project(guard, home, proj, granted):
    d = run(guard, home, payload(str(granted / ".private" / "x"), proj), "ask")
    assert d is not None and d.action == "deny"


def test_floor_paths_are_denied(guard, home, proj, settings):
    allow(guard, None, ["floor"], settings)
    d = run(guard, home, payload(str(home / ".bashrc"), proj))
    assert d is not None and d.action == "deny"
    d = run(guard, home, payload(str(home / ".ssh" / "authorized_keys"), proj))
    assert d is not None and d.action == "deny"


def test_managed_settings_and_state_are_denied(guard, home, proj, granted, settings):
    for p in (settings, guard.state_dir / "state.json"):
        d = run(guard, home, payload(str(p), proj))
        assert d is not None and d.action == "deny"


def test_grant_in_another_projects_settings_is_not_active(guard, home, proj, tmp_path, target):
    other = tmp_path / "other"
    (other / ".claude").mkdir(parents=True)
    allow(guard, target, ["edit-ask"], other / ".claude" / "settings.local.json")
    # a session in `proj` is not governed by other's grants: the target is just "outside"
    d = run(guard, home, payload(str(target / "n.md"), proj))
    assert d is not None and d.action == "ask"
    # but a session in `other` is
    assert run(guard, home, payload(str(target / "n.md"), other)) is None


def test_user_scope_grant_is_active_everywhere(guard, home, proj, tmp_path, target):
    (home / ".claude").mkdir()
    allow(guard, target, ["edit-ask"], home / ".claude" / "settings.json")
    assert run(guard, home, payload(str(target / "n.md"), tmp_path)) is None


def test_relative_path_resolves_against_cwd(guard, home, proj, granted):
    assert run(guard, home, payload("src/a.py", proj)) is None
    d = run(guard, home, payload("../outside.txt", proj))
    assert d is not None and d.action == "ask"


def test_notebook_path_is_checked(guard, home, proj, granted, tmp_path):
    pl = payload(str(tmp_path / "e" / "n.ipynb"), proj, "NotebookEdit", "notebook_path")
    d = run(guard, home, pl)
    assert d is not None and d.action == "ask"


def test_missing_path_is_denied(guard, home, proj, granted):
    d = run(guard, home, payload(None, proj))
    assert d is not None and d.action == "deny"


def test_other_tools_are_not_our_business(guard, home, proj, granted):
    pl = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(proj)}
    assert run(guard, home, pl) is None


def test_empty_state_still_asks_outside_project(guard, home, proj, tmp_path):
    d = run(guard, home, payload(str(tmp_path / "x" / "y"), proj))
    assert d is not None and d.action == "ask"


# ---- glob matching -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pat", "path", "ok"),
    [
        ("/a/b/**", "/a/b/c", True),
        ("/a/b/**", "/a/b/c/d/e", True),
        ("/a/b/**", "/a/bc/d", False),
        ("/a/*", "/a/x", True),
        ("/a/*", "/a/x/y", False),
        ("/a/**/z", "/a/z", True),
        ("/a/**/z", "/a/b/c/z", True),
        ("/a/?", "/a/x", True),
        ("/a/?", "/a/xy", False),
        ("/a/.bashrc", "/a/.bashrc", True),
        ("/a/.bashrc", "/a/xbashrc", False),
        ("/a/(x)+[y]/**", "/a/(x)+[y]/f", True),
        ("/a b/c d/**", "/a b/c d/e", True),
        ("/mnt/d/V/.private/**", "/mnt/d/v/.PRIVATE/x", True),  # Windows drives: case-insensitive
        ("/home/V/.private/**", "/home/v/.private/x", False),  # Linux paths: case-sensitive
    ],
)
def test_glob_match(pat: str, path: str, ok: bool):
    assert glob_match(pat, path) is ok


# ---- the settings entry ------------------------------------------------------------------------


def hooks(settings: Path) -> list:
    return read(settings)["hooks"]["PreToolUse"]


def test_confine_edits_registers_fail_closed_hook(guard: Guard, settings: Path):
    allow(guard, None, ["confine-edits"], settings)
    (entry,) = hooks(settings)
    assert entry["matcher"] == "Write|Edit|MultiEdit|NotebookEdit"
    (h,) = entry["hooks"]
    assert h["type"] == "command" and h["command"] == "/opt/guard/bin/guard"
    assert h["onFailure"] == "block"
    assert h["args"][:2] == ["hook", "edit"]
    assert h["args"][h["args"].index("--state-dir") + 1] == str(guard.state_dir)
    assert h["args"][h["args"].index("--unlisted") + 1] == "ask"


def test_strict_profile_registers_deny(guard: Guard, settings: Path):
    allow(guard, None, ["confine-edits-strict"], settings)
    (h,) = hooks(settings)[0]["hooks"]
    assert h["args"][h["args"].index("--unlisted") + 1] == "deny"


def test_hook_grant_is_idempotent_and_revocable(guard: Guard, settings: Path):
    allow(guard, None, ["confine-edits"], settings)
    assert guard.plan_allow(None, ["confine-edits"], settings).noop
    guard.commit(guard.plan_revoke(None, settings))
    assert not settings.exists()


def test_revoke_leaves_other_hooks_and_restores_bytes(guard: Guard, settings: Path):
    mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/x/audit"}]}
    original = json.dumps({"hooks": {"PreToolUse": [mine]}}, indent=2) + "\n"
    settings.write_text(original)
    allow(guard, None, ["confine-edits"], settings)
    assert hooks(settings)[0] == mine and len(hooks(settings)) == 2
    guard.commit(guard.plan_revoke(None, settings))
    assert settings.read_text() == original


def test_hook_needs_a_command(guard: Guard, settings: Path, policy):
    g = Guard(guard.state_dir, guard.profiles, policy)  # no hook_command
    with pytest.raises(GuardError, match="hook"):
        g.plan_allow(None, ["confine-edits"], settings)


def test_hook_entries_do_not_break_key_ordering(guard: Guard, settings: Path):
    # same logical entry with a different key order must be recognised as already present
    allow(guard, None, ["confine-edits"], settings)
    data = read(settings)
    entry = data["hooks"]["PreToolUse"][0]
    data["hooks"]["PreToolUse"][0] = dict(reversed(list(entry.items())))
    settings.write_text(json.dumps(data, indent=2) + "\n")
    assert guard.plan_allow(None, ["confine-edits"], settings).noop
