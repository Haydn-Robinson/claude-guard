from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from claude_guard.cli import Env, main
from claude_guard.pathsafety import PathPolicy

SNAP_DIR = Path(__file__).parent / "snapshots"


class Runner:
    def __init__(self, tmp_path: Path, home: Path, proj: Path, policy: PathPolicy):
        self.tmp = tmp_path
        self.home, self.proj, self.policy = home, proj, policy
        self.state_dir = tmp_path / "state"
        self.managed = tmp_path / "managed"
        self.interactive = False
        self.answer = True
        self.prompts: list[str] = []
        self.hook_exe = tmp_path / "bin" / "guard"
        self.hook_exe.parent.mkdir()
        self.hook_exe.write_text("#!/bin/sh\n")
        self.hook_exe.chmod(0o755)

    def run(self, *argv: str, cwd: Path | None = None, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()

        def confirm(prompt: str) -> bool:
            self.prompts.append(prompt)
            return self.answer

        env = Env(
            home=self.home,
            cwd=cwd or self.proj,
            state_dir=self.state_dir,
            out=out,
            err=err,
            interactive=self.interactive,
            confirm=confirm,
            policy=self.policy,
            managed_dir=self.managed,
            which=lambda name: f"/usr/bin/{name}",
            stdin=io.StringIO(stdin),
            hook_command=[str(self.hook_exe)],
        )
        rc = main(list(argv), env)
        return rc, out.getvalue(), err.getvalue()

    def norm(self, text: str) -> str:
        return text.replace(str(self.tmp), "<TMP>")


@pytest.fixture
def cli(tmp_path: Path, home: Path, proj: Path, policy: PathPolicy) -> Runner:
    return Runner(tmp_path, home, proj, policy)


def snap(name: str, text: str) -> None:
    path = SNAP_DIR / f"{name}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not path.exists():
        SNAP_DIR.mkdir(exist_ok=True)
        path.write_text(text)
        if os.environ.get("UPDATE_SNAPSHOTS") != "1":
            pytest.fail(f"snapshot {name} did not exist; created it. Review it, then re-run.")
        return
    assert text == path.read_text(), f"snapshot {name} differs (UPDATE_SNAPSHOTS=1 to refresh)"


def read(p: Path) -> dict:
    return json.loads(p.read_text())


# ---- allow -----------------------------------------------------------------------------------


def test_allow_dry_run_changes_nothing_and_matches_snapshot(
    cli: Runner, target: Path, settings: Path
):
    rc, out, _ = cli.run(
        "allow",
        str(target),
        "edit",
        "git-safe",
        "--protect",
        ".private/**",
        "--protect",
        "archive/**",
        "--dry-run",
    )
    assert rc == 0
    assert not settings.exists() and not cli.state_dir.exists()
    assert "Dry run: nothing was changed." in out
    snap("allow_dry_run_target", cli.norm(out))


def test_allow_applies_with_yes(cli: Runner, target: Path, settings: Path):
    rc, out, _ = cli.run("allow", str(target), "--yes")
    assert rc == 0 and "Applied." in out
    data = read(settings)
    assert str(target) in data["sandbox"]["filesystem"]["allowWrite"]  # default profile: edit
    assert "Read(~/.ssh/**)" in data["permissions"]["deny"]  # secrets auto-applied
    assert "Edit tool" not in out and "Edit/Write tools are NOT sandboxed" in out
    assert "guard allow --global lock" in out  # sandbox not enabled hint


def test_secrets_only_on_first_management_and_can_be_skipped(
    cli: Runner, tmp_path: Path, target: Path, settings: Path
):
    other = tmp_path / "other"
    other.mkdir()
    assert cli.run("allow", str(target), "--yes", "--no-secrets")[0] == 0
    assert "Read(~/.ssh/**)" not in read(settings)["permissions"].get("deny", [])
    assert cli.run("allow", str(other), "--yes")[0] == 0  # not first any more -> still no secrets
    assert "Read(~/.ssh/**)" not in read(settings)["permissions"]["deny"]


def test_secrets_not_duplicated_for_second_folder(
    cli: Runner, tmp_path: Path, target: Path, settings: Path
):
    other = tmp_path / "other"
    other.mkdir()
    cli.run("allow", str(target), "--yes")
    cli.run("allow", str(other), "--yes")
    assert read(settings)["permissions"]["deny"].count("Read(~/.ssh/**)") == 1


def test_option_order_is_flexible(cli: Runner, target: Path, settings: Path):
    rc, _, err = cli.run(
        "allow", "--scope", "local", str(target), "edit", "--protect", "x/**", "--yes"
    )
    assert rc == 0, err
    assert any(r.endswith("/x/**)") for r in read(settings)["permissions"]["deny"])


def test_non_tty_without_yes_refuses(cli: Runner, target: Path, settings: Path):
    rc, _, err = cli.run("allow", str(target))
    assert rc == 2 and "--yes" in err
    assert not settings.exists()


def test_interactive_decline_and_accept(cli: Runner, target: Path, settings: Path):
    cli.interactive = True
    cli.answer = False
    rc, out, _ = cli.run("allow", str(target))
    assert rc == 1 and "Aborted" in out and not settings.exists()
    assert cli.prompts == ["Apply these changes?"]
    cli.answer = True
    assert cli.run("allow", str(target))[0] == 0 and settings.exists()


def test_create_flag(cli: Runner, tmp_path: Path, settings: Path):
    new = tmp_path / "brand" / "new"
    rc, out, _ = cli.run("allow", str(new), "--create", "--dry-run")
    assert rc == 0 and "Would create folder" in out and not new.exists()
    assert "does not exist" not in out
    rc, out, _ = cli.run("allow", str(new), "--create", "--yes")
    assert rc == 0 and new.is_dir()


def test_missing_folder_without_create_warns(cli: Runner, tmp_path: Path):
    rc, out, _ = cli.run("allow", str(tmp_path / "ghost"), "--dry-run")
    assert rc == 0 and "warning:" in out and "does not exist" in out


@pytest.mark.parametrize("bad", ["/", "/mnt/d", "/mnt/c/Windows", "/mnt/c/Users/haydn"])
def test_unsafe_folder_rejected(cli: Runner, settings: Path, bad: str):
    rc, _, err = cli.run("allow", bad, "--yes")
    assert rc == 2 and err.startswith("error:")
    assert not settings.exists() and not cli.state_dir.exists()


def test_scope_user_writes_home_settings(cli: Runner, target: Path, home: Path, settings: Path):
    assert cli.run("allow", str(target), "--scope", "user", "--yes")[0] == 0
    assert (home / ".claude" / "settings.json").exists() and not settings.exists()


def test_project_option(cli: Runner, tmp_path: Path, target: Path):
    other = tmp_path / "other-proj"
    other.mkdir()
    assert cli.run("allow", str(target), "--project", str(other), "--yes")[0] == 0
    assert (other / ".claude" / "settings.local.json").exists()


def test_bad_project_dir(cli: Runner, target: Path, tmp_path: Path):
    rc, _, err = cli.run("allow", str(target), "--project", str(tmp_path / "nope"), "--yes")
    assert rc == 2 and "project directory" in err


def test_global_lock_and_hint_goes_away(cli: Runner, target: Path, settings: Path):
    rc, out, _ = cli.run("allow", "--global", "lock", "--yes")
    assert rc == 0 and "guard allow --global lock" not in out
    assert read(settings)["sandbox"]["enabled"] is True
    assert cli.run("allow", "--global")[0] == 2  # needs profile names


def test_edit_ask_warning_shown(cli: Runner, target: Path):
    _, out, _ = cli.run("allow", str(target), "edit-ask", "--dry-run")
    assert "warning: profile edit-ask" in out


def test_noop_second_allow(cli: Runner, target: Path):
    cli.run("allow", str(target), "--yes")
    rc, out, _ = cli.run("allow", str(target), "--yes")
    assert rc == 0 and "Nothing to change." in out


# ---- revoke ----------------------------------------------------------------------------------


def test_revoke_dry_run_snapshot_then_apply_restores_exact_file(
    cli: Runner, target: Path, settings: Path
):
    original = (
        json.dumps({"model": "opus", "permissions": {"allow": ["Bash(ls *)"]}}, indent=2) + "\n"
    )
    settings.write_text(original)
    cli.run("allow", str(target), "edit", "git-safe", "--protect", ".private/**", "--yes")
    assert settings.read_text() != original
    rc, out, _ = cli.run("revoke", str(target), "--dry-run")
    assert rc == 0 and "Dry run" in out
    snap("revoke_dry_run_target", cli.norm(out))
    assert cli.run("revoke", str(target), "--yes")[0] == 0
    # secrets was auto-applied as a separate global grant; remove it too
    assert cli.run("revoke", "--global", "--yes")[0] == 0
    assert settings.read_text() == original


def test_revoke_single_profile(cli: Runner, target: Path, settings: Path):
    cli.run("allow", str(target), "edit", "git-safe", "--yes")
    assert cli.run("revoke", str(target), "git-safe", "--yes")[0] == 0
    allow = read(settings)["permissions"]["allow"]
    assert "Bash(git status *)" not in allow and "Bash(mkdir *)" in allow


def test_revoke_unknown(cli: Runner, target: Path):
    rc, _, err = cli.run("revoke", str(target), "--yes")
    assert rc == 2 and "no grant" in err
    assert cli.run("revoke", "--yes")[0] == 2


# ---- status ----------------------------------------------------------------------------------


def test_status_text_snapshot_and_unmanaged(cli: Runner, target: Path, settings: Path):
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(curl *)"]}}, indent=2) + "\n")
    cli.run("allow", str(target), "edit", "git-safe", "--protect", ".private/**", "--yes")
    rc, out, _ = cli.run("status")
    assert rc == 0 and "unmanaged permissions.allow:" in out and "Bash(curl *)" in out
    assert "protected: .private/**" in out
    snap("status_after_grant", cli.norm(out))


def test_status_json(cli: Runner, target: Path, settings: Path):
    cli.run("allow", str(target), "edit", "--protect", "archive/**", "--yes")
    rc, out, _ = cli.run("status", "--json")
    data = json.loads(out)
    assert rc == 0
    local = next(f for f in data["files"] if f["scope"] == "local")
    folders = {g["folder"]: g for g in local["grants"]}
    assert folders[str(target)]["profiles"] == ["edit"]
    assert folders[str(target)]["protected_paths"] == ["archive/**"]
    assert None in folders  # global secrets grant
    assert local["unmanaged"] == {}
    user = next(f for f in data["files"] if f["scope"] == "user")
    assert user["exists"] is False and user["grants"] == []


def test_status_with_nothing(cli: Runner):
    rc, out, _ = cli.run("status")
    assert rc == 0 and "no grants managed" in out


def test_status_shows_other_projects_files(cli: Runner, tmp_path: Path, target: Path):
    other = tmp_path / "other-proj"
    other.mkdir()
    cli.run("allow", str(target), "--project", str(other), "--yes")
    _, out, _ = cli.run("status")
    assert "Other managed file" in out and "other-proj" in out


# ---- profiles --------------------------------------------------------------------------------


def test_profiles_list_snapshot(cli: Runner):
    rc, out, _ = cli.run("profiles")
    assert rc == 0
    snap("profiles_list", out)


def test_profile_show_snapshot_and_unknown(cli: Runner):
    rc, out, _ = cli.run("profile", "show", "edit")
    assert rc == 0
    snap("profile_show_edit", out)
    _, out, _ = cli.run("profile", "show", "edit-ask")
    assert "WARNING:" in out
    rc, _, err = cli.run("profile", "show", "nope")
    assert rc == 2 and "unknown profile" in err


def test_user_profiles_file_is_used(cli: Runner):
    cli.state_dir.mkdir()
    (cli.state_dir / "profiles.toml").write_text(
        '[mine]\ndescription = "x"\nbash_allow = ["Bash(foo *)"]\n'
    )
    assert "mine" in cli.run("profiles")[1]


# ---- doctor ----------------------------------------------------------------------------------


def test_doctor_fails_without_sandbox_and_passes_after_lock(cli: Runner, target: Path):
    rc, out, _ = cli.run("doctor")
    assert rc == 1 and "[FAIL] sandbox enabled" in out
    cli.run("allow", "--global", "lock", "--yes")
    rc, out, _ = cli.run("doctor")
    assert rc == 0, out
    assert "0 failure(s), 0 warning(s)" in out
    snap("doctor_healthy", cli.norm(out))


def test_doctor_missing_tools(cli: Runner, tmp_path: Path, policy, home, proj):
    out, err = io.StringIO(), io.StringIO()
    env = Env(
        home=home,
        cwd=proj,
        state_dir=tmp_path / "s",
        out=out,
        err=err,
        policy=policy,
        managed_dir=tmp_path / "m",
        which=lambda n: None,
    )
    assert main(["doctor"], env) == 1
    assert "[FAIL] bwrap installed" in out.getvalue() and "[FAIL] socat installed" in out.getvalue()


def test_doctor_detects_hand_edits_missing_folder_and_auto_mode(
    cli: Runner, tmp_path: Path, target: Path, settings: Path
):
    cli.run("allow", str(target), "edit", "--yes")
    cli.run("allow", "--global", "lock", "--yes")
    data = read(settings)
    data["permissions"]["allow"].remove("Bash(cp *)")
    data["permissions"]["defaultMode"] = "auto"
    settings.write_text(json.dumps(data, indent=2) + "\n")
    target.rmdir()
    rc, out, _ = cli.run("doctor")
    assert rc == 0
    assert "[warn] managed entries intact" in out and "Bash(cp *)" in out
    assert "[warn] granted folder exists" in out
    assert "[warn] Edit/Write tools in auto mode" in out


def test_doctor_reports_invalid_settings_json(cli: Runner, settings: Path):
    settings.write_text("{nope")
    rc, out, _ = cli.run("doctor")
    assert rc == 1 and "[FAIL] settings JSON valid" in out


def test_doctor_flags_system_drive_grant(cli: Runner, settings: Path, policy):
    # Craft state by hand: a grant on /mnt/c (could only exist from older versions or hand edits)
    state_dir = cli.state_dir
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "grants": {
                    "g": {
                        "folder": "/mnt/c/Users/haydn/proj",
                        "settings_file": str(settings),
                        "profiles": ["edit"],
                        "protect": [],
                        "created_at": "x",
                        "entries": [],
                    }
                },
                "files": {},
            }
        )
    )
    _, out, _ = cli.run("doctor")
    assert "[warn] granted folder on system drive" in out


