from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_guard.engine import Guard
from claude_guard.errors import GuardError, SettingsError, UnsafeFolderError


def allow(g: Guard, folder, profiles, settings: Path, **kw):
    plan = g.plan_allow(folder, profiles, settings, **kw)
    g.commit(plan)
    return plan


def revoke(g: Guard, folder, settings: Path, profiles=None):
    plan = g.plan_revoke(folder, settings, profiles)
    g.commit(plan)
    return plan


def read(p: Path) -> dict:
    return json.loads(p.read_text())


def canonical(d: dict, indent=2) -> str:
    return json.dumps(d, indent=indent, ensure_ascii=False) + "\n"


# ---- exact generated settings ----------------------------------------------------------------


def test_edit_grant_generates_expected_entries(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit"], settings, protect=[".private/**", "archive/**"])
    data = read(settings)
    perms = data["permissions"]
    assert f"Edit(//{str(target).lstrip('/')}/**)" in perms["allow"]  # spaces preserved
    assert "Bash(mkdir *)" in perms["allow"]
    assert data["sandbox"]["filesystem"]["allowWrite"] == [str(target)]
    assert f"Edit(//{str(target).lstrip('/')}/.private/**)" in perms["deny"]
    assert f"Edit(//{str(target).lstrip('/')}/archive/**)" in perms["deny"]


def test_self_protection_rules(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit"], settings)
    deny = read(settings)["permissions"]["deny"]
    assert f"Edit(//{str(settings).lstrip('/')})" in deny
    assert f"Edit(//{str(guard.state_dir).lstrip('/')}/**)" in deny


def test_secrets_and_lock_are_global(guard: Guard, settings: Path):
    allow(guard, None, ["secrets", "lock"], settings)
    data = read(settings)
    assert "Read(~/.ssh/**)" in data["permissions"]["deny"]
    assert "Read(//**/.env)" in data["permissions"]["deny"]
    assert data["permissions"]["disableBypassPermissionsMode"] == "disable"
    assert data["sandbox"] == {"enabled": True, "allowUnsandboxedCommands": False}


def test_git_destructive_goes_to_ask_not_allow(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["git-destructive"], settings)
    perms = read(settings)["permissions"]
    assert "Bash(git reset --hard *)" in perms["ask"]
    assert not any("git reset" in r for r in perms.get("allow", []))


def test_profile_needing_folder_without_folder(guard: Guard, settings: Path):
    with pytest.raises(GuardError, match="needs a folder"):
        guard.plan_allow(None, ["edit"], settings)


# ---- refusal -----------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["/", "/mnt/d", "/mnt/c/Windows", "/mnt/c/Users/haydn"])
def test_unsafe_folders_refused_and_nothing_written(guard: Guard, settings: Path, bad: str):
    with pytest.raises(UnsafeFolderError):
        guard.plan_allow(bad, ["edit"], settings)
    assert not settings.exists() and not guard.state_file.exists()


def test_home_refused(guard: Guard, home: Path, settings: Path):
    with pytest.raises(UnsafeFolderError):
        guard.plan_allow(home, ["edit"], settings)


def test_bad_protect_glob_refused(guard: Guard, target: Path, settings: Path):
    for glob in ["/etc", "../x", ""]:
        with pytest.raises(GuardError):
            guard.plan_allow(target, ["edit"], settings, protect=[glob])


def test_missing_folder_warns(guard: Guard, tmp_path: Path, settings: Path):
    plan = guard.plan_allow(tmp_path / "nope" / "dir", ["edit"], settings)
    assert any("does not exist" in w for w in plan.warnings)


def test_profile_warning_is_reported(guard: Guard, target: Path, settings: Path):
    plan = guard.plan_allow(target, ["bash-readonly"], settings)
    assert any("bash-readonly" in w for w in plan.warnings)


# ---- dry run -----------------------------------------------------------------------------------


def test_plan_writes_nothing_and_diff_shows_changes(guard: Guard, target: Path, settings: Path):
    plan = guard.plan_allow(target, ["edit"], settings)
    assert not settings.exists() and not guard.state_file.exists()
    diff = plan.diff()
    assert "+++" in diff and "allowWrite" in diff and "/dev/null" in diff


# ---- round trips -------------------------------------------------------------------------------


