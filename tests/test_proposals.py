"""Propose and review: Claude queues a settings change; you approve it (design section 9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_guard.applied import Applied, FileRecord, Owned
from claude_guard.errors import GuardError
from claude_guard.model import Entry
from claude_guard.proposals import (
    Op,
    accept,
    check_target,
    list_proposals,
    parse_op,
    propose,
    review,
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    return h


@pytest.fixture
def qdir(tmp_path: Path) -> Path:
    return tmp_path / "tmp" / "guard-proposals"


def canonical(d: dict) -> str:
    return json.dumps(d, indent=2) + "\n"


# ---- operations and targets -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "text", "op"),
    [
        ("set", "model=opus", Op("set", "model", "opus")),
        ("set", "sandbox.enabled=true", Op("set", "sandbox.enabled", True)),
        ("set", 'env={"A": "1"}', Op("set", "env", {"A": "1"})),
        (
            "set",
            "statusLine.command=~/bin/s.sh --x=1",
            Op("set", "statusLine.command", "~/bin/s.sh --x=1"),
        ),
        ("unset", "model", Op("unset", "model")),
        ("add", "permissions.allow=Bash(make *)", Op("add", "permissions.allow", "Bash(make *)")),
        ("remove", "permissions.deny=Read(//x)", Op("remove", "permissions.deny", "Read(//x)")),
    ],
)
def test_parse_op(kind, text, op):
    assert parse_op(kind, text) == op


@pytest.mark.parametrize(
    ("kind", "text"), [("set", "noequals"), ("set", "=1"), ("unset", ""), ("add", "a..b=1")]
)
def test_bad_ops_rejected(kind, text):
    with pytest.raises(GuardError):
        parse_op(kind, text)


def test_targets(home: Path, tmp_path: Path):
    proj = tmp_path / "p"
    assert (
        check_target(str(home / ".claude" / "settings.json"), home)
        == home / ".claude" / "settings.json"
    )
    assert check_target("~/.claude.json", home) == home / ".claude.json"
    assert check_target(str(proj / ".claude" / "settings.local.json"), home)
    assert check_target(str(proj / ".mcp.json"), home)
    for bad in [str(home / ".bashrc"), str(proj / "settings.json"), "relative/.mcp.json"]:
        with pytest.raises(GuardError, match="target"):
            check_target(bad, home)


# ---- propose ------------------------------------------------------------------------------------


def test_propose_writes_a_queued_proposal(home: Path, qdir: Path):
    target = home / ".claude" / "settings.json"
    path = propose(target, [Op("set", "model", "opus")], "use opus", qdir)
    assert path.parent == qdir
    data = json.loads(path.read_text())
    assert data["target"] == str(target)
    assert data["reason"] == "use opus"
    assert data["ops"] == [{"kind": "set", "key": "model", "value": "opus"}]
    assert not target.exists()  # nothing changed yet
    [(_p, prop)] = list_proposals([qdir])
    assert prop.reason == "use opus"


def test_propose_needs_a_reason_and_ops(home: Path, qdir: Path):
    target = home / ".claude" / "settings.json"
    with pytest.raises(GuardError, match="reason"):
        propose(target, [Op("set", "model", "x")], "  ", qdir)
    with pytest.raises(GuardError, match="operation"):
        propose(target, [], "why", qdir)


def test_propose_rejects_ops_that_cannot_apply(home: Path, qdir: Path):
    target = home / ".claude" / "settings.json"
    target.write_text(canonical({"model": "opus"}))
    with pytest.raises(GuardError):
        propose(target, [Op("add", "model", "x")], "why", qdir)  # model is not an array


def test_list_skips_files_owned_by_someone_else(home: Path, qdir: Path, monkeypatch):
    propose(home / ".claude" / "settings.json", [Op("set", "model", "o")], "r", qdir)
    import claude_guard.proposals as mod

    monkeypatch.setattr(mod.os, "getuid", lambda: 12345)
    assert list_proposals([qdir]) == []


# ---- review --------------------------------------------------------------------------------------


def test_review_applies_to_the_file_as_it_is_now(home: Path, qdir: Path):
    target = home / ".claude" / "settings.json"
    target.write_text(canonical({"model": "opus"}))
    propose(target, [Op("set", "statusLine.command", "~/bin/s.sh")], "status", qdir)
    target.write_text(canonical({"model": "sonnet"}))  # changed after the proposal
    [(_path, prop)] = list_proposals([qdir])
    r = review(prop, Applied(), home)
    assert json.loads(r.new_text) == {"model": "sonnet", "statusLine": {"command": "~/bin/s.sh"}}
    assert "+" in r.diff and "statusLine" in r.diff
    assert any("status line" in n and "outside the sandbox" in n for n in r.notes)


@pytest.mark.parametrize(
    ("ops", "expect"),
    [
        (
            [
                Op(
                    "add",
                    "hooks.PreToolUse",
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "./x.sh"}]},
                )
            ],
            "adds a hook that runs `./x.sh` on PreToolUse (Bash), outside the sandbox",
        ),
        ([Op("set", "apiKeyHelper", "~/k.sh")], "`~/k.sh`"),
        ([Op("set", "env.NODE_OPTIONS", "--require x")], "environment variable NODE_OPTIONS"),
        ([Op("add", "permissions.allow", "Bash(curl *)")], "allows `Bash(curl *)` without asking"),
        ([Op("set", "permissions.defaultMode", "bypassPermissions")], "default permission mode"),
        ([Op("set", "sandbox.enabled", False)], "sandbox"),
        ([Op("add", "sandbox.filesystem.allowWrite", "/srv")], "sandbox"),
        ([Op("set", "enabledPlugins.x@y", True)], "plugin"),
        ([Op("set", "model", "opus")], "changes model"),
    ],
)
def test_security_notes(home: Path, qdir: Path, ops, expect):
    target = home / ".claude" / "settings.json"
    propose(target, ops, "why", qdir)
    [(_path, prop)] = list_proposals([qdir])
    notes = review(prop, Applied(), home).notes
    assert any(expect in n for n in notes), notes


def test_mcp_server_note(home: Path, qdir: Path, tmp_path: Path):
    target = tmp_path / "p" / ".mcp.json"
    propose(
        target, [Op("set", "mcpServers.db", {"command": "npx", "args": ["db-mcp"]})], "db", qdir
    )
    [(_path, prop)] = list_proposals([qdir])
    notes = review(prop, Applied(), home).notes
    assert any("MCP server db" in n and "not sandboxed" in n for n in notes)


def test_review_refuses_changes_to_guard_owned_entries(home: Path, qdir: Path):
    target = home / ".claude" / "settings.json"
    target.write_text(
        canonical({"permissions": {"deny": ["Edit(//x/**)"]}, "sandbox": {"enabled": True}})
    )
    owned = Applied(
        files={
            str(target): FileRecord(
                False,
                [],
                [
                    Owned(Entry("array", "permissions.deny", "Edit(//x/**)")),
                    Owned(Entry("scalar", "sandbox.enabled", True), prior=(False, None)),
                ],
            )
        }
    )
    for ops in (
        [Op("remove", "permissions.deny", "Edit(//x/**)")],
        [Op("set", "sandbox.enabled", False)],
        [Op("unset", "sandbox")],
        [Op("unset", "permissions.deny")],
    ):
        for f in qdir.glob("*.json"):
            f.unlink()
        propose(target, ops, "why", qdir)
        [(_path, prop)] = list_proposals([qdir])
        r = review(prop, owned, home)
        assert r.refusals and "guard allow" in r.refusals[0] + " ".join(r.refusals)


def test_accept_backs_up_writes_and_removes_the_proposal(home: Path, qdir: Path, tmp_path: Path):
    target = home / ".claude" / "settings.json"
    target.write_text(canonical({"model": "opus"}))
    path = propose(target, [Op("set", "model", "sonnet")], "why", qdir)
    [(p, prop)] = list_proposals([qdir])
    r = review(prop, Applied(), home)
    gh = tmp_path / "guard"
    backup = accept(r, p, gh)
    assert json.loads(target.read_text()) == {"model": "sonnet"}
    assert backup is not None and json.loads(backup.read_text()) == {"model": "opus"}
    assert not path.exists()
    assert "sonnet" in (gh / "proposals.log").read_text()


def test_accept_refuses_when_the_file_changed_since_review(home: Path, qdir: Path, tmp_path: Path):
    target = home / ".claude" / "settings.json"
    target.write_text(canonical({"model": "opus"}))
    propose(target, [Op("set", "model", "sonnet")], "why", qdir)
    [(p, prop)] = list_proposals([qdir])
    r = review(prop, Applied(), home)
    target.write_text(canonical({"model": "haiku"}))
    with pytest.raises(GuardError, match="changed"):
        accept(r, p, tmp_path / "guard")
    assert json.loads(target.read_text()) == {"model": "haiku"}


def test_accept_refuses_a_review_with_refusals(home: Path, qdir: Path, tmp_path: Path):
    target = home / ".claude" / "settings.json"
    target.write_text(canonical({"sandbox": {"enabled": True}}))
    owned = Applied(
        files={
            str(target): FileRecord(
                False, [], [Owned(Entry("scalar", "sandbox.enabled", True), prior=(False, None))]
            )
        }
    )
    propose(target, [Op("set", "sandbox.enabled", False)], "why", qdir)
    [(p, prop)] = list_proposals([qdir])
    with pytest.raises(GuardError):
        accept(review(prop, owned, home), p, tmp_path / "guard")
