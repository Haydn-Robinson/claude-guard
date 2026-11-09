"""New profile primitives: edit_ask, sandbox_deny_write, global edit_deny, and the profiles built
from them (bash-readonly, edit-ask, confirm-destructive, floor). Behaviour verified in Phase 0b."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_guard.engine import Guard
from claude_guard.errors import GuardError, ProfileError
from claude_guard.profiles import load_profiles


def allow(g: Guard, folder, profiles, settings: Path, **kw):
    plan = g.plan_allow(folder, profiles, settings, **kw)
    g.commit(plan)
    return plan


def read(p: Path) -> dict:
    return json.loads(p.read_text())


def rp(p: Path) -> str:
    return "/" + str(p)


# ---- profiles ----------------------------------------------------------------------------------


def test_new_profiles_exist_and_old_delete_profiles_are_gone():
    ps = load_profiles()
    for name in ("bash-readonly", "edit-ask", "confirm-destructive", "floor"):
        ps.resolve(name)
    assert "delete" not in ps.names and "delete-recursive" not in ps.names


def test_confirm_destructive_only_asks():
    p = load_profiles().resolve("confirm-destructive")
    assert not p.bash_allow and not p.bash_deny
    assert "Bash(rm *)" in p.bash_ask and "Bash(rmdir *)" in p.bash_ask


def test_floor_is_global_and_denies_only():
    p = load_profiles().resolve("floor")
    assert not p.needs_folder
    assert not p.bash_allow and p.bash_deny and p.edit_deny
    assert "Bash(sudo *)" in p.bash_deny
    assert "~/.bashrc" in p.edit_deny and "~/.ssh/**" in p.edit_deny


def test_git_destructive_uses_star_form_only():
    # Phase 0b T4: "Bash(cmd *)" also matches the bare command, so the duplicates are redundant.
    p = load_profiles().resolve("git-destructive")
    assert p.bash_ask and all(r.endswith(" *)") for r in p.bash_ask)


def test_edit_ask_extends_bash_readonly():
    p = load_profiles().resolve("edit-ask")
    assert p.edit_ask == ("{folder}/**",) and p.sandbox_deny_write
    assert not p.edit_paths and not p.sandbox_write and not p.bash_allow
    assert p.warning


def test_extends_merges_new_fields(tmp_path: Path):
    f = tmp_path / "p.toml"
    f.write_text(
        '[mine]\nextends = ["edit-ask", "floor"]\nedit_deny = ["~/.npmrc"]\n'
        'edit_ask = ["{folder}/notes/**"]\n'
    )
    p = load_profiles(f).resolve("mine")
    assert p.edit_ask == ("{folder}/**", "{folder}/notes/**")
    assert "~/.npmrc" in p.edit_deny and "~/.zshrc" in p.edit_deny
    assert p.sandbox_deny_write


@pytest.mark.parametrize(
    "toml",
    [
        '[a]\nedit_ask=["/etc/**"]\n',
        "[a]\nsandbox_deny_write = 1\n",
        '[a]\nedit_deny=[""]\n',
        "[a]\nedit_deny=[1]\n",
    ],
)
def test_invalid_new_fields_rejected(tmp_path: Path, toml: str):
    f = tmp_path / "p.toml"
    f.write_text(toml)
    with pytest.raises(ProfileError):
        load_profiles(f)


# ---- generated settings ------------------------------------------------------------------------


def test_bash_readonly_denies_sandbox_writes_only(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["bash-readonly"], settings)
    data = read(settings)
    assert data["sandbox"]["filesystem"]["denyWrite"] == [str(target)]
    assert "allowWrite" not in data["sandbox"]["filesystem"]
    assert not any(r.startswith("Edit(") for r in data["permissions"].get("allow", []))
    assert not any(r.startswith("Edit(") for r in data["permissions"].get("ask", []))


def test_edit_ask_generates_ask_rule_and_deny_write(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit-ask"], settings, protect=[".private/**", "archive/**"])
    data = read(settings)
    perms = data["permissions"]
    assert perms["ask"] == [f"Edit({rp(target)}/**)"]
    assert data["sandbox"]["filesystem"]["denyWrite"] == [str(target)]
    assert f"Edit({rp(target)}/.private/**)" in perms["deny"]
    assert f"Edit({rp(target)}/archive/**)" in perms["deny"]
    assert "allow" not in perms  # nothing auto-approved, nothing that gives Bash write access
    assert "allowWrite" not in data["sandbox"]["filesystem"]


def test_floor_generates_global_rules(guard: Guard, settings: Path):
    allow(guard, None, ["floor"], settings)
    deny = read(settings)["permissions"]["deny"]
    assert "Bash(sudo *)" in deny
    assert "Edit(~/.bashrc)" in deny and "Edit(~/.ssh/**)" in deny and "Edit(//etc/**)" in deny
    assert not any(r.startswith("Read(") for r in deny)


def test_confirm_destructive_asks_for_rm(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["confirm-destructive"], settings)
    perms = read(settings)["permissions"]
    assert "Bash(rm *)" in perms["ask"]
    assert not perms.get("allow")


# ---- undo --------------------------------------------------------------------------------------


def test_revoke_edit_ask_restores_byte_identical(guard: Guard, target: Path, settings: Path):
    original = json.dumps({"model": "x", "permissions": {"allow": ["Bash(ls)"]}}, indent=2) + "\n"
    settings.write_text(original)
    allow(guard, target, ["edit-ask"], settings, protect=["a/**"])
    assert settings.read_text() != original
    guard.commit(guard.plan_revoke(target, settings))
    assert settings.read_text() == original


def test_revoke_deletes_file_the_tool_created(guard: Guard, target: Path, settings: Path):
    assert not settings.exists()
    allow(guard, target, ["edit-ask"], settings)
    guard.commit(guard.plan_revoke(target, settings))
    assert not settings.exists()


def test_deny_write_is_refcounted_across_profiles(guard: Guard, target: Path, settings: Path):
    allow(guard, target, ["edit-ask", "bash-readonly"], settings)
    guard.commit(guard.plan_revoke(target, settings, ["bash-readonly"]))
    # edit-ask still needs the denyWrite entry, so it must survive
    assert read(settings)["sandbox"]["filesystem"]["denyWrite"] == [str(target)]
    guard.commit(guard.plan_revoke(target, settings, ["edit-ask"]))
    assert not settings.exists()


# ---- warnings ----------------------------------------------------------------------------------


def test_warns_when_readonly_overrides_write_grant(guard: Guard, target: Path, settings: Path):
    plan = guard.plan_allow(target, ["edit", "bash-readonly"], settings)
    assert any("overrides" in w and "edit" in w for w in plan.warnings)


def test_warns_about_git_in_readonly_folder(guard: Guard, target: Path, settings: Path):
    plan = guard.plan_allow(target, ["bash-readonly", "git-safe"], settings)
    assert any("git" in w and "read-only" in w for w in plan.warnings)


def test_no_conflict_warning_for_compatible_profiles(guard: Guard, target: Path, settings: Path):
    plan = guard.plan_allow(target, ["edit", "git-safe"], settings)
    assert not any("overrides" in w for w in plan.warnings)


def test_edit_ask_shows_profile_warning(guard: Guard, target: Path, settings: Path):
    plan = guard.plan_allow(target, ["edit-ask"], settings)
    assert any("edit-ask" in w for w in plan.warnings)


def test_edit_ask_needs_folder(guard: Guard, settings: Path):
    with pytest.raises(GuardError, match="needs a folder"):
        guard.plan_allow(None, ["edit-ask"], settings)
