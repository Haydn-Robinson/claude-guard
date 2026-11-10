"""End-to-end: the guard CLI against a temporary home (never your real settings)."""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

import pytest

from claude_guard.cli import Env, main
from claude_guard.context import Ctx

SNAP_DIR = Path(__file__).parent / "snapshots"


class Runner:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.proj = self.home / "repos" / "proj"
        (self.proj / ".git").mkdir(parents=True)
        self.gh = self.home / ".config" / "claude-guard"
        self.bin = tmp / "opt" / "bin" / "guard"
        self.bin.parent.mkdir(parents=True)
        self.bin.write_text("#!/bin/sh\n")
        self.bin.chmod(0o755)
        self.queue = tmp / "queue"
        self.cwd = self.proj
        self.interactive = True
        self.in_claude = False
        self.answers: list[bool] = []
        self.prompts: list[str] = []
        self.editor = None

    @property
    def user(self) -> Path:
        return self.home / ".claude" / "settings.json"

    @property
    def local(self) -> Path:
        return self.proj / ".claude" / "settings.local.json"

    @property
    def policy(self) -> Path:
        return self.gh / "policy.toml"

    def _confirm(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else True

    def run(self, *argv: str, stdin: str = "") -> tuple[int, str, str]:
        ctx = Ctx(
            home=self.home,
            cwd=self.cwd,
            guard_home=self.gh,
            managed=self.tmp / "etc" / "claude-code",
            hook=(str(self.bin),),
            run=lambda q: None,
            which=lambda t: f"/usr/bin/{t}",
            wsl=False,
            queue=[self.queue],
            claude_tmp=self.tmp / "claude-tmp",
        )
        out, err = io.StringIO(), io.StringIO()
        env = Env(
            ctx,
            out=out,
            err=err,
            stdin=io.StringIO(stdin),
            interactive=self.interactive,
            in_claude=self.in_claude,
            confirm=self._confirm,
            editor=self.editor or (lambda p: 0),
        )
        rc = main(list(argv), env)
        return rc, out.getvalue(), err.getvalue()

    def ok(self, *argv: str, **kw) -> str:
        rc, out, err = self.run(*argv, **kw)
        assert rc == 0, f"rc={rc}\n{out}\n{err}"
        return out


@pytest.fixture
def g(tmp_path: Path) -> Runner:
    return Runner(tmp_path)


def read(p: Path) -> dict:
    return json.loads(p.read_text())


def snapshot(name: str, text: str, tmp: Path) -> None:
    text = text.replace(str(tmp), "<TMP>").replace(f"claude-{os.getuid()}", "claude-<UID>")
    path = SNAP_DIR / name
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        path.write_text(text)
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            pytest.fail(f"snapshot {name} did not exist; created it. Review it, then re-run.")
        return
    assert text == path.read_text(), f"snapshot {name} differs (UPDATE_SNAPSHOTS=1 to refresh)"


# ---- init ------------------------------------------------------------------------------------------


def test_init_creates_policy_applies_baseline_and_runs_doctor(g: Runner):
    out = g.ok("init")
    assert g.policy.exists()
    data = read(g.user)
    assert data["sandbox"]["enabled"] is True
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == str(g.bin)
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["args"] == [
        "hook",
        "edit",
        "--guard-home",
        str(g.gh),
    ]
    assert "The baseline preset" in out and "0 failure(s)" in out
    assert g.prompts == ["Apply these changes?"]


def test_init_twice_refused(g: Runner):
    g.ok("init")
    rc, _, err = g.run("init")
    assert rc == 2 and "already exists" in err


def test_init_dry_run_changes_nothing(g: Runner):
    out = g.ok("init", "--dry-run")
    assert "Dry run" in out and not g.policy.exists() and not g.user.exists()


def test_declining_changes_nothing(g: Runner):
    g.answers = [False]
    rc, out, _ = g.run("init")
    assert rc == 1 and "Aborted" in out
    assert not g.policy.exists() and not g.user.exists()


def test_no_terminal_needs_yes(g: Runner):
    g.interactive = False
    rc, _, err = g.run("init")
    assert rc == 2 and "--yes" in err and not g.policy.exists()
    g.ok("init", "--yes")
    assert g.policy.exists()


def test_commands_before_init_say_so(g: Runner):
    rc, _, err = g.run("allow", str(g.tmp / "x"))
    assert rc == 2 and "guard init" in err


# ---- allow / revoke / protect ----------------------------------------------------------------------


def test_allow_defaults_to_full_for_the_current_project(g: Runner):
    g.ok("init")
    out_dir = g.tmp / "data" / "out"
    out_dir.mkdir(parents=True)
    g.ok("allow", str(out_dir))
    assert 'projects."~/repos/proj"' in g.policy.read_text()
    assert f"Edit(/{out_dir}/**)" in read(g.local)["permissions"]["allow"]
    assert "Edit" not in json.dumps(read(g.user)["permissions"].get("allow", []))


def test_allow_relative_path_and_comments_survive(g: Runner):
    g.ok("init")
    g.policy.write_text(g.policy.read_text() + "# my note\n")
    (g.proj / "build").mkdir()
    g.cwd = g.proj / "build"
    g.ok("allow", "../../other-out", "--create", "--access", "bash")
    target = g.home / "repos" / "other-out"
    assert target.is_dir()  # --create
    assert str(target) in read(g.local)["sandbox"]["filesystem"]["allowWrite"]
    assert "# my note" in g.policy.read_text()


def test_allow_everywhere(g: Runner):
    g.ok("init")
    d = g.tmp / "notes"
    d.mkdir()
    g.ok("allow", str(d), "--access", "ask", "--everywhere")
    assert f"Edit(/{d}/**)" in read(g.user)["permissions"]["ask"]
    assert str(d) in read(g.user)["sandbox"]["filesystem"]["denyWrite"]


def test_allow_outside_a_project_needs_a_scope(g: Runner):
    g.ok("init")
    g.cwd = g.home
    rc, _, err = g.run("allow", str(g.tmp / "x"))
    assert rc == 2 and "--everywhere" in err


def test_allow_above_a_ceiling_refused_and_nothing_changes(g: Runner):
    g.ok("init")
    secret = g.home / ".ssh"
    secret.mkdir()
    before = (g.policy.read_text(), g.user.read_text())
    rc, _, err = g.run("allow", str(secret), "--everywhere")
    assert rc == 2 and "ceiling" in err and "ask" in err
    assert (g.policy.read_text(), g.user.read_text()) == before
    g.ok("allow", str(secret), "--everywhere", "--access", "ask")


def test_allow_on_a_locked_path_refused(g: Runner):
    g.ok("init")
    rc, _, err = g.run("allow", str(g.gh), "--everywhere")
    assert rc == 2 and "locked" in err


def test_allow_conflict_refused(g: Runner):
    g.ok("init")
    d = g.tmp / "w"
    (d / "in").mkdir(parents=True)
    g.ok("allow", str(d), "--access", "edit")
    rc, _, err = g.run("allow", str(d / "in"))
    assert rc == 2 and "inside grant" in err


def test_revoke(g: Runner):
    g.ok("init")
    d = g.tmp / "w"
    d.mkdir()
    g.ok("allow", str(d))
    g.ok("revoke", str(d))
    assert not g.local.exists()  # guard created it, guard removed it
    assert "projects" not in g.policy.read_text()
    rc, _, err = g.run("revoke", str(d))
    assert rc == 2 and "no grant" in err


def test_protect_then_allow_above_the_ceiling(g: Runner):
    g.ok("init")
    d = g.tmp / "archive"
    d.mkdir()
    g.ok("protect", str(d), "--ceiling", "deny", "--everywhere")
    assert f"Edit(/{d}/**)" in read(g.user)["permissions"]["deny"]
    rc, _, err = g.run("allow", str(d / "x"), "--everywhere")
    assert rc == 2 and "ceiling" in err


# ---- apply, drift, plan, edit, uninstall -----------------------------------------------------------


def test_apply_twice_is_nothing(g: Runner):
    g.ok("init")
    assert "Nothing to change" in g.ok("apply")


def test_drift_is_reported_and_repaired(g: Runner):
    g.ok("init")
    data = read(g.user)
    data["sandbox"]["enabled"] = False
    g.user.write_text(json.dumps(data, indent=2) + "\n")
    st = g.ok("status")
    assert "do NOT match" in st and "changed by hand" in st
    g.ok("apply")
    assert read(g.user)["sandbox"]["enabled"] is True


def test_plan_changes_nothing(g: Runner):
    g.ok("init")
    g.policy.write_text(g.policy.read_text().replace('["baseline"]', '["baseline", "strict"]'))
    before = g.user.read_text()
    out = g.ok("plan")
    assert "Dry run" in out and g.user.read_text() == before


def test_edit_validates_and_applies(g: Runner):
    g.ok("init")

    def editor(path: Path) -> int:
        path.write_text(
            path.read_text().replace('["baseline"]', '["baseline", "confirm-destructive"]')
        )
        return 0

    g.editor = editor
    g.ok("edit")
    assert "Bash(rm *)" in read(g.user)["permissions"]["ask"]
    assert "confirm-destructive" in g.policy.read_text()


def test_edit_with_an_error_changes_nothing(g: Runner):
    g.ok("init")
    before = g.policy.read_text()

    def editor(path: Path) -> int:
        path.write_text("version = 1\n[everywhere]\nwrite = [{ path = 'rel', access = 'full' }]\n")
        return 0

    g.editor = editor
    g.answers = [False]  # "Edit again?" -> no
    rc, out, _ = g.run("edit")
    assert rc == 1 and "absolute" in out
    assert g.policy.read_text() == before
    assert not (g.gh / ".policy.edit.toml").exists()


def test_uninstall_restores_settings_and_keeps_the_policy(g: Runner):
    g.user.write_text(json.dumps({"model": "opus"}, indent=2) + "\n")
    g.ok("init")
    d = g.tmp / "w"
    d.mkdir()
    g.ok("allow", str(d))
    g.ok("uninstall")
    assert read(g.user) == {"model": "opus"}
    assert not g.local.exists()
    assert g.policy.exists()


# ---- views ------------------------------------------------------------------------------------------


def test_status_snapshot(g: Runner):
    g.ok("init")
    d = g.tmp / "data"
    d.mkdir()
    g.ok("allow", str(d), "--access", "edit", "--protect", "*.lock")
    snapshot("v2_status.txt", g.ok("status"), g.tmp)
    s = json.loads(g.ok("status", "--json", "--verbose"))
    assert s["in_sync"] and s["baseline"]
    assert any(x["access"] == "edit" for sc in s["scopes"] for x in sc["grants"])
    assert str(g.local) in s["files"]


def test_status_lists_unmanaged_entries(g: Runner):
    g.user.write_text(json.dumps({"permissions": {"allow": ["Bash(curl *)"]}}) + "\n")
    g.ok("init")
    out = g.ok("status", "--verbose")
    assert "not managed by guard" in out and "Bash(curl *)" in out


def test_doctor_healthy(g: Runner):
    g.ok("init")
    out = g.ok("doctor")
    snapshot("v2_doctor.txt", out, g.tmp)


def test_doctor_fails_when_the_hook_executable_is_missing(g: Runner):
    g.ok("init")
    g.bin.unlink()
    rc, out, _ = g.run("doctor")
    assert rc == 1 and "fail-closed" in out


def test_doctor_fails_when_the_hook_is_inside_the_project(g: Runner):
    g.bin = g.proj / "bin" / "guard"
    g.bin.parent.mkdir()
    g.bin.write_text("#!/bin/sh\n")
    g.bin.chmod(0o755)
    g.ok("init")
    rc, out, _ = g.run("doctor")
    assert rc == 1 and "uv tool install" in out


def test_explain_path(g: Runner):
    g.ok("init")
    outside = g.tmp / "elsewhere" / "f.txt"
    out = g.ok("explain", str(outside))
    assert "ASKS" in out and "read-only" in out
    d = g.tmp / "data"
    d.mkdir()
    g.ok("allow", str(d), "--access", "edit")
    out = g.ok("explain", str(d / "f.txt"))
    assert "allowed without a prompt" in out and "BLOCKED" in out
    inside = g.ok("explain", str(g.proj / "src" / "a.py"))
    assert "inside the project" in inside and "writable" in inside


def test_explain_command(g: Runner):
    g.ok("init")
    assert "DENIED" in g.ok("explain", "--command", "sudo ls")
    assert "no rule" in g.ok("explain", "--command", "ls -la")


def test_presets(g: Runner):
    out = g.ok("presets")
    assert "baseline" in out and "strict" in out
    show = g.ok("preset", "show", "confirm-destructive")
    assert "Bash(rm *)" in show


# ---- propose / review --------------------------------------------------------------------------------


def test_propose_then_review_and_accept(g: Runner):
    g.ok("init")
    script = g.home / "bin" / "status.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    out = g.ok(
        "propose",
        str(g.user),
        "--set",
        f"statusLine.command={script}",
        "--set",
        "statusLine.type=command",
        "--reason",
        "show the branch",
    )
    assert "guard review" in out
    assert "statusLine" not in read(g.user)  # nothing changed yet
    g.answers = [True, True]  # accept, then confirm the re-apply
    out = g.ok("review")
    assert "outside the sandbox" in out and "Accepted" in out
    assert read(g.user)["statusLine"]["command"] == str(script)
    assert list(g.queue.glob("*.json")) == []
    st = json.loads(g.ok("status", "--json"))
    assert any(p["path"] == str(script) for p in st["scopes"][0]["protected"])  # now protected


