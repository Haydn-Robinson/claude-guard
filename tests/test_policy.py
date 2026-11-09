"""policy.toml: parsing, validation, preset expansion, path resolution and format-preserving edits."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_guard.errors import PolicyError
from claude_guard.model import Access, Closed, Platform
from claude_guard.policy import PolicyFile, parse_policy, resolve, shipped_presets


def plat(home: Path, **kw) -> Platform:
    return Platform(
        home=home, guard_home=home / ".config" / "claude-guard", hook_command=("/g",), **kw
    )


# ---- parsing ---------------------------------------------------------------------------------


def test_minimal_policy():
    p = parse_policy('version = 1\n[everywhere]\npresets = ["baseline"]\n')
    assert p.everywhere.presets == ("baseline",)
    assert p.projects == {}


def test_full_schema_parses():
    p = parse_policy(
        """
version = 1
[everywhere]
write = [{ path = "~/notes", access = "ask" }, { path = "/srv/x", access = "full", protect = ["*.lock"] }]
protected = [{ path = "/opt/app", ceiling = "edit" }]
locked = ["~/.config/thing.json"]
read.deny = ["**/.secret"]
commands.allow = ["make *"]
commands.ask = ["git push --force *"]
commands.deny = ["sudo *"]
raw.allow = ["WebFetch(domain:example.com)"]
raw.sandbox.allow_write = ["/srv/y"]
raw.settings = [{ path = "model", value = "opus" }]
closed = "deny"

[projects."~/repos/a"]
write = [{ path = "/mnt/d/data", access = "edit" }]

[presets.notes]
write = [{ path = "~/notes", access = "full" }]
"""
    )
    e = p.everywhere
    assert [(g.path, g.access, g.protect) for g in e.write] == [
        ("~/notes", Access.ASK, ()),
        ("/srv/x", Access.FULL, ("*.lock",)),
    ]
    assert e.protected[0].ceiling is Access.EDIT
    assert e.locked == ("~/.config/thing.json",)
    assert e.read_deny == ("**/.secret",)
    assert e.commands.deny == ("sudo *",)
    assert e.raw.allow == ("WebFetch(domain:example.com)",)
    assert e.raw.allow_write == ("/srv/y",)
    assert e.raw.settings == (("model", "opus"),)
    assert e.closed is Closed.DENY
    assert p.projects["~/repos/a"].write[0].access is Access.EDIT
    assert p.presets["notes"].write[0].access is Access.FULL


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("[everywhere]\n", "version"),
        ("version = 2\n", "version"),
        ("version = 1\n[everywhere]\nwrit = []\n", "unknown key 'writ'"),
        ('version = 1\n[everywhere]\nwrite = [{ path = "/a/b", access = "rw" }]\n', "access level"),
        ('version = 1\n[everywhere]\nwrite = [{ path = "/a/b" }]\n', "access"),
        ('version = 1\n[everywhere]\nwrite = [{ path = "/a/b", access = "full", x = 1 }]\n', "'x'"),
        (
            'version = 1\n[everywhere]\nwrite = [{ path = "rel/dir", access = "full" }]\n',
            "absolute",
        ),
        ('version = 1\n[everywhere]\nwrite = [{ path = "/a/*", access = "full" }]\n', "glob"),
        (
            'version = 1\n[everywhere]\nwrite = [{ path = "/a/b", access = "full", protect = ["/abs"] }]\n',
            "relative",
        ),
        (
            'version = 1\n[everywhere]\nwrite = [{ path = "/a/b", access = "full", protect = ["../x"] }]\n',
            "relative",
        ),
        (
            'version = 1\n[everywhere]\nprotected = [{ path = "/a/b", ceiling = "nope" }]\n',
            "access level",
        ),
        ('version = 1\n[everywhere]\nread.deny = ["relative"]\n', "absolute"),
        ('version = 1\n[everywhere]\nlocked = ["x/**"]\n', "absolute"),
        ('version = 1\n[everywhere]\nclosed = "maybe"\n', "closed"),
        ('version = 1\n[everywhere]\nraw.settings = [{ path = "", value = 1 }]\n', "path"),
        ('version = 1\n[everywhere]\nraw.sandbox.allow_write = ["rel"]\n', "absolute"),
        ("version = 1\n[everywhere]\ncommands.ask = [3]\n", "string"),
        ('version = 1\n[projects."rel/dir"]\n', "absolute"),
        ("version = 1\n[[everywhere]]\n", "table"),
        ("version = 1\nnot toml at all [", "TOML"),
    ],
)
def test_invalid_policies_name_the_problem(text, match):
    with pytest.raises(PolicyError, match=match):
        parse_policy(text)


def test_error_names_the_location():
    with pytest.raises(PolicyError, match=r'projects\."/p/q"\.write\[1\]'):
        parse_policy(
            'version = 1\n[projects."/p/q"]\n'
            'write = [{ path = "/a/b", access = "full" }, { path = "/c/d", access = "bad" }]\n'
        )


# ---- shipped presets -------------------------------------------------------------------------


def test_shipped_presets_parse_and_include_the_documented_ones():
    presets = shipped_presets()
    assert {"baseline", "confirm-destructive", "git-destructive", "strict"} <= set(presets)
    assert presets["baseline"].closed is Closed.ASK
    assert presets["strict"].closed is Closed.DENY


# ---- resolution ------------------------------------------------------------------------------


def test_tilde_and_symlinks_resolve(tmp_path: Path):
    home = tmp_path / "home"
    (home / "real").mkdir(parents=True)
    (home / "link").symlink_to(home / "real")
    p = parse_policy('version = 1\n[everywhere]\nwrite = [{ path = "~/link", access = "full" }]\n')
    r = resolve(p, plat(home))
    assert [g.path for g in r.everywhere.grants] == [home / "real"]


def test_project_keys_resolve(tmp_path: Path):
    home = tmp_path / "home"
    (home / "repo").mkdir(parents=True)
    p = parse_policy('version = 1\n[projects."~/repo"]\npresets = ["confirm-destructive"]\n')
    r = resolve(p, plat(home))
    assert list(r.projects) == [home / "repo"]
    assert "rm *" in r.projects[home / "repo"].commands.ask


def test_presets_expand_recursively_in_order(tmp_path: Path):
    p = parse_policy(
        """
