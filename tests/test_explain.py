"""`guard explain`: what would happen to a path (Edit/Write tools, Bash writes) or a Bash command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_guard.engine import Guard
from claude_guard.explain import Explanation, bash_match, explain_command, explain_path


def allow(g: Guard, folder, profiles, settings: Path, **kw):
    g.commit(g.plan_allow(folder, profiles, settings, **kw))


@pytest.fixture
def managed(tmp_path: Path) -> Path:
    return tmp_path / "managed"


def path_exp(guard, home, proj, managed, target: str | Path, cwd: Path | None = None):
    return explain_path(str(target), cwd or proj, home, proj, guard.state_file, managed)


def cmd_exp(guard, home, proj, managed, command: str):
    return explain_command(command, home, proj, managed, guard.state_file)


def sec(e: Explanation, sid: str):
    return next(s for s in e.sections if s.id == sid)


def text(e: Explanation) -> str:
    from claude_guard.explain import render_explanation

    return render_explanation(e)


# ---- paths: Edit/Write tools ---------------------------------------------------------------------


def test_protected_path_is_denied_for_edit_tools_and_blocked_for_bash(
    guard, home, proj, managed, target, settings
):
    allow(guard, target, ["edit-ask", "lock"], settings, protect=[".private/**"])
    e = path_exp(guard, home, proj, managed, target / ".private" / "app.json")
    assert sec(e, "edit").verdict.code == "deny"
    assert sec(e, "sandbox").verdict.code == "blocked"
    assert any(f.effect == "deny" and f.managed for f in sec(e, "edit").findings)


def test_edit_ask_folder_asks_and_bash_is_read_only(guard, home, proj, managed, target, settings):
    allow(guard, target, ["edit-ask", "lock"], settings)
    e = path_exp(guard, home, proj, managed, target / "note.md")
    assert sec(e, "edit").verdict.code == "ask"
    assert sec(e, "sandbox").verdict.code == "blocked"
    assert "denyWrite" in sec(e, "sandbox").verdict.text


def test_edit_grant_allows_and_bash_can_write(guard, home, proj, managed, target, settings):
    allow(guard, target, ["edit", "lock"], settings)
    e = path_exp(guard, home, proj, managed, target / "note.md")
    assert sec(e, "edit").verdict.code == "allow"
    assert sec(e, "sandbox").verdict.code == "writable"


def test_sandbox_off_is_called_out(guard, home, proj, managed, target, settings):
    allow(guard, target, ["edit"], settings)  # no lock: sandbox not enabled
    e = path_exp(guard, home, proj, managed, target / "note.md")
    assert sec(e, "sandbox").verdict.code == "unconfined"
    assert "sandbox is off" in sec(e, "sandbox").verdict.text.lower()


def test_outside_path_with_hook_asks(guard, home, proj, managed, tmp_path, settings):
    allow(guard, None, ["confine-edits", "lock"], settings)
    e = path_exp(guard, home, proj, managed, tmp_path / "elsewhere" / "x.txt")
    assert sec(e, "edit").verdict.code == "ask"
    assert any(f.layer == "hook" for f in sec(e, "edit").findings)
    assert sec(e, "sandbox").verdict.code == "blocked"  # not in any allowed folder


def test_outside_path_without_hook_explains_the_gap(guard, home, proj, managed, tmp_path):
    e = path_exp(guard, home, proj, managed, tmp_path / "elsewhere" / "x.txt")
    v = sec(e, "edit").verdict
    assert v.code == "none" and "auto" in v.text.lower()
    assert any("no guard hook" in n.lower() for n in e.notes)


def test_inside_project_is_normal_behaviour(guard, home, proj, managed):
    e = path_exp(guard, home, proj, managed, proj / "src" / "a.py")
    assert sec(e, "edit").verdict.code == "none" and "project" in sec(e, "edit").verdict.text
    assert sec(e, "sandbox").verdict.code in ("writable", "unconfined")


def test_deny_beats_allow(guard, home, proj, managed, target, settings):
    allow(guard, target, ["edit", "lock"], settings, protect=["secret/**"])
    e = path_exp(guard, home, proj, managed, target / "secret" / "k")
    assert sec(e, "edit").verdict.code == "deny"
    assert sec(e, "sandbox").verdict.code == "blocked"


def test_hand_written_rules_are_included_and_labelled(
    guard, home, proj, managed, settings, tmp_path
):
    settings.write_text(json.dumps({"permissions": {"deny": [f"Edit(/{tmp_path}/hand/**)"]}}))
    e = path_exp(guard, home, proj, managed, tmp_path / "hand" / "f")
    assert sec(e, "edit").verdict.code == "deny"
    (f,) = [f for f in sec(e, "edit").findings if f.effect == "deny"]
    assert not f.managed and f.origin == "local"


def test_uninterpretable_rule_forms_are_reported_not_guessed(
    guard, home, proj, managed, settings, tmp_path
):
    settings.write_text(json.dumps({"permissions": {"deny": ["Edit(relative/**)", "Edit(*.env)"]}}))
    e = path_exp(guard, home, proj, managed, tmp_path / "x")
    assert any("cannot evaluate" in n.lower() and "relative/**" in n for n in e.notes)


def test_symlinks_and_relative_paths_are_resolved(
    guard, home, proj, managed, target, settings, tmp_path
):
    allow(guard, target, ["edit-ask", "lock"], settings)
    link = tmp_path / "link"
    link.symlink_to(target)
    e = path_exp(guard, home, proj, managed, link / "n.md")
    assert str(target) in e.subject and sec(e, "edit").verdict.code == "ask"
    rel = path_exp(guard, home, proj, managed, "src/a.py", cwd=proj)
    assert e.subject != rel.subject and rel.subject.startswith(str(proj))


def test_explain_changes_nothing(guard, home, proj, managed, target, settings):
    allow(guard, target, ["edit-ask", "lock"], settings)
    before = (settings.read_text(), guard.state_file.read_text())
    path_exp(guard, home, proj, managed, target / "a")
    cmd_exp(guard, home, proj, managed, "rm a")
    assert (settings.read_text(), guard.state_file.read_text()) == before


def test_render_names_layers(guard, home, proj, managed, target, settings):
    allow(guard, target, ["edit-ask", "lock"], settings, protect=["p/**"])
    out = text(path_exp(guard, home, proj, managed, target / "p" / "f"))
    assert "Edit/Write tools" in out and "Bash" in out
    assert "sandbox" in out and "Claude Code rule" in out and "guard" in out
    assert "classifier" in out  # honest limit


# ---- commands --------------------------------------------------------------------------------


def verdict(guard, home, proj, managed, command: str) -> str:
    return sec(cmd_exp(guard, home, proj, managed, command), "bash").verdict.code


def test_command_rules(guard, home, proj, managed, target, settings):
    allow(guard, target, ["git-safe", "git-destructive", "confirm-destructive", "lock"], settings)
    allow(guard, None, ["floor"], settings)
    v = lambda c: verdict(guard, home, proj, managed, c)
    assert v("git status") == "allow"  # the "*" form matches the bare command (Phase 0b T4)
    assert v("git status -s") == "allow"
    assert v("git reset --hard") == "ask"
    assert v("git reset --hard HEAD~1") == "ask"
    assert v("rm -rf build") == "ask"
    assert v("sudo ls") == "deny"
    assert v("ls -la") == "none"


def test_deny_beats_allow_for_commands(guard, home, proj, managed, settings):
    settings.write_text(
        json.dumps({"permissions": {"allow": ["Bash(rm *)"], "deny": ["Bash(rm -rf *)"]}})
    )
    assert verdict(guard, home, proj, managed, "rm -rf x") == "deny"
    assert verdict(guard, home, proj, managed, "rm x") == "allow"


def test_command_explanation_states_limits(guard, home, proj, managed, settings):
    out = text(cmd_exp(guard, home, proj, managed, "echo hi"))
    assert "text" in out.lower() and "rephras" in out.lower()


@pytest.mark.parametrize(
    ("spec", "command", "ok"),
    [
        ("whoami", "whoami", True),
        ("whoami", "whoami --version", False),
        ("uname *", "uname", True),
        ("uname *", "uname -s", True),
        ("uname *", "unamex", False),
        ("git stash list *", "git stash list", True),
        ("git stash list *", "git stash list --oneline", True),
        ("git stash list *", "git stash", False),
        ("npm run test:*", "npm run test --watch", True),
        ("npm run test:*", "npm run testing", False),
        ("rm * /tmp/x", "rm -rf /tmp/x", True),
        ("a.b *", "axb", False),
    ],
)
def test_bash_match(spec: str, command: str, ok: bool):
    assert bash_match(spec, command) is ok


def test_command_findings_are_labelled_managed(guard, home, proj, managed, settings):
    allow(guard, None, ["floor"], settings)
    settings.write_text(json.dumps({**json.loads(settings.read_text())}, indent=2) + "\n")
    e = cmd_exp(guard, home, proj, managed, "sudo ls")
    assert [f.managed for f in sec(e, "bash").findings] == [True]
