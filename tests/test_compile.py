"""compile: a resolved policy to settings entries per file, hook data and warnings (design section 7)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claude_guard.compile import HOOK_MATCHER, compile_policy
from claude_guard.errors import CompileError
from claude_guard.model import Compiled, Entry, Platform
from claude_guard.policy import parse_policy, resolve

SNAP_DIR = Path(__file__).parent / "snapshots"

ALLOW, ASK, DENY = "permissions.allow", "permissions.ask", "permissions.deny"
ALLOW_WRITE, DENY_WRITE = "sandbox.filesystem.allowWrite", "sandbox.filesystem.denyWrite"
HOOKS = "hooks.PreToolUse"


@pytest.fixture
def plat(tmp_path: Path) -> Platform:
    home = tmp_path / "home"
    home.mkdir()
    return Platform(
        home=home, guard_home=home / ".config" / "claude-guard", hook_command=("/opt/guard",)
    )


def compile_text(text: str, plat: Platform) -> Compiled:
    return compile_policy(resolve(parse_policy("version = 1\n" + text), plat), plat)


def arr(c: Compiled, file: Path, path: str) -> list:
    return [e.value for e in c.files.get(file, []) if e.kind == "array" and e.path == path]


def scalars(c: Compiled, file: Path) -> dict:
    return {e.path: e.value for e in c.files.get(file, []) if e.kind == "scalar"}


def r(p: Path) -> str:
    """Rule form of an absolute path (VB-R4)."""
    return "/" + str(p)


def mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---- access levels (section 6) ------------------------------------------------------------------


def test_full_is_one_edit_allow_and_nothing_else(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "srv" / "a")
    c = compile_text(f'[everywhere]\nwrite = [{{ path = "{d}", access = "full" }}]\n', plat)
    # VB-S7: the Edit allow reaches the sandbox, so no allowWrite and no Bash rules are needed.
    assert c.files == {plat.user_settings: [Entry("array", ALLOW, f"Edit({r(d)}/**)")]}


@pytest.mark.parametrize(
    ("access", "expected"),
    [
        ("bash", [(ALLOW_WRITE, "{p}")]),
        ("edit", [(ALLOW, "Edit({r}/**)"), (DENY_WRITE, "{p}")]),
        ("modify", [(ALLOW, "Edit({r}/**)"), (DENY_WRITE, "{p}")]),
        ("ask", [(ASK, "Edit({r}/**)"), (DENY_WRITE, "{p}")]),
        ("deny", [(DENY, "Edit({r}/**)")]),
    ],
)
def test_access_levels_compile_to_the_documented_entries(
    plat: Platform, tmp_path: Path, access, expected
):
    d = mkdir(tmp_path / "srv" / "a")
    c = compile_text(f'[everywhere]\nwrite = [{{ path = "{d}", access = "{access}" }}]\n', plat)
    want = [Entry("array", k, v.format(p=d, r=r(d))) for k, v in expected]
    assert sorted(c.files[plat.user_settings], key=repr) == sorted(want, key=repr)


def test_protect_globs_become_edit_denies(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "srv" / "a")
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "edit", protect = ["*.lock", "keep"] }}]\n',
        plat,
    )
    assert arr(c, plat.user_settings, DENY) == [f"Edit({r(d)}/*.lock)", f"Edit({r(d)}/keep)"]
    assert c.warnings == []  # Bash is read-only there anyway


def test_wildcard_protect_glob_on_bash_writable_grant_warns(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "srv" / "a")
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "full", protect = ["*.lock"] }}]\n', plat
    )
    assert any("*.lock" in w and "Bash" in w for w in c.warnings)


def test_missing_grant_folder_warns(plat: Platform, tmp_path: Path):
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{tmp_path}/no/such", access = "full" }}]\n', plat
    )
    assert any("does not exist" in w for w in c.warnings)  # VB-S5


def test_entries_are_deduplicated_and_deterministic(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "srv" / "a")
    text = (
        f'[everywhere]\npresets = ["p", "p2"]\n'
        f'[presets.p]\nwrite = [{{ path = "{d}", access = "full" }}]\ncommands.ask = ["x *"]\n'
        f'[presets.p2]\nwrite = [{{ path = "{d}", access = "full" }}]\ncommands.ask = ["x *"]\n'
    )
    a, b = compile_text(text, plat), compile_text(text, plat)
    assert a.files == b.files
    assert arr(a, plat.user_settings, ASK) == ["Bash(x *)"]
    assert arr(a, plat.user_settings, ALLOW) == [f"Edit({r(d)}/**)"]


# ---- scopes (section 7.1) -----------------------------------------------------------------------


def test_project_section_goes_to_project_local_file(plat: Platform, tmp_path: Path):
    proj = mkdir(tmp_path / "repos" / "p")
    d = mkdir(tmp_path / "data" / "x")
    c = compile_text(f'[projects."{proj}"]\nwrite = [{{ path = "{d}", access = "edit" }}]\n', plat)
    local = proj / ".claude" / "settings.local.json"
    assert set(c.files) == {local}
    assert arr(c, local, ALLOW) == [f"Edit({r(d)}/**)"]
    assert arr(c, local, DENY_WRITE) == [str(d)]


def test_commands_read_deny_and_raw(plat: Platform, tmp_path: Path):
    home = plat.home
    mkdir(home / ".ssh")
    (home / ".netrc").write_text("x")
    c = compile_text(
        """