def test_grant_revoke_on_missing_file_leaves_no_file(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit", "git-safe", "lock"], settings, protect=["x/**"])
    assert settings.exists()
    revoke(guard, target, settings)
    assert not settings.exists()
    assert read(guard.state_file)["grants"] == {} and read(guard.state_file)["files"] == {}


@pytest.mark.parametrize("indent", [2, 4])
def test_roundtrip_byte_identical_on_existing_file(
    guard: Guard, target: Path, settings: Path, indent
):
    original = canonical(
        {
            "model": "opus",
            "env": {"A": "1"},
            "permissions": {"allow": ["Bash(ls *)"], "defaultMode": "auto"},
            "hooks": {},
        },
        indent,
    )
    settings.write_text(original)
    allow(guard, target, ["edit", "git-safe", "secrets", "lock"], settings, protect=[".private/**"])
    assert settings.read_text() != original
    revoke(guard, target, settings)  # secrets/lock were part of the same grant: all gone
    assert settings.read_text() == original


def test_roundtrip_with_empty_existing_arrays(guard: Guard, target: Path, settings: Path):
    original = canonical({"permissions": {"allow": [], "deny": []}, "sandbox": {}})
    settings.write_text(original)
    allow(guard, target, ["edit"], settings)
    revoke(guard, target, settings)
    assert settings.read_text() == original  # user's own empty containers are kept


def test_noncanonical_file_roundtrips_semantically(guard: Guard, target: Path, settings: Path):
    settings.write_text('{"permissions": {"allow": ["A","B"]},   "x": 1}')
    before = read(settings)
    plan = allow(guard, target, ["edit"], settings)
    assert plan.changes[0].reformats
    revoke(guard, target, settings)
    assert read(settings) == before


def test_unknown_keys_preserved(guard: Guard, target: Path, settings: Path):
    settings.write_text(canonical({"zzz": {"deep": [1, {"a": None}]}, "aaa": 1}))
    allow(guard, target, ["edit"], settings)
    data = read(settings)
    assert list(data)[:2] == ["zzz", "aaa"]
    assert data["zzz"] == {"deep": [1, {"a": None}]}


# ---- idempotence -------------------------------------------------------------------------------


def test_allow_twice_is_noop(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit", "git-safe"], settings)
    text, state = settings.read_text(), guard.state_file.read_text()
    plan = guard.plan_allow(target, ["edit", "git-safe"], settings)
    assert plan.noop and not plan.changes
    guard.commit(plan)
    assert settings.read_text() == text and guard.state_file.read_text() == state
    assert len(read(settings)["permissions"]["allow"]) == len(
        set(read(settings)["permissions"]["allow"])
    )


# ---- sharing / reference counting --------------------------------------------------------------


def test_overlapping_grants_share_entries(guard: Guard, tmp_path: Path, settings: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    allow(guard, a, ["edit", "git-safe"], settings)
    allow(guard, b, ["edit", "git-safe"], settings)
    allow_list = read(settings)["permissions"]["allow"]
    assert allow_list.count("Bash(mkdir *)") == 1  # shared global entry, stored once
    revoke(guard, a, settings)
    data = read(settings)
    assert "Bash(mkdir *)" in data["permissions"]["allow"]  # still needed by b
    assert str(a) not in data["sandbox"]["filesystem"]["allowWrite"]
    assert str(b) in data["sandbox"]["filesystem"]["allowWrite"]
    assert not any(str(a) in r for r in data["permissions"]["allow"])
    revoke(guard, b, settings)
    assert not settings.exists()


def test_revoke_one_profile_keeps_others(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit", "python", "git-safe"], settings)
    revoke(guard, target, settings, ["python"])
    allow_list = read(settings)["permissions"]["allow"]
    assert "Bash(pytest *)" not in allow_list
    assert "Bash(git status *)" in allow_list and "Bash(mkdir *)" in allow_list
    revoke(guard, target, settings, ["edit", "git-safe"])
    assert not settings.exists()


def test_allow_adds_profiles_to_existing_grant(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit"], settings)
    allow(guard, target, ["python"], settings, protect=["x/**"])
    state = read(guard.state_file)
    (grant,) = state["grants"].values()
    assert grant["profiles"] == ["edit", "python"] and grant["protect"] == ["x/**"]
    assert "Bash(uv run *)" in read(settings)["permissions"]["allow"]


def test_two_settings_files_are_independent(guard: Guard, target: Path, settings: Path, home: Path):
    other = home / ".claude" / "settings.json"
    allow(guard, target, ["edit"], settings)
    allow(guard, target, ["edit"], other)
    revoke(guard, target, settings)
    assert not settings.exists() and other.exists()
    assert str(target) in read(other)["sandbox"]["filesystem"]["allowWrite"]


# ---- unmanaged entries are never touched -------------------------------------------------------


def test_preexisting_entry_survives_revoke(guard: Guard, target: Path, settings: Path):
    original = canonical({"permissions": {"allow": ["Bash(mkdir *)", "Bash(mine *)"]}})
    settings.write_text(original)
    allow(guard, target, ["edit"], settings)
    revoke(guard, target, settings)
    assert settings.read_text() == original  # the user's own mkdir rule stayed, no duplicate


def test_hand_added_entries_survive(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit"], settings)
    data = read(settings)
    data["permissions"]["allow"].append("Bash(curl *)")
    data["permissions"]["deny"].append("WebFetch")
    data["customKey"] = 5
    settings.write_text(canonical(data))
    revoke(guard, target, settings)
    data = read(settings)
    assert data["permissions"]["allow"] == ["Bash(curl *)"]
    assert data["permissions"]["deny"] == ["WebFetch"]
    assert data["customKey"] == 5


def test_entry_removed_by_hand_warns_on_revoke(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit"], settings)
    data = read(settings)
    data["permissions"]["allow"].remove("Bash(cp *)")
    settings.write_text(canonical(data))
    plan = revoke(guard, target, settings)
    assert any("already removed by hand" in w for w in plan.warnings)
    assert not settings.exists()


def test_settings_file_deleted_by_hand(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit"], settings)
    settings.unlink()
    plan = revoke(guard, target, settings)
    assert plan.warnings and not settings.exists()
    assert read(guard.state_file)["grants"] == {}


# ---- scalars -----------------------------------------------------------------------------------


def test_scalar_prior_value_restored(guard: Guard, settings: Path):
    original = canonical({"sandbox": {"enabled": False, "keep": 1}})
    settings.write_text(original)
    allow(guard, None, ["lock"], settings)
    assert read(settings)["sandbox"]["enabled"] is True
    revoke(guard, None, settings)
    assert settings.read_text() == original


def test_scalar_changed_by_hand_is_left_alone(guard: Guard, settings: Path):
    allow(guard, None, ["lock"], settings)
    data = read(settings)
    data["sandbox"]["enabled"] = "mine"
    settings.write_text(canonical(data))
    plan = revoke(guard, None, settings)
    assert any("changed by hand" in w for w in plan.warnings)
    assert read(settings)["sandbox"]["enabled"] == "mine"


def test_shared_scalar_restored_only_by_last_grant(guard: Guard, tmp_path: Path, settings: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    allow(guard, a, ["edit", "lock"], settings)
    allow(guard, b, ["edit", "lock"], settings)
    revoke(guard, a, settings)
    assert read(settings)["sandbox"]["enabled"] is True
    revoke(guard, b, settings)
    assert not settings.exists()


# ---- failure safety ----------------------------------------------------------------------------


def test_malformed_settings_refused_untouched(guard: Guard, target: Path, settings: Path):
    settings.write_text("{ this is not json")
    with pytest.raises(SettingsError):
        guard.plan_allow(target, ["edit"], settings)
    assert settings.read_text() == "{ this is not json"
    assert not guard.state_file.exists()


def test_wrong_shape_refused(guard: Guard, target: Path, settings: Path):
    settings.write_text(canonical({"permissions": {"allow": "oops"}}))
    with pytest.raises(SettingsError):
        guard.plan_allow(target, ["edit"], settings)


def test_malformed_state_refused(guard: Guard, target: Path, settings: Path):
    guard.state_dir.mkdir(parents=True)
    guard.state_file.write_text("garbage")
    with pytest.raises(GuardError):
        guard.plan_allow(target, ["edit"], settings)
    assert not settings.exists()


def test_revoke_unknown_grant(guard: Guard, target: Path, settings: Path):
    with pytest.raises(GuardError, match="no grant"):
        guard.plan_revoke(target, settings)
    allow(guard, target, ["edit"], settings)
    with pytest.raises(GuardError, match="does not have profile"):
        guard.plan_revoke(target, settings, ["python"])


def test_unknown_profile(guard: Guard, target: Path, settings: Path):
    with pytest.raises(GuardError, match="unknown profile"):
        guard.plan_allow(target, ["nonsense"], settings)


def test_state_write_failure_restores_settings(
    guard: Guard, target: Path, settings: Path, monkeypatch
):
    original = canonical({"model": "x"})
    settings.write_text(original)
    plan = guard.plan_allow(target, ["edit"], settings)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("claude_guard.engine.save_state", boom)
    with pytest.raises(OSError):
        guard.commit(plan)
    assert settings.read_text() == original


def test_state_write_failure_removes_newly_created_file(
    guard: Guard, target: Path, settings: Path, monkeypatch
):
    plan = guard.plan_allow(target, ["edit"], settings)
    monkeypatch.setattr(
        "claude_guard.engine.save_state", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(OSError):
        guard.commit(plan)
    assert not settings.exists()


def test_backups_made_before_writes(guard: Guard, target: Path, settings: Path):
    original = canonical({"model": "x"})
    settings.write_text(original)
    plan = guard.plan_allow(target, ["edit"], settings)
    backups = guard.commit(plan)
    assert len(backups) == 1 and backups[0].read_text() == original
    plan = guard.plan_revoke(target, settings)
    backups = guard.commit(plan)
    assert len(backups) == 2  # the settings file and the state file
    assert all(b.parent == guard.backup_dir for b in backups)


def test_atomic_write_keeps_file_mode_and_symlink(
    guard: Guard, target: Path, settings: Path, tmp_path: Path
):
    real = tmp_path / "real.json"
    real.write_text("{}\n")
    real.chmod(0o640)
    settings.symlink_to(real)
    allow(guard, target, ["edit"], settings)
    assert settings.is_symlink()  # link kept, content written to its target
    assert "permissions" in read(real)
    assert (real.stat().st_mode & 0o777) == 0o640


# ---- state file --------------------------------------------------------------------------------


def test_state_records_grant(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit"], settings, protect=["a/**"])
    state = read(guard.state_file)
    assert state["version"] == 1
    ((gid, grant),) = state["grants"].items()
    assert gid == f"{target}|{settings}"
    assert grant["folder"] == str(target) and grant["protect"] == ["a/**"]
    frec = state["files"][str(settings)]
    assert frec["file_created"] is True
    for file, key in grant["entries"]:
        assert gid in frec["entries"][key]["refs"]