def test_review_needs_a_keyboard(g: Runner):
    g.ok("init")
    g.ok("propose", str(g.user), "--set", "model=opus", "--reason", "r")
    g.interactive = False
    rc, _, err = g.run("review")
    assert rc == 2 and "separate terminal" in err
    g.interactive, g.in_claude = True, True
    rc, _, err = g.run("review")
    assert rc == 2
    assert "model" not in read(g.user)


def test_review_refuses_guard_owned_entries(g: Runner):
    g.ok("init")
    g.ok("propose", str(g.user), "--set", "sandbox.enabled=false", "--reason", "faster")
    out = g.ok("review")
    assert "Refused" in out and read(g.user)["sandbox"]["enabled"] is True


def test_propose_rejects_other_files(g: Runner):
    rc, _, err = g.run("propose", str(g.home / ".bashrc"), "--set", "a=1", "--reason", "r")
    assert rc == 2 and "target" in err


def test_ops_keep_their_order(g: Runner):
    g.ok("init")
    g.ok(
        "propose",
        str(g.user),
        "--add",
        "permissions.allow=Bash(make *)",
        "--remove",
        "permissions.allow=Bash(make *)",
        "--reason",
        "noop",
    )
    [f] = g.queue.glob("*.json")
    assert [o["kind"] for o in json.loads(f.read_text())["ops"]] == ["add", "remove"]