version = 1
[everywhere]
presets = ["a"]
commands.ask = ["own *"]
[presets.a]
presets = ["b"]
commands.ask = ["a *"]
[presets.b]
commands.ask = ["b *", "a *"]
"""
    )
    r = resolve(p, plat(tmp_path))
    assert r.everywhere.commands.ask == ("b *", "a *", "own *")


def test_preset_cycle_and_unknown_preset_rejected(tmp_path: Path):
    cyc = parse_policy(
        'version = 1\n[everywhere]\npresets = ["a"]\n[presets.a]\npresets = ["b"]\n[presets.b]\npresets = ["a"]\n'
    )
    with pytest.raises(PolicyError, match="cycle"):
        resolve(cyc, plat(tmp_path))
    unk = parse_policy('version = 1\n[everywhere]\npresets = ["nope"]\n')
    with pytest.raises(PolicyError, match="unknown preset 'nope'"):
        resolve(unk, plat(tmp_path))


def test_user_preset_replaces_shipped_one(tmp_path: Path):
    p = parse_policy(
        'version = 1\n[everywhere]\npresets = ["confirm-destructive"]\n'
        '[presets.confirm-destructive]\ncommands.ask = ["mine *"]\n'
    )
    assert resolve(p, plat(tmp_path)).everywhere.commands.ask == ("mine *",)


def test_placeholders_expand_per_platform(tmp_path: Path):
    p = parse_policy(
        """
