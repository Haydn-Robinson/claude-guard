from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_guard.errors import SettingsError
from claude_guard.settingsfile import SettingsDoc


def doc_from(tmp_path: Path, text: str | None) -> SettingsDoc:
    p = tmp_path / "s.json"
    if text is not None:
        p.write_text(text)
    return SettingsDoc.load(p)


def test_missing_file_is_empty(tmp_path: Path):
    d = doc_from(tmp_path, None)
    assert not d.exists and d.data == {}


@pytest.mark.parametrize("indent", [2, 4, "\t"])
def test_canonical_roundtrip_is_byte_identical(tmp_path: Path, indent):
    text = (
        json.dumps({"model": "x", "permissions": {"allow": ["A"]}, "z": [1, 2]}, indent=indent)
        + "\n"
    )
    d = doc_from(tmp_path, text)
    assert d.render() == text
    assert not d.reformats


def test_no_trailing_newline_preserved(tmp_path: Path):
    text = json.dumps({"a": 1}, indent=2)
    assert doc_from(tmp_path, text).render() == text


def test_noncanonical_flagged_as_reformatting(tmp_path: Path):
    d = doc_from(tmp_path, '{"a":   [1,2]}\n')
    assert d.reformats


@pytest.mark.parametrize("text", ["{not json", "[]", "5", '"s"'])
def test_bad_files_refused(tmp_path: Path, text: str):
    with pytest.raises(SettingsError):
        doc_from(tmp_path, text)


def test_unknown_keys_and_order_preserved(tmp_path: Path):
    d = doc_from(
        tmp_path, '{\n  "zeta": 1,\n  "alpha": {"keep": true},\n  "permissions": {"x": 1}\n}\n'
    )
    d.add_to_array("permissions.allow", "Bash(ls *)")
    out = json.loads(d.render())
    assert list(out) == ["zeta", "alpha", "permissions"]
    assert out["alpha"] == {"keep": True} and out["permissions"]["x"] == 1


def test_wrong_types_not_clobbered(tmp_path: Path):
    d = doc_from(tmp_path, '{"permissions": []}')
    with pytest.raises(SettingsError):
        d.add_to_array("permissions.allow", "x")
    d2 = doc_from(tmp_path, '{"permissions": {"allow": "str"}}')
    with pytest.raises(SettingsError):
        d2.add_to_array("permissions.allow", "x")


def test_add_reports_created_containers_and_dedups(tmp_path: Path):
    d = doc_from(tmp_path, "{}")
    assert d.add_to_array("sandbox.filesystem.allowWrite", "/a") == [
        "sandbox",
        "sandbox.filesystem",
        "sandbox.filesystem.allowWrite",
    ]
    assert d.add_to_array("sandbox.filesystem.allowWrite", "/a") == []
    assert d.data["sandbox"]["filesystem"]["allowWrite"] == ["/a"]


def test_prune_only_empty_created(tmp_path: Path):
    d = doc_from(tmp_path, "{}")
    created = set(d.add_to_array("permissions.allow", "A"))
    assert d.prune(created) == set()  # not empty
    d.remove_from_array("permissions.allow", "A")
    assert d.prune(created) == {"permissions", "permissions.allow"}
    assert d.data == {}


def test_prune_leaves_user_content(tmp_path: Path):
    d = doc_from(tmp_path, "{}")
    created = set(d.add_to_array("permissions.allow", "A"))
    d.data["permissions"]["deny"] = ["mine"]
    d.remove_from_array("permissions.allow", "A")
    d.prune(created)
    assert d.data == {"permissions": {"deny": ["mine"]}}


def test_scalars(tmp_path: Path):
    d = doc_from(tmp_path, "{}")
    assert d.get_scalar("sandbox.enabled") == (False, None)
    d.set_scalar("sandbox.enabled", True)
    assert d.get_scalar("sandbox.enabled") == (True, True)
    d.delete_scalar("sandbox.enabled")
    assert d.get_scalar("sandbox.enabled") == (False, None)
