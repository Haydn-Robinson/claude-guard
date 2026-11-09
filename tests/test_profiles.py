from __future__ import annotations

from pathlib import Path

import pytest

from claude_guard.errors import ProfileError
from claude_guard.profiles import load_profiles


def test_defaults_load_and_all_resolve():
    ps = load_profiles()
    for name in (
        "edit",
        "bash-readonly",
        "edit-ask",
        "confirm-destructive",
        "floor",
        "git-safe",
        "git-destructive",
        "python",
        "secrets",
        "lock",
    ):
        assert name in ps.names
        ps.resolve(name)


def test_git_destructive_only_asks():
    p = load_profiles().resolve("git-destructive")
    assert p.bash_ask and not p.bash_allow and not p.bash_deny


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_extends_merges_parent_first_dedup(tmp_path: Path):
    f = write(
        tmp_path / "p.toml",
        """
[target]
extends = ["edit", "git-safe"]
bash_allow = ["Bash(mkdir *)", "Bash(extra *)"]
protect = [".private/**"]
""",
    )
    p = load_profiles(f).resolve("target")
    assert p.edit_paths == ("{folder}/**",)
    assert p.sandbox_write
    assert p.bash_allow.count("Bash(mkdir *)") == 1
    assert "Bash(git status *)" in p.bash_allow and "Bash(extra *)" in p.bash_allow
    assert p.protect == (".private/**",)


def test_user_profile_replaces_default(tmp_path: Path):
    f = write(tmp_path / "p.toml", '[python]\nbash_allow = ["Bash(poetry run *)"]\n')
    assert load_profiles(f).resolve("python").bash_allow == ("Bash(poetry run *)",)


def test_cycle_rejected(tmp_path: Path):
    f = write(tmp_path / "p.toml", '[a]\nextends=["b"]\n[b]\nextends=["a"]\n')
    with pytest.raises(ProfileError, match="cycle"):
        load_profiles(f)


@pytest.mark.parametrize(
    "toml",
    [
        '[a]\nextends=["nope"]\n',
        "[a]\nunknown_key = 1\n",
        '[a]\nedit_paths=["/etc/**"]\n',
        '[a]\nbash_allow=["rm *"]\n',
        '[a]\nprotect=["../x"]\n',
        '[a]\nprotect=["/abs"]\n',
        "[a]\nsandbox_write = 1\n",
        "[a]\nsettings = [{path = 'x'}]\n",
        "not toml [[[",
    ],
)
def test_invalid_profiles_rejected(tmp_path: Path, toml: str):
    with pytest.raises(ProfileError):
        load_profiles(write(tmp_path / "p.toml", toml))


def test_lock_scalars():
    p = load_profiles().resolve("lock")
    assert dict(p.settings)["permissions.disableBypassPermissionsMode"] == "disable"
    assert dict(p.settings)["sandbox.allowUnsandboxedCommands"] is False