[everywhere]
commands.allow = ["make *"]
commands.ask = ["git push --force *"]
commands.deny = ["sudo *"]
read.deny = ["~/.ssh", "~/.netrc", "~/.missing", "~/.missing-dir/", "**/.env"]
raw.allow = ["WebFetch(domain:example.com)"]
raw.deny = ["Read(//weird/**)"]
raw.sandbox.allow_write = ["~/out"]
raw.sandbox.deny_write = ["/srv/ro"]
raw.settings = [{ path = "model", value = "opus" }, { path = "env.FOO", value = "1" }]
""",
        plat,
    )
    u = plat.user_settings
    assert arr(c, u, ALLOW) == ["Bash(make *)", "WebFetch(domain:example.com)"]
    assert arr(c, u, ASK) == ["Bash(git push --force *)"]
    assert arr(c, u, DENY) == [
        "Bash(sudo *)",
        f"Read({r(home)}/.ssh/**)",  # an existing folder: its contents
        f"Read({r(home)}/.netrc)",  # an existing file: exactly
        f"Read({r(home)}/.missing)",  # missing and not marked as a folder: exactly
        f"Read({r(home)}/.missing-dir/**)",  # a trailing / marks a folder
        "Read(//**/.env)",
        "Read(//weird/**)",
    ]
    assert arr(c, u, ALLOW_WRITE) == [str(home / "out")]
    assert arr(c, u, DENY_WRITE) == ["/srv/ro"]
    assert scalars(c, u) == {"model": "opus", "env.FOO": "1"}


def test_conflicting_scalars_across_scopes_rejected(plat: Platform, tmp_path: Path):
    proj = mkdir(tmp_path / "repos" / "p")
    with pytest.raises(CompileError, match="model"):
        compile_text(
            '[everywhere]\nraw.settings = [{ path = "model", value = "opus" }]\n'
            f'[projects."{proj}"]\nraw.settings = [{{ path = "model", value = "sonnet" }}]\n',
            plat,
        )


def test_conflicting_scalars_from_presets_rejected(plat: Platform):
    with pytest.raises(CompileError, match="model"):
        compile_text(
            '[everywhere]\npresets = ["a", "b"]\n'
            '[presets.a]\nraw.settings = [{ path = "model", value = "opus" }]\n'
            '[presets.b]\nraw.settings = [{ path = "model", value = "sonnet" }]\n',
            plat,
        )


# ---- the hook registration ----------------------------------------------------------------------


def hook_entry(plat: Platform) -> dict:
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": "/opt/guard",
                "args": ["hook", "edit", "--guard-home", str(plat.guard_home)],
                "onFailure": "block",  # VB-H3
            }
        ],
    }


def test_hook_registered_once_in_user_file(plat: Platform, tmp_path: Path):
    proj = mkdir(tmp_path / "repos" / "p")
    c = compile_text(f'[everywhere]\nclosed = "ask"\n[projects."{proj}"]\nclosed = "deny"\n', plat)
    assert arr(c, plat.user_settings, HOOKS) == [hook_entry(plat)]
    assert arr(c, proj / ".claude" / "settings.local.json", HOOKS) == []
    assert HOOK_MATCHER == "Write|Edit|MultiEdit|NotebookEdit"  # VB-H8


def test_hook_registered_in_project_file_when_only_a_project_wants_it(
    plat: Platform, tmp_path: Path
):
    proj = mkdir(tmp_path / "repos" / "p")
    c = compile_text(f'[projects."{proj}"]\nclosed = "ask"\n', plat)
    assert arr(c, proj / ".claude" / "settings.local.json", HOOKS) == [hook_entry(plat)]
    assert plat.user_settings not in c.files


# ---- baseline (section 7.2) ---------------------------------------------------------------------


@pytest.fixture
def wsl(tmp_path: Path) -> Platform:
    home = mkdir(tmp_path / "home")
    c = mkdir(tmp_path / "mnt" / "c")
    for d in ("Windows", "Program Files", "Program Files (x86)", "ProgramData"):
        mkdir(c / d)
    dd = mkdir(tmp_path / "mnt" / "d")
    mkdir(home / ".ssh")
    return Platform(
        home=home,
        guard_home=home / ".config" / "claude-guard",
        hook_command=("/opt/guard",),
        system_drive=c,
        drives=(c, dd),
        caches=(mkdir(home / ".cache" / "uv"),),
        settings_scripts=(home / "bin" / "status.sh",),
        managed_settings=tmp_path / "etc" / "claude-code",
    )


def _snapshot(name: str, text: str) -> None:
    path = SNAP_DIR / name
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        path.write_text(text)
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            pytest.fail(f"snapshot {name} did not exist; created it. Review it, then re-run.")
        return
    assert text == path.read_text(), f"snapshot {name} differs (UPDATE_SNAPSHOTS=1 to refresh)"


def test_baseline_golden(wsl: Platform, tmp_path: Path):
    c = compile_text('[everywhere]\npresets = ["baseline"]\n', wsl)
    assert set(c.files) == {wsl.user_settings}
    out = {
        "entries": [e.to_json() for e in c.files[wsl.user_settings]],
        "hook": c.hook,
        "warnings": c.warnings,
    }
    text = json.dumps(out, indent=2).replace(str(tmp_path), "<TMP>") + "\n"
    _snapshot("v2_baseline_user.json", text)


def test_baseline_essentials(wsl: Platform):
    c = compile_text('[everywhere]\npresets = ["baseline"]\n', wsl)
    u, home, cdrive = wsl.user_settings, wsl.home, wsl.system_drive
    assert scalars(c, u) == {
        "sandbox.enabled": True,
        "sandbox.allowUnsandboxedCommands": False,  # VB-U5
        "permissions.disableBypassPermissionsMode": "disable",  # VB-P6
    }
    assert arr(c, u, HOOKS) == [hook_entry(wsl)]
    deny = arr(c, u, DENY)
    # protected with ceiling deny: Edit deny, refused before the hook (VB-E5)
    assert f"Edit({r(cdrive)}/Windows/**)" in deny
    assert f"Edit({r(cdrive)}/Program Files (x86)/**)" in deny
    # locked: concrete user files, globs for every project, managed settings, guard itself
    assert f"Edit({r(home)}/.claude/settings.json)" in deny
    assert f"Edit({r(home)}/.claude.json)" in deny
    assert "Edit(//**/.claude/settings.json)" in deny
    assert "Edit(//**/.mcp.json)" in deny
    assert f"Edit({r(wsl.guard_home)}/**)" in deny
    assert f"Edit({r(home)}/.claude/settings.json/**)" not in deny  # no doubled forms
    # secrets
    assert f"Read({r(home)}/.ssh/**)" in deny
    assert "Read(//**/.env)" in deny and "Read(//**/.env.*)" in deny
    assert "Bash(sudo *)" in deny
    # caches open to Bash only (VB-S16)
    assert arr(c, u, ALLOW_WRITE) == [str(home / ".cache" / "uv")]
    # protected with ceiling ask/edit (/etc, ~/.bashrc, drives): the hook only, no rules
    assert not any("/etc/**" in x or ".bashrc" in x for x in deny + arr(c, u, DENY_WRITE))
    assert not any(str(wsl.drives[1]) in x for x in deny + arr(c, u, DENY_WRITE))
    # no allow rules at all: nothing is opened for the file tools
    assert arr(c, u, ALLOW) == []


def test_bash_writable_grant_gets_concrete_locked_settings_paths(wsl: Platform, tmp_path: Path):
    """`**/` globs do not reach the sandbox (VB-S14); inside a grant Bash can write, the built-in
    `.claude/` protection may not apply (VB-U17), so the settings files are named explicitly."""
    d = mkdir(tmp_path / "work" / "w")
    c = compile_text(
        f'[everywhere]\npresets = ["baseline"]\nwrite = [{{ path = "{d}", access = "full" }}]\n',
        wsl,
    )
    dw = arr(c, wsl.user_settings, DENY_WRITE)
    assert str(d / ".claude" / "settings.json") in dw
    assert str(d / ".claude" / "settings.local.json") in dw
    assert str(d / ".mcp.json") in dw
    e = compile_text(
        f'[everywhere]\npresets = ["baseline"]\nwrite = [{{ path = "{d}", access = "edit" }}]\n',
        wsl,
    )
    edw = arr(e, wsl.user_settings, DENY_WRITE)
    assert str(d) in edw  # already read-only to Bash: no settings paths needed inside it
    assert not any(x.startswith(str(d) + "/") for x in edw)


def test_cache_grant_in_baseline_gets_locked_paths_too(wsl: Platform):
    c = compile_text('[everywhere]\npresets = ["baseline"]\n', wsl)
    cache = wsl.caches[0]
    assert str(cache / ".mcp.json") in arr(c, wsl.user_settings, DENY_WRITE)


# ---- protected paths and ceilings (section 6.1) -------------------------------------------------


def test_grant_within_ceiling_allowed(wsl: Platform):
    d = mkdir(wsl.drives[1] / "proj" / "x")
    c = compile_text(
        f'[everywhere]\npresets = ["baseline"]\nwrite = [{{ path = "{d}", access = "edit" }}]\n',
        wsl,
    )
    assert f"Edit({r(d)}/**)" in arr(c, wsl.user_settings, ALLOW)


@pytest.mark.parametrize("access", ["full", "bash"])
def test_grant_above_ceiling_rejected_naming_the_protected_entry(wsl: Platform, access):
    d = mkdir(wsl.drives[1] / "proj" / "x")
    with pytest.raises(CompileError, match=rf"ceiling.*{wsl.drives[1]}|{wsl.drives[1]}.*ceiling"):
        compile_text(
            f'[everywhere]\npresets = ["baseline"]\nwrite = [{{ path = "{d}", access = "{access}" }}]\n',
            wsl,
        )


def test_ceiling_deny_blocks_every_grant_but_deny(wsl: Platform):
    win = wsl.system_drive / "Windows" / "Temp"
    with pytest.raises(CompileError, match="ceiling"):
        compile_text(
            f'[everywhere]\npresets = ["baseline"]\nwrite = [{{ path = "{win}", access = "ask" }}]\n',
            wsl,
        )


def test_everywhere_can_raise_a_ceiling(wsl: Platform):
    d = mkdir(wsl.drives[1] / "proj" / "x")
    c = compile_text(
        f'[everywhere]\npresets = ["baseline"]\n'
        f'protected = [{{ path = "{wsl.drives[1]}", ceiling = "full" }}]\n'
        f'write = [{{ path = "{d}", access = "full" }}]\n',
        wsl,
    )
    assert f"Edit({r(d)}/**)" in arr(c, wsl.user_settings, ALLOW)


def test_project_can_lower_but_not_raise_a_ceiling(wsl: Platform, tmp_path: Path):
    proj = mkdir(tmp_path / "repos" / "p")
    d = mkdir(wsl.drives[1] / "proj" / "x")
    base = '[everywhere]\npresets = ["baseline"]\n'
    with pytest.raises(CompileError, match="ceiling"):
        compile_text(
            base + f'[projects."{proj}"]\n'
            f'protected = [{{ path = "{wsl.drives[1]}", ceiling = "full" }}]\n'
            f'write = [{{ path = "{d}", access = "full" }}]\n',
            wsl,
        )
    with pytest.raises(CompileError, match="ceiling"):
        compile_text(
            base + f'[projects."{proj}"]\n'
            f'protected = [{{ path = "{wsl.drives[1]}", ceiling = "ask" }}]\n'
            f'write = [{{ path = "{d}", access = "edit" }}]\n',
            wsl,
        )


def test_protected_path_inside_bash_writable_grant_gets_deny_write(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "work" / "w")
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "full" }}]\n'
        f'protected = [{{ path = "{d}/scripts", ceiling = "ask" }}]\n',
        plat,
    )
    assert arr(c, plat.user_settings, DENY_WRITE) == [str(d / "scripts")]


def test_protected_path_inside_read_only_grant_needs_nothing_extra(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "work" / "w")
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "edit" }}]\n'
        f'protected = [{{ path = "{d}/scripts", ceiling = "ask" }}]\n',
        plat,
    )
    assert arr(c, plat.user_settings, DENY_WRITE) == [str(d)]


def test_protected_path_inside_a_read_only_grant_inside_a_writable_one(
    plat: Platform, tmp_path: Path
):
    d = mkdir(tmp_path / "work" / "w")
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "full" }}, '
        f'{{ path = "{d}/docs", access = "edit" }}]\n'
        f'protected = [{{ path = "{d}/docs/x", ceiling = "ask" }}]\n',
        plat,
    )
    assert arr(c, plat.user_settings, DENY_WRITE) == [str(d / "docs")]  # covers x already


def test_protected_path_inside_managed_project_gets_deny_write(plat: Platform, tmp_path: Path):
    proj = mkdir(tmp_path / "repos" / "p")
    c = compile_text(
        f'[everywhere]\nprotected = [{{ path = "{proj}/hooks/run.sh", ceiling = "ask" }}]\n'
        f'[projects."{proj}"]\ncommands.ask = ["x *"]\n',
        plat,
    )
    local = proj / ".claude" / "settings.local.json"
    assert arr(c, local, DENY_WRITE) == [str(proj / "hooks" / "run.sh")]
    assert arr(c, plat.user_settings, DENY_WRITE) == []


def test_grant_opening_a_protected_path_suppresses_deny_write(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "work" / "w")
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "full" }}, '
        f'{{ path = "{d}/scripts", access = "full" }}]\n'
        f'protected = [{{ path = "{d}/scripts", ceiling = "full" }}]\n',
        plat,
    )
    assert arr(c, plat.user_settings, DENY_WRITE) == []


def test_partly_opened_protected_path_in_bash_writable_area_warns(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "work" / "w")
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "full" }}, '
        f'{{ path = "{d}/scripts/sub", access = "full" }}]\n'
        f'protected = [{{ path = "{d}/scripts", ceiling = "full" }}]\n',
        plat,
    )
    assert any(str(d / "scripts") in w and "Bash" in w for w in c.warnings)


def test_user_wide_deny_write_shadowing_a_project_grant_rejected(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "work" / "w")
    proj = mkdir(tmp_path / "repos" / "p")
    with pytest.raises(CompileError, match="scripts"):
        compile_text(
            f'[everywhere]\nwrite = [{{ path = "{d}", access = "full" }}]\n'
            f'protected = [{{ path = "{d}/scripts", ceiling = "full" }}]\n'
            f'[projects."{proj}"]\nwrite = [{{ path = "{d}/scripts", access = "full" }}]\n',
            plat,
        )


# ---- conflicts (section 7.4) --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outer", "inner"),
    [
        ("edit", "full"),
        ("edit", "bash"),
        ("ask", "full"),
        ("ask", "edit"),
        ("modify", "full"),
        ("deny", "ask"),
        ("deny", "full"),
    ],
)
def test_more_permissive_grant_inside_a_stricter_one_rejected(
    plat: Platform, tmp_path: Path, outer, inner
):
    d = mkdir(tmp_path / "work" / "w")
    with pytest.raises(CompileError, match=rf"{inner}.*{outer}|{outer}.*{inner}"):
        compile_text(
            f'[everywhere]\nwrite = [{{ path = "{d}", access = "{outer}" }}, '
            f'{{ path = "{d}/in", access = "{inner}" }}]\n',
            plat,
        )


@pytest.mark.parametrize(
    ("outer", "inner"),
    [
        ("full", "edit"),
        ("full", "ask"),
        ("full", "deny"),
        ("edit", "ask"),
        ("bash", "edit"),
        ("full", "bash"),
        ("edit", "modify"),
    ],
)
def test_stricter_grant_inside_a_looser_one_is_fine(plat: Platform, tmp_path: Path, outer, inner):
    d = mkdir(tmp_path / "work" / "w")
    compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "{outer}" }}, '
        f'{{ path = "{d}/in", access = "{inner}" }}]\n',
        plat,
    )


def test_project_grant_inside_stricter_everywhere_grant_rejected(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "work" / "w")
    proj = mkdir(tmp_path / "repos" / "p")
    with pytest.raises(CompileError, match=r"\[everywhere\]"):
        compile_text(
            f'[everywhere]\nwrite = [{{ path = "{d}", access = "ask" }}]\n'
            f'[projects."{proj}"]\nwrite = [{{ path = "{d}", access = "full" }}]\n',
            plat,
        )


def test_project_may_tighten_an_everywhere_grant(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "work" / "w")
    proj = mkdir(tmp_path / "repos" / "p")
    c = compile_text(
        f'[everywhere]\nwrite = [{{ path = "{d}", access = "full" }}]\n'
        f'[projects."{proj}"]\nwrite = [{{ path = "{d}", access = "ask" }}]\n',
        plat,
    )
    local = proj / ".claude" / "settings.local.json"
    assert arr(c, local, ASK) == [f"Edit({r(d)}/**)"]


def test_grant_on_or_inside_a_locked_path_rejected(wsl: Platform):
    target = mkdir(wsl.guard_home / "sub")
    with pytest.raises(CompileError, match="locked"):
        compile_text(
            f'[everywhere]\npresets = ["baseline"]\nwrite = [{{ path = "{target}", access = "full" }}]\n',
            wsl,
        )


def test_grant_on_a_path_matching_a_locked_glob_rejected(plat: Platform, tmp_path: Path):
    d = mkdir(tmp_path / "work" / "w" / ".claude")
    with pytest.raises(CompileError, match="locked"):
        compile_text(
            f'[everywhere]\nlocked = ["**/.claude"]\nwrite = [{{ path = "{d}", access = "full" }}]\n',
            plat,
        )


# ---- hook data (stored in applied.json, read by the hook) ---------------------------------------


def test_hook_data_merges_everywhere_into_each_project(plat: Platform, tmp_path: Path):
    a = mkdir(tmp_path / "data" / "a")
    b = mkdir(tmp_path / "data" / "b")
    proj = mkdir(tmp_path / "repos" / "p")
    c = compile_text(
        f'[everywhere]\nclosed = "ask"\nwrite = [{{ path = "{a}", access = "modify" }}]\n'
        f'protected = [{{ path = "/opt/app", ceiling = "edit" }}]\nlocked = ["**/.mcp.json"]\n'
        f'[projects."{proj}"]\nclosed = "deny"\n'
        f'write = [{{ path = "{b}", access = "ask", protect = ["k"] }}]\n'
        f'protected = [{{ path = "/opt/app", ceiling = "ask" }}]\n',
        plat,
    )
    h = c.hook
    assert h["home"] == str(plat.home)
    star = h["scopes"]["*"]
    assert star == {
        "closed": "ask",
        "grants": [{"path": str(a), "access": "modify", "protect": []}],
        "protected": [{"path": "/opt/app", "ceiling": "edit"}],
        "locked": ["**/.mcp.json"],
    }
    p = h["scopes"][str(proj)]
    assert p["closed"] == "deny"
    assert p["grants"] == [
        {"path": str(a), "access": "modify", "protect": []},
        {"path": str(b), "access": "ask", "protect": ["k"]},
    ]
    assert p["protected"] == [{"path": "/opt/app", "ceiling": "ask"}]  # lowered
    assert p["locked"] == ["**/.mcp.json"]


def test_compile_has_no_side_effects(plat: Platform, tmp_path: Path):
    proj = mkdir(tmp_path / "repos" / "p")
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    compile_text(f'[everywhere]\npresets = ["baseline"]\n[projects."{proj}"]\n', plat)
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before