version = 1
[everywhere]
write = [{ path = "{caches}", access = "bash" }]
protected = [
  { path = "{system_drive}/Windows", ceiling = "deny" },
  { path = "{drives}", ceiling = "edit" },
  { path = "{settings_scripts}", ceiling = "ask" },
]
locked = ["{guard_home}", "{managed_settings}"]
"""
    )
    home = tmp_path / "home"
    wsl = plat(
        home,
        system_drive=Path("/mnt/c"),
        drives=(Path("/mnt/c"), Path("/mnt/d")),
        caches=(home / ".cache" / "uv",),
        settings_scripts=(home / "bin" / "status.sh",),
    )
    r = resolve(p, wsl).everywhere
    assert [g.path for g in r.grants] == [home / ".cache" / "uv"]
    assert [(x.path, x.ceiling) for x in r.protected] == [
        (Path("/mnt/c/Windows"), Access.DENY),
        (Path("/mnt/c"), Access.EDIT),
        (Path("/mnt/d"), Access.EDIT),
        (home / "bin" / "status.sh", Access.ASK),
    ]
    assert r.locked == (str(home / ".config" / "claude-guard"), "/etc/claude-code")

    linux = resolve(p, plat(home)).everywhere  # not WSL, no caches: placeholders vanish
    assert linux.grants == ()
    assert linux.protected == ()


def test_trailing_slash_kept_on_patterns(tmp_path: Path):
    p = parse_policy('version = 1\n[everywhere]\nread.deny = ["~/.kube/", "~/.netrc"]\n')
    r = resolve(p, plat(tmp_path / "home")).everywhere
    assert r.read_deny == (
        str(tmp_path / "home" / ".kube") + "/",
        str(tmp_path / "home" / ".netrc"),
    )


def test_unknown_placeholder_rejected():
    with pytest.raises(PolicyError, match="placeholder"):
        parse_policy(
            'version = 1\n[everywhere]\nwrite = [{ path = "{nope}/x", access = "full" }]\n'
        )


def test_protected_ceiling_own_entry_replaces_preset(tmp_path: Path):
    p = parse_policy(
        """
version = 1
[everywhere]
presets = ["p"]
protected = [{ path = "/opt/x", ceiling = "full" }]
[presets.p]
protected = [{ path = "/opt/x", ceiling = "ask" }, { path = "/opt/y", ceiling = "edit" }]
"""
    )
    r = resolve(p, plat(tmp_path)).everywhere
    assert {(x.path, x.ceiling) for x in r.protected} == {
        (Path("/opt/x"), Access.FULL),
        (Path("/opt/y"), Access.EDIT),
    }


@pytest.mark.parametrize("order", [("full", "ask"), ("ask", "full")])
def test_two_presets_disagreeing_on_a_ceiling_take_the_stricter(tmp_path: Path, order):
    first, second = order
    p = parse_policy(
        f"""
version = 1
[everywhere]
presets = ["p", "q"]
[presets.p]
protected = [{{ path = "/opt/x", ceiling = "{first}" }}]
[presets.q]
protected = [{{ path = "/opt/x", ceiling = "{second}" }}]
"""
    )
    r = resolve(p, plat(tmp_path)).everywhere
    assert [(x.path, x.ceiling) for x in r.protected] == [(Path("/opt/x"), Access.ASK)]


def test_closed_stricter_wins(tmp_path: Path):
    p = parse_policy('version = 1\n[everywhere]\npresets = ["baseline", "strict"]\n')
    assert resolve(p, plat(tmp_path)).everywhere.closed is Closed.DENY


def test_duplicate_grants_merge_but_conflicting_access_is_an_error(tmp_path: Path):
    same = parse_policy(
        'version = 1\n[everywhere]\npresets = ["p"]\nwrite = [{ path = "/srv/a", access = "full" }]\n'
        '[presets.p]\nwrite = [{ path = "/srv/a", access = "full", protect = ["x"] }]\n'
    )
    g = resolve(same, plat(tmp_path)).everywhere.grants
    assert len(g) == 1 and g[0].protect == ("x",)

    clash = parse_policy(
        'version = 1\n[everywhere]\npresets = ["p"]\nwrite = [{ path = "/srv/a", access = "full" }]\n'
        '[presets.p]\nwrite = [{ path = "/srv/a", access = "ask" }]\n'
    )
    with pytest.raises(PolicyError, match="/srv/a.*full.*ask|/srv/a.*ask.*full"):
        resolve(clash, plat(tmp_path))


@pytest.mark.parametrize("bad", ["/", "~", "/mnt/d", "/mnt/c/Users/someone"])
def test_broad_grants_refused(tmp_path: Path, bad: str):
    p = parse_policy(
        f'version = 1\n[everywhere]\nwrite = [{{ path = "{bad}", access = "full" }}]\n'
    )
    with pytest.raises(PolicyError, match="refusing"):
        resolve(p, plat(tmp_path / "home", system_drive=Path("/mnt/c"), drives=(Path("/mnt/d"),)))


def test_shallow_grant_needs_broad_flag(tmp_path: Path):
    p = parse_policy('version = 1\n[everywhere]\nwrite = [{ path = "/srv", access = "full" }]\n')
    with pytest.raises(PolicyError, match="broad"):
        resolve(p, plat(tmp_path / "home"))
    ok = parse_policy(
        'version = 1\n[everywhere]\nwrite = [{ path = "/srv", access = "full", broad = true }]\n'
    )
    assert resolve(ok, plat(tmp_path / "home")).everywhere.grants[0].path == Path("/srv")


def test_deny_grant_may_be_broad(tmp_path: Path):
    p = parse_policy('version = 1\n[everywhere]\nwrite = [{ path = "/mnt/d", access = "deny" }]\n')
    r = resolve(p, plat(tmp_path / "home", drives=(Path("/mnt/d"),)))
    assert r.everywhere.grants[0].access is Access.DENY


def test_project_at_or_above_home_refused(tmp_path: Path):
    home = tmp_path / "home"
    p = parse_policy('version = 1\n[projects."~"]\n')
    with pytest.raises(PolicyError, match="project"):
        resolve(p, plat(home))


# ---- format-preserving edits -------------------------------------------------------------------

DOC = """\
# my policy
version = 1

