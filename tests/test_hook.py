"""The v2 Edit/Write hook: a decision table over applied hook data (design section 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_guard.applied import Applied, save_applied
from claude_guard.compile import compile_policy
from claude_guard.hook import decide, run_hook
from claude_guard.model import Platform
from claude_guard.policy import parse_policy, resolve


@pytest.fixture
def world(tmp_path: Path):
    home = tmp_path / "home"
    proj = home / "repos" / "p"
    (proj / ".git").mkdir(parents=True)
    (proj / "sub").mkdir()
    out = tmp_path / "out"
    out.mkdir()
    plat = Platform(home=home, guard_home=home / ".config" / "claude-guard", hook_command=("/g",))
    return home, proj, out, plat


def data_for(text: str, plat: Platform) -> dict:
    return compile_policy(resolve(parse_policy("version = 1\n" + text), plat), plat).hook


def call(data, path, tool="Write", project=None, cwd=None):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    payload = {"tool_name": tool, "tool_input": {key: str(path)} if path is not None else {}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    d = decide(payload, data, str(project) if project else None)
    return d.action if d else None


BASE = '[everywhere]\nclosed = "ask"\n'


def test_no_path_denied(world):
    *_, plat = world
    assert call(data_for(BASE, plat), None) == "deny"


def test_project_is_open_and_outside_is_closed(world):
    _home, proj, out, plat = world
    data = data_for(BASE, plat)
    assert call(data, proj / "a.txt", project=proj) is None
    assert call(data, out / "a.txt", project=proj) == "ask"
    strict = data_for('[everywhere]\nclosed = "deny"\n', plat)
    assert call(strict, out / "a.txt", project=proj) == "deny"


def test_started_in_a_subfolder_uses_the_git_root(world):
    """CLAUDE_PROJECT_DIR is the launch folder (VB-H7); the repo root's settings apply (VB-P5)."""
    _home, proj, _out, plat = world
    assert call(data_for(BASE, plat), proj / "x.txt", project=proj / "sub") is None


def test_home_is_never_a_project(world):
    home, _proj, _out, plat = world
    assert call(data_for(BASE, plat), home / "notes.txt", project=home) == "ask"


@pytest.mark.parametrize(
    ("access", "tool", "expected"),
    [
        ("full", "Write", None),
        ("edit", "Write", None),
        ("ask", "Write", None),  # the ask rule prompts; the hook adds nothing
        ("bash", "Write", "ask"),  # file tools stay closed
        ("modify", "Edit", None),
        ("modify", "NotebookEdit", None),
        ("modify", "Write", "deny"),  # VB-H8
        ("deny", "Write", "deny"),
    ],
)
def test_access_levels(world, access, tool, expected):
    _home, proj, out, plat = world
    data = data_for(BASE + f'write = [{{ path = "{out}", access = "{access}" }}]\n', plat)
    assert call(data, out / "f.txt", tool=tool, project=proj) == expected


def test_most_specific_grant_wins(world):
    _home, proj, out, plat = world
    (out / "cfg").mkdir()
    data = data_for(
        BASE + f'write = [{{ path = "{out}", access = "full" }}, '
        f'{{ path = "{out}/cfg", access = "modify" }}]\n',
        plat,
    )
    assert call(data, out / "f.txt", project=proj) is None
    assert call(data, out / "cfg" / "f.txt", project=proj) == "deny"
    assert call(data, out / "cfg" / "f.txt", tool="Edit", project=proj) is None


def test_protect_glob_denied(world):
    _home, proj, out, plat = world
    data = data_for(
        BASE
        + f'write = [{{ path = "{out}", access = "full", protect = ["*.lock", "keep/**"] }}]\n',
        plat,
    )
    assert call(data, out / "a.lock", project=proj) == "deny"
    assert call(data, out / "keep" / "x" / "y", project=proj) == "deny"
    assert call(data, out / "sub" / "a.lock", project=proj) is None  # * stays in one segment


def test_locked_paths_denied_even_in_the_project(world):
    _home, proj, _out, plat = world
    data = data_for(BASE + 'locked = ["**/.claude/settings.json", "{guard_home}/"]\n', plat)
    assert call(data, proj / ".claude" / "settings.json", project=proj) == "deny"
    assert call(data, plat.guard_home / "policy.toml", project=proj) == "deny"
    assert call(data, proj / ".claude" / "skills" / "s.md", project=proj) is None  # VB-E9


def test_protected_paths(world):
    home, proj, out, plat = world
    (home / ".ssh").mkdir()
    data = data_for(
        BASE + f'protected = [{{ path = "~/.ssh", ceiling = "ask" }}, '
        f'{{ path = "{proj}/scripts", ceiling = "ask" }}, {{ path = "{out}/win", ceiling = "deny" }}]\n',
        plat,
    )
    assert call(data, home / ".ssh" / "config", project=proj) == "deny"  # outside the project
    assert call(data, proj / "scripts" / "run.sh", project=proj) == "ask"  # inside it
    assert call(data, out / "win" / "x", project=proj) == "deny"


