from __future__ import annotations

from pathlib import Path

import pytest

from claude_guard.errors import UnsafeFolderError
from claude_guard.paths import PathPolicy, detect_drive_roots, rule_path, sandbox_path


def test_path_forms_with_spaces():
    p = Path("/mnt/d/My Projects/Sample Project")
    assert rule_path(p) == "//mnt/d/My Projects/Sample Project"
    assert sandbox_path(p) == "/mnt/d/My Projects/Sample Project"


@pytest.mark.parametrize(
    "raw",
    [
        "/",
        "/mnt",
        "/mnt/d",
        "/mnt/d/",
        "/mnt/e",
        "/mnt/c",
        "/mnt/c/Windows",
        "/mnt/c/windows/System32",  # case-insensitive, and below counts
        "/mnt/c/Program Files",
        "/mnt/c/Program Files (x86)/Foo",
        "/mnt/c/ProgramData",
        "/mnt/c/Users",
        "/mnt/c/Users/haydn",
        "/mnt/c/users/HAYDN",
    ],
)
def test_hard_refusals_not_overridable(policy: PathPolicy, raw: str):
    with pytest.raises(UnsafeFolderError) as ei:
        policy.check_folder(raw, force_broad=True)
    assert not ei.value.overridable


def test_home_and_parents_refused(policy: PathPolicy, home: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(UnsafeFolderError):
        policy.check_folder(home, force_broad=True)
    with pytest.raises(UnsafeFolderError):
        policy.check_folder(home.parent, force_broad=True)
    with pytest.raises(UnsafeFolderError):
        policy.check_folder("~", force_broad=True)


def test_subfolders_allowed(policy: PathPolicy, home: Path):
    assert policy.check_folder("/mnt/d/My Projects/Sample Project") == Path(
        "/mnt/d/My Projects/Sample Project"
    )
    assert policy.check_folder("/mnt/c/Users/haydn/claude-guard-test") == Path(
        "/mnt/c/Users/haydn/claude-guard-test"
    )
    assert policy.check_folder(home / "proj") == home / "proj"


def test_depth_rule_overridable(policy: PathPolicy):
    with pytest.raises(UnsafeFolderError) as ei:
        policy.check_folder("/opt")
    assert ei.value.overridable
    assert policy.check_folder("/opt", force_broad=True) == Path("/opt")


@pytest.mark.parametrize("name", ["a*b", "a?b", "a[b]", "a\\b", "a\nb"])
def test_pattern_characters_refused(policy: PathPolicy, home: Path, name: str):
    with pytest.raises(UnsafeFolderError):
        policy.check_folder(home / name)


def test_symlink_resolved_before_checking(policy: PathPolicy, tmp_path: Path, home: Path):
    link = tmp_path / "innocent"
    link.symlink_to(home)
    with pytest.raises(UnsafeFolderError):
        policy.check_folder(link, force_broad=True)


def test_relative_resolved_against_cwd(policy: PathPolicy, tmp_path: Path, monkeypatch):
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path)
    assert policy.check_folder("work") == (tmp_path / "work").resolve()


def test_detect_drive_roots(tmp_path: Path):
    info = tmp_path / "mountinfo"
    info.write_text(
        "30 1 0:27 / /mnt/d rw - 9p D: rw\n"
        "31 1 0:28 / /mnt/c rw - 9p C: rw\n"
        "32 1 0:29 / /mnt/wsl rw - tmpfs tmpfs rw\n"
        "33 1 0:30 / /usr/lib/wsl/drivers ro - 9p drivers ro\n"
    )
    roots = detect_drive_roots(info, mnt=tmp_path / "nonexistent")
    assert roots == {Path("/mnt/d"), Path("/mnt/c")}


def test_unlisted_drive_not_invented(policy: PathPolicy):
    # A drive letter that is not in drive_roots is not specially protected (only depth rule applies)
    assert policy.check_folder("/mnt/q/stuff") == Path("/mnt/q/stuff")
