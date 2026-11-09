"""plan / commit: reconcile compiled entries with the settings files and applied.json (section 10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_guard import applied as applied_mod
from claude_guard.applied import Applied, load_applied, lock
from claude_guard.apply import commit, plan
from claude_guard.errors import GuardError, SettingsError
from claude_guard.model import Compiled, Entry

ALLOW, DENY = "permissions.allow", "permissions.deny"


@pytest.fixture
def gh(tmp_path: Path) -> Path:
    return tmp_path / "guard"


@pytest.fixture
def user(tmp_path: Path) -> Path:
    p = tmp_path / "home" / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    return p


def canonical(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def read(p: Path) -> dict:
    return json.loads(p.read_text())


def compiled(files: dict[Path, list[Entry]], hook: dict | None = None) -> Compiled:
    return Compiled(files=files, hook=hook or {})


def apply(c: Compiled, gh: Path):
    p = plan(c, load_applied(gh))
    commit(p, gh)
    return p


A = Entry("array", ALLOW, "Edit(//srv/a/**)")
B = Entry("array", DENY, "Bash(sudo *)")
S = Entry("scalar", "sandbox.enabled", True)
H = Entry("array", "hooks.PreToolUse", {"matcher": "Write", "hooks": [{"type": "command"}]})


# ---- basic apply ----------------------------------------------------------------------------------


def test_fresh_apply_creates_file_and_records_ownership(gh: Path, user: Path):
    apply(compiled({user: [A, B, S, H]}, {"scopes": {}}), gh)
    assert read(user) == {
        "permissions": {"allow": [A.value], "deny": [B.value]},
        "sandbox": {"enabled": True},
        "hooks": {"PreToolUse": [H.value]},
    }
    rec = load_applied(gh)
    assert rec.hook == {"scopes": {}}
    f = rec.files[str(user)]
    assert f.file_created is True
    assert [(o.entry, o.preexisting) for o in f.entries if o.entry.kind == "array"] == [
        (A, False),
        (B, False),
        (H, False),
    ]


def test_apply_twice_is_a_noop(gh: Path, user: Path):
    c = compiled({user: [A, B, S, H]}, {"x": 1})
    apply(c, gh)
    before = user.read_text()
    p = plan(c, load_applied(gh))
    assert p.noop and p.changes == [] and p.diff() == ""
    commit(p, gh)
    assert user.read_text() == before


def test_hook_data_change_alone_is_not_a_noop(gh: Path, user: Path):
    apply(compiled({user: [A]}, {"x": 1}), gh)
    p = plan(compiled({user: [A]}, {"x": 2}), load_applied(gh))
    assert not p.noop and p.changes == []
    commit(p, gh)
    assert load_applied(gh).hook == {"x": 2}


def test_unknown_keys_and_order_preserved(gh: Path, user: Path):
    original = {"model": "opus", "permissions": {"deny": ["Mine"], "defaultMode": "auto"}, "z": [1]}
    user.write_text(canonical(original))
    apply(compiled({user: [A, B]}), gh)
    data = read(user)
    assert list(data) == ["model", "permissions", "z"]
    assert data["permissions"] == {
        "deny": ["Mine", B.value],
        "defaultMode": "auto",
        "allow": [A.value],
    }


# ---- uninstall restores files exactly -------------------------------------------------------------


def test_apply_then_uninstall_restores_bytes(gh: Path, user: Path, tmp_path: Path):
    original = canonical(
        {
            "model": "opus",
            "permissions": {"deny": [B.value], "allow": []},
            "sandbox": {"enabled": False},
        }
    )
    user.write_text(original)
    local = tmp_path / "proj" / ".claude" / "settings.local.json"  # does not exist yet
    apply(compiled({user: [A, B, S, H], local: [A]}), gh)
    assert read(user)["sandbox"]["enabled"] is True and local.exists()

    apply(compiled({}), gh)
    assert user.read_text() == original  # pre-existing B kept, prior scalar restored
    assert not local.exists()  # guard created it, guard deletes it
    assert local.parent.exists()  # but never the folder
    assert load_applied(gh).files == {}


def test_file_guard_did_not_create_is_kept_when_emptied(gh: Path, user: Path):
    user.write_text("{}\n")
    apply(compiled({user: [A]}), gh)
    apply(compiled({}), gh)
    assert user.read_text() == "{}\n"


def test_preexisting_entry_never_removed(gh: Path, user: Path):
    user.write_text(canonical({"permissions": {"allow": [A.value]}}))
    apply(compiled({user: [A]}), gh)
    assert load_applied(gh).files[str(user)].entries[0].preexisting is True
    apply(compiled({}), gh)
    assert read(user) == {"permissions": {"allow": [A.value]}}


def test_scalar_prior_absent_is_deleted_on_uninstall(gh: Path, user: Path):
    user.write_text(canonical({"sandbox": {"network": {}}}))
    apply(compiled({user: [S]}), gh)
    apply(compiled({}), gh)
    assert read(user) == {"sandbox": {"network": {}}}


def test_entry_no_longer_desired_is_removed(gh: Path, user: Path):
    apply(compiled({user: [A, B]}), gh)
    p = plan(compiled({user: [A]}), load_applied(gh))
    assert "-" in p.diff() and B.value in p.diff()
    commit(p, gh)
    assert read(user) == {"permissions": {"allow": [A.value]}}  # emptied deny pruned


def test_file_no_longer_targeted_is_cleaned(gh: Path, user: Path, tmp_path: Path):
    local = tmp_path / "proj" / ".claude" / "settings.local.json"
    local.parent.mkdir(parents=True)
    apply(compiled({user: [A], local: [B]}), gh)
    apply(compiled({user: [A]}), gh)
    assert not local.exists()
    assert set(load_applied(gh).files) == {str(user)}


# ---- drift: hand edits are detected and repaired ----------------------------------------------------


def test_hand_removed_entry_is_restored_with_a_note(gh: Path, user: Path):
    apply(compiled({user: [A, B]}), gh)
    data = read(user)
    data["permissions"]["allow"].remove(A.value)
    user.write_text(canonical(data))
    p = plan(compiled({user: [A, B]}), load_applied(gh))
    assert any("removed by hand" in n and A.value in n for n in p.notes)
    commit(p, gh)
    assert A.value in read(user)["permissions"]["allow"]


def test_hand_changed_scalar_is_restored_with_a_note(gh: Path, user: Path):
    apply(compiled({user: [S]}), gh)
    user.write_text(canonical({"sandbox": {"enabled": False}}))
    p = plan(compiled({user: [S]}), load_applied(gh))
    assert any("changed by hand" in n and "sandbox.enabled" in n for n in p.notes)
    commit(p, gh)
    assert read(user)["sandbox"]["enabled"] is True


def test_policy_scalar_change_keeps_the_original_prior(gh: Path, user: Path):
    user.write_text(canonical({"model": "haiku"}))
    apply(compiled({user: [Entry("scalar", "model", "opus")]}), gh)
    apply(compiled({user: [Entry("scalar", "model", "sonnet")]}), gh)
    assert read(user) == {"model": "sonnet"}
    apply(compiled({}), gh)
    assert read(user) == {"model": "haiku"}


def test_removed_entry_already_gone_by_hand_is_a_note_not_an_error(gh: Path, user: Path):
    apply(compiled({user: [A, B]}), gh)
    user.write_text(canonical({"permissions": {"allow": [A.value]}}))
    p = plan(compiled({user: [A]}), load_applied(gh))
    assert any("already removed" in n for n in p.notes)
    commit(p, gh)
    assert read(user) == {"permissions": {"allow": [A.value]}}


def test_hand_changed_scalar_no_longer_desired_is_left_alone(gh: Path, user: Path):
    apply(compiled({user: [S]}), gh)
    user.write_text(canonical({"sandbox": {"enabled": "custom"}}))
    p = plan(compiled({}), load_applied(gh))
    assert any("changed by hand" in n for n in p.notes)
    commit(p, gh)
    assert read(user) == {"sandbox": {"enabled": "custom"}}


def test_noncanonical_file_untouched_when_nothing_changes(gh: Path, user: Path):
    user.write_text('{"permissions": {"allow": ["Edit(//srv/a/**)"]}}')  # one line
    apply(
        compiled({user: [A]}), gh
    )  # already present: recorded as pre-existing, file not rewritten
    assert user.read_text() == '{"permissions": {"allow": ["Edit(//srv/a/**)"]}}'


# ---- commit safety ------------------------------------------------------------------------------


def test_invalid_settings_json_refused(gh: Path, user: Path):
    user.write_text("{not json")
    with pytest.raises(SettingsError):
        plan(compiled({user: [A]}), load_applied(gh))
    assert user.read_text() == "{not json"


def test_backups_made_for_changed_files(gh: Path, user: Path):
    user.write_text(canonical({"model": "opus"}))
    p = plan(compiled({user: [A]}), load_applied(gh))
    backups = commit(p, gh)
    assert len(backups) == 1
    assert backups[0].parent == gh / "backups"
    assert backups[0].read_text() == canonical({"model": "opus"})


def test_failed_applied_write_rolls_back_settings(
    gh: Path, user: Path, tmp_path: Path, monkeypatch
):
    original = canonical({"model": "opus"})
    user.write_text(original)
    local = tmp_path / "proj" / ".claude" / "settings.local.json"
    p = plan(compiled({user: [A], local: [B]}), load_applied(gh))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(applied_mod, "save_applied", boom)
    with pytest.raises(OSError):
        commit(p, gh)
    assert user.read_text() == original
    assert not local.exists()
    assert load_applied(gh).files == {}


def test_stale_plan_refused(gh: Path, user: Path):
    user.write_text(canonical({"model": "opus"}))
    p = plan(compiled({user: [A]}), load_applied(gh))
    user.write_text(canonical({"model": "sonnet"}))
    with pytest.raises(GuardError, match="changed"):
        commit(p, gh)
    assert read(user) == {"model": "sonnet"}


def test_concurrent_apply_refused(gh: Path, user: Path):
    p = plan(compiled({user: [A]}), load_applied(gh))
    with lock(gh), pytest.raises(GuardError, match="another"):
        commit(p, gh)
    assert not user.exists()


def test_applied_json_rejects_unknown_versions(gh: Path):
    gh.mkdir()
    (gh / "applied.json").write_text('{"version": 99}')
    with pytest.raises(GuardError, match="applied.json"):
        load_applied(gh)


def test_applied_roundtrip(gh: Path, user: Path):
    apply(compiled({user: [A, S]}, {"k": [1]}), gh)
    a = load_applied(gh)
    assert isinstance(a, Applied)
    assert a == load_applied(gh)
    scal = next(o for o in a.files[str(user)].entries if o.entry.kind == "scalar")
    assert scal.prior == (False, None)


def test_diff_names_files_and_new_files(gh: Path, user: Path):
    p = plan(compiled({user: [A]}), load_applied(gh))
    d = p.diff()
    assert "/dev/null" in d and str(user) in d and f'+      "{A.value}"' in d