def test_parent_grant_does_not_open_a_protected_path(world):
    _home, proj, out, plat = world
    data = data_for(
        BASE + f'write = [{{ path = "{out}", access = "full" }}]\n'
        f'protected = [{{ path = "{out}/scripts", ceiling = "ask" }}]\n',
        plat,
    )
    assert call(data, out / "a.txt", project=proj) is None
    assert call(data, out / "scripts" / "a.sh", project=proj) == "deny"


def test_grant_naming_a_protected_path_opens_it(world):
    _home, proj, out, plat = world
    data = data_for(
        BASE + f'write = [{{ path = "{out}/scripts", access = "ask" }}]\n'
        f'protected = [{{ path = "{out}/scripts", ceiling = "ask" }}]\n',
        plat,
    )
    assert call(data, out / "scripts" / "a.sh", project=proj) is None  # the ask rule prompts


def test_project_grants_apply_only_in_that_project(world, tmp_path: Path):
    home, proj, out, plat = world
    other = home / "repos" / "q"
    (other / ".git").mkdir(parents=True)
    data = data_for(
        BASE + f'[projects."{proj}"]\nwrite = [{{ path = "{out}", access = "full" }}]\n', plat
    )
    assert call(data, out / "a", project=proj) is None
    assert call(data, out / "a", project=proj / "sub") is None
    assert call(data, out / "a", project=other) == "ask"


def test_project_closed_mode_overrides(world):
    _home, proj, out, plat = world
    data = data_for(BASE + f'[projects."{proj}"]\nclosed = "deny"\n', plat)
    assert call(data, out / "a", project=proj) == "deny"


def test_symlink_out_of_the_project_counts_as_outside(world):
    _home, proj, out, plat = world
    (proj / "link").symlink_to(out)
    assert call(data_for(BASE, plat), proj / "link" / "f.txt", project=proj) == "ask"


def test_relative_path_resolved_against_cwd(world):
    _home, proj, _out, plat = world
    assert call(data_for(BASE, plat), "../../../out/x", project=proj, cwd=proj / "sub") == "ask"
    assert call(data_for(BASE, plat), "x.txt", project=proj, cwd=proj / "sub") is None


def test_windows_drive_paths_match_case_insensitively(world):
    """VB-U12: drvfs is case-insensitive; matching errs towards the grant's protection."""
    home, proj, _out, _plat = world
    data = {
        "home": str(home),
        "scopes": {
            "*": {
                "closed": "ask",
                "grants": [{"path": "/mnt/z/Data/Proj", "access": "modify", "protect": []}],
                "protected": [],
                "locked": [],
            }
        },
    }
    assert call(data, "/mnt/z/data/PROJ/f.txt", project=proj) == "deny"


# ---- run_hook: the process interface ---------------------------------------------------------


def test_run_hook_output_and_log(world, monkeypatch):
    _home, proj, out, plat = world
    save_applied(plat.guard_home, Applied(hook=data_for(BASE, plat)))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(out / "x")}, "cwd": str(proj)}
    text = run_hook(json.dumps(payload), plat.guard_home)
    out_json = json.loads(text)["hookSpecificOutput"]  # VB-H2
    assert out_json["hookEventName"] == "PreToolUse"
    assert out_json["permissionDecision"] == "ask"
    assert str(out / "x") in out_json["permissionDecisionReason"]
    payload["tool_input"]["file_path"] = str(proj / "x")
    assert run_hook(json.dumps(payload), plat.guard_home) == ""
    log = (plat.guard_home / "hook.log").read_text().splitlines()
    assert len(log) == 2 and "ask" in log[0] and "pass" in log[1]


def test_run_hook_without_applied_policy_asks(world, monkeypatch):
    _home, proj, _out, plat = world
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(proj / "x")}}
    decision = json.loads(run_hook(json.dumps(payload), plat.guard_home))
    assert decision["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_run_hook_bad_input_raises(world):
    """A crash blocks the write: the hook is registered with onFailure: block (VB-H3)."""
    *_, plat = world
    with pytest.raises(ValueError):
        run_hook("not json", plat.guard_home)
    with pytest.raises(TypeError):
        run_hook("[1]", plat.guard_home)


def test_a_sibling_with_a_common_prefix_is_not_inside(world):
    _home, proj, out, plat = world
    sibling = out.parent / (out.name + "2")
    data = data_for(BASE + f'write = [{{ path = "{out}", access = "full" }}]\n', plat)
    assert call(data, sibling / "f.txt", project=proj) == "ask"