# ---- the hook ----------------------------------------------------------------------------------------


def test_hook_edit_through_main(g: Runner, monkeypatch):
    g.ok("init")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(g.proj))
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(g.tmp / "x")}})
    out = g.ok("hook", "edit", "--guard-home", str(g.gh), stdin=payload)
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_review_reapplies_so_new_scripts_are_protected(g: Runner):
    from claude_guard.applied import load_applied

    g.ok("init")
    script = g.home / "bin" / "s.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    g.ok("propose", str(g.user), "--set", f"apiKeyHelper={script}", "--reason", "key")
    g.answers = [True, True]
    g.ok("review")
    protected = load_applied(g.gh).hook["scopes"]["*"]["protected"]
    assert {"path": str(script), "ceiling": "ask"} in protected


def test_an_existing_project_key_is_reused(g: Runner):
    g.ok("init")
    g.policy.write_text(g.policy.read_text() + f'\n[projects."{g.proj}"]\ncommands.ask = ["x *"]\n')
    d = g.tmp / "d"
    d.mkdir()
    g.ok("allow", str(d))
    text = g.policy.read_text()
    assert text.count("[projects.") == 1 and "~/repos/proj" not in text


def test_a_policy_only_change_is_still_saved(g: Runner):
    g.ok("init")

    def editor(path: Path) -> int:
        path.write_text(path.read_text() + "# why the baseline: safety\n")
        return 0

    g.editor = editor
    g.ok("edit")
    assert "# why the baseline" in g.policy.read_text()


def test_explain_knows_the_builtin_protection_of_project_settings(g: Runner):
    g.ok("init")
    out = g.ok("explain", str(g.proj / ".claude" / "skills" / "s.md"))
    assert "BLOCKED" in out and "built-in" in out


def test_explain_the_session_s_memory_folder(g: Runner, monkeypatch):
    """The hook leaves the session's own auto-memory folder open (VB-E11); other projects' stay
    closed."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    g.ok("init")
    projects = g.home / ".claude" / "projects"
    own = projects / re.sub(r"[^A-Za-z0-9]", "-", str(g.proj)) / "memory" / "MEMORY.md"
    assert "the session's own memory folder" in g.ok("explain", str(own))
    other = projects / "-somewhere-else" / "memory" / "MEMORY.md"
    assert "ASKS" in g.ok("explain", str(other))