def test_doctor_detects_inconsistent_state(cli: Runner, settings: Path):
    cli.state_dir.mkdir()
    (cli.state_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "grants": {
                    "g": {
                        "folder": None,
                        "settings_file": str(settings),
                        "profiles": [],
                        "protect": [],
                        "created_at": "x",
                        "entries": [[str(settings), "missing-key"]],
                    }
                },
                "files": {},
            }
        )
    )
    rc, out, _ = cli.run("doctor")
    assert rc == 1 and "[FAIL] state consistent" in out


def test_doctor_json(cli: Runner):
    _, out, _ = cli.run("doctor", "--json")
    assert {"level", "name", "detail"} <= set(json.loads(out)[0])


# ---- real entry point ------------------------------------------------------------------------


def test_console_script_smoke(tmp_path: Path):
    """Run the real entry point as a subprocess against a fake HOME and state dir."""
    import subprocess
    import sys

    fake_home, proj, folder = tmp_path / "fakehome", tmp_path / "p", tmp_path / "f"
    for d in (fake_home, proj, folder):
        d.mkdir()
    env = {**os.environ, "HOME": str(fake_home), "CLAUDE_GUARD_HOME": str(tmp_path / "gstate")}
    code = "import sys; from claude_guard import main; sys.exit(main())"

    def guard(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code, *args],
            check=False,
            cwd=proj,
            env=env,
            capture_output=True,
            text=True,
            input="",
        )

    r = guard("allow", str(folder), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "Dry run" in r.stdout and not (proj / ".claude").exists()
    r = guard("allow", str(folder))
    assert r.returncode == 2 and "--yes" in r.stderr  # stdin is not a terminal
    r = guard("allow", str(folder), "--yes")
    assert r.returncode == 0, r.stderr
    assert (proj / ".claude" / "settings.local.json").exists()
    assert (tmp_path / "gstate" / "state.json").exists()
    assert not (fake_home / ".claude").exists()  # local scope only


# ---- hook ------------------------------------------------------------------------------------


def hook_input(path: str, cwd: Path, tool: str = "Write") -> str:
    return json.dumps(
        {
            "tool_name": tool,
            "tool_input": {"file_path": path},
            "cwd": str(cwd),
            "permission_mode": "auto",
        }
    )


def test_hook_cli_silent_inside_project_and_logs(cli: Runner):
    rc, out, _ = cli.run(
        "hook", "edit", "--state-dir", str(cli.state_dir),
        stdin=hook_input(str(cli.proj / "a.txt"), cli.proj),
    )  # fmt: skip
    assert rc == 0 and out == ""
    log = [json.loads(line) for line in (cli.state_dir / "hook.log").read_text().splitlines()]
    assert log[0]["decision"] == "none" and log[0]["permission_mode"] == "auto"


def test_hook_cli_asks_outside_with_claude_code_json(cli: Runner, tmp_path: Path):
    rc, out, _ = cli.run(
        "hook", "edit", "--state-dir", str(cli.state_dir),
        stdin=hook_input(str(tmp_path / "elsewhere" / "a.txt"), cli.proj),
    )  # fmt: skip
    assert rc == 0
    spec = json.loads(out)["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "ask"
    assert "guard allow" in spec["permissionDecisionReason"]


def test_hook_cli_strict_denies(cli: Runner, tmp_path: Path):
    _, out, _ = cli.run(
        "hook", "edit", "--state-dir", str(cli.state_dir), "--unlisted", "deny",
        stdin=hook_input(str(tmp_path / "elsewhere" / "a.txt"), cli.proj),
    )  # fmt: skip
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_cli_bad_input_fails_closed(cli: Runner):
    rc, out, err = cli.run("hook", "edit", "--state-dir", str(cli.state_dir), stdin="not json")
    assert rc == 2 and out == "" and "not valid JSON" in err


def test_hook_cli_corrupt_state_fails_closed(cli: Runner):
    cli.state_dir.mkdir()
    (cli.state_dir / "state.json").write_text("{broken")
    rc, out, _ = cli.run(
        "hook", "edit", "--state-dir", str(cli.state_dir),
        stdin=hook_input(str(cli.proj / "a.txt"), cli.proj),
    )  # fmt: skip
    assert rc != 0 and out == ""


def test_allow_confine_edits_end_to_end(cli: Runner, target: Path):
    rc, out, _ = cli.run("allow", "--global", "confine-edits", "--yes")
    assert rc == 0
    settings = cli.proj / ".claude" / "settings.local.json"
    (h,) = read(settings)["hooks"]["PreToolUse"][0]["hooks"]
    assert h["command"] == str(cli.hook_exe) and h["onFailure"] == "block"
    rc, out, _ = cli.run("status")
    assert "hook PreToolUse" in out and "--unlisted ask" in out
    assert cli.run("revoke", "--global", "--yes")[0] == 0
    assert not settings.exists() or "hooks" not in read(settings)


def test_doctor_warns_about_auto_mode_without_hook(cli: Runner):
    (cli.proj / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"defaultMode": "auto"}})
    )
    _, out, _ = cli.run("doctor")
    assert "confine-edits" in out