[everywhere]
presets = ["baseline"]  # keep this
write = [
  { path = "~/notes", access = "ask" },  # why: notes
]
"""


def test_add_grant_to_everywhere_keeps_comments():
    pf = PolicyFile(DOC)
    pf.add_grant(None, "/srv/data", Access.EDIT, protect=["*.lock"])
    text = pf.text
    assert "# my policy" in text and "# keep this" in text and "# why: notes" in text
    g = parse_policy(text).everywhere.write
    assert [(x.path, x.access, x.protect) for x in g] == [
        ("~/notes", Access.ASK, ()),
        ("/srv/data", Access.EDIT, ("*.lock",)),
    ]


def test_add_grant_creates_project_section():
    pf = PolicyFile(DOC)
    pf.add_grant("/home/u/repo", "/mnt/d/x", Access.FULL)
    p = parse_policy(pf.text)
    assert p.projects["/home/u/repo"].write[0].path == "/mnt/d/x"
    assert "# keep this" in pf.text


def test_add_grant_replaces_the_same_path_in_the_same_scope():
    pf = PolicyFile(DOC)
    pf.add_grant(None, "~/notes", Access.FULL)
    g = parse_policy(pf.text).everywhere.write
    assert [(x.path, x.access) for x in g] == [("~/notes", Access.FULL)]


def test_remove_grant():
    pf = PolicyFile(DOC)
    pf.add_grant("/home/u/repo", "/mnt/d/x", Access.FULL)
    assert pf.remove_grant(None, "~/notes") is True
    assert pf.remove_grant(None, "~/notes") is False
    assert pf.remove_grant("/home/u/repo", "/mnt/d/x") is True
    p = parse_policy(pf.text)
    assert p.everywhere.write == ()
    assert "/home/u/repo" not in p.projects  # emptied project section removed
    assert "# my policy" in pf.text


def test_set_protected_adds_then_changes_ceiling():
    pf = PolicyFile(DOC)
    pf.set_protected(None, "/opt/app", Access.ASK)
    pf.set_protected(None, "/opt/app", Access.EDIT)
    prot = parse_policy(pf.text).everywhere.protected
    assert [(x.path, x.ceiling) for x in prot] == [("/opt/app", Access.EDIT)]


def test_new_policy_file():
    pf = PolicyFile.new()
    p = parse_policy(pf.text)
    assert p.everywhere.presets == ("baseline",)


def test_edits_are_validated():
    pf = PolicyFile(DOC)
    with pytest.raises(PolicyError):
        pf.add_grant(None, "relative/path", Access.FULL)
    assert parse_policy(pf.text).everywhere.write[0].path == "~/notes"  # unchanged