def test_doctor_ok_with_hook_and_fails_when_command_vanishes(cli: Runner):
    cli.run("allow", "--global", "confine-edits", "--yes")
    settings = cli.proj / ".claude" / "settings.local.json"
    data = read(settings)
    data["permissions"]["defaultMode"] = "auto"
    settings.write_text(json.dumps(data, indent=2) + "\n")
    rc, out, _ = cli.run("doctor")
    assert (
        "[ok]   Edit/Write hook" in out and "confine-edits" not in out.split("Edit/Write hook")[0]
    )
    cli.hook_exe.unlink()
    rc, out, _ = cli.run("doctor")
    assert rc == 1 and "[FAIL] Edit/Write hook command" in out


# ---- explain ---------------------------------------------------------------------------------


def test_explain_path_text_and_json(cli: Runner, target: Path):
    cli.run("allow", str(target), "edit-ask", "--protect", "p/**", "--yes")
    rc, out, _ = cli.run("explain", str(target / "p" / "f.txt"))
    assert rc == 0
    assert "Edit/Write tools" in out and "DENIED" in out and "Bash writes" in out
    rc, out, _ = cli.run("explain", str(target / "f.txt"), "--json")
    data = json.loads(out)
    assert data["kind"] == "path"
    assert {s["id"]: s["verdict"]["code"] for s in data["sections"]}["edit"] == "ask"


def test_explain_command(cli: Runner, target: Path):
    cli.run("allow", "--global", "floor", "--yes")
    rc, out, _ = cli.run("explain", "--command", "sudo rm x")
    assert rc == 0 and "DENIED" in out and "guard" in out
    rc, out, _ = cli.run("explain", "--json", "--command", "echo hi")
    assert json.loads(out)["sections"][0]["verdict"]["code"] == "none"


def test_explain_relative_path_uses_cwd(cli: Runner):
    rc, out, _ = cli.run("explain", "src/a.py")
    assert rc == 0 and str(cli.proj / "src" / "a.py") in out


def test_explain_bad_project(cli: Runner, tmp_path: Path):
    rc, _, err = cli.run("explain", "x", "--project", str(tmp_path / "nope"))
    assert rc == 2 and "does not exist" in err
