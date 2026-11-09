"""Command-line interface (``guard``)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .engine import Guard, Plan
from .errors import GuardError
from .paths import PathPolicy
from .profiles import ProfileSet, load_profiles
from .settings_view import (
    build_status,
    load_effective,
    local_settings,
    render_doctor,
    render_status,
    run_doctor,
    user_settings,
)

EDIT_TOOL_NOTE = (
    "Note: the sandbox confines Bash only. The built-in Edit/Write tools are NOT sandboxed: they are\n"
    "governed by the Edit rules above, and in auto mode can write elsewhere without a prompt."
)


def _default_state_dir() -> Path:
    if os.environ.get("CLAUDE_GUARD_HOME"):
        return Path(os.environ["CLAUDE_GUARD_HOME"])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claude-guard"


def _ask_yes_no(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


@dataclass
class Env:
    home: Path = field(default_factory=Path.home)
    cwd: Path = field(default_factory=Path.cwd)
    state_dir: Path = field(default_factory=_default_state_dir)
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    interactive: bool = field(default_factory=lambda: sys.stdin.isatty())
    confirm: Callable[[str], bool] = _ask_yes_no
    policy: PathPolicy | None = None
    managed_dir: Path = Path("/etc/claude-code")
    which: Callable[[str], str | None] = shutil.which
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    hook_command: list[str] | None = None

    def get_policy(self) -> PathPolicy:
        return self.policy or PathPolicy(self.home, _detected_roots())

    def p(self, *parts: object) -> None:
        print(*parts, file=self.out)

    def e(self, *parts: object) -> None:
        print(*parts, file=self.err)


def _detected_roots() -> set[Path]:
    from .paths import detect_drive_roots

    return detect_drive_roots()


def _profiles(env: Env) -> ProfileSet:
    return load_profiles(env.state_dir / "profiles.toml")


def _hook_command(env: Env) -> list[str]:
    """How Claude Code should run `guard`: an absolute path, because hooks do not use your PATH."""
    if env.hook_command:
        return env.hook_command
    found = env.which("guard")
    return [str(Path(found).absolute())] if found else [sys.executable, "-m", "claude_guard"]


def _guard(env: Env) -> Guard:
    return Guard(env.state_dir, _profiles(env), env.get_policy(), _hook_command(env))


def _project(env: Env, arg: str | None) -> Path:
    p = Path(arg).expanduser() if arg else env.cwd
    p = (env.cwd / p).resolve() if not p.is_absolute() else p.resolve()
    if not p.is_dir():
        raise GuardError(f"project directory does not exist: {p}")
    return p


def _settings_file(env: Env, scope: str, project: Path) -> Path:
    return user_settings(env.home) if scope == "user" else local_settings(project)


# ---- allow / revoke --------------------------------------------------------------------------


def _split_targets(
    args: argparse.Namespace, default_profiles: list[str]
) -> tuple[str | None, list[str]]:
    targets = list(args.targets)
    if args.global_:
        if not targets:
            raise GuardError("--global needs at least one profile name")
        return None, targets
    if not targets:
        raise GuardError("a folder is required (or use --global with profile names)")
    return targets[0], targets[1:] or default_profiles


def _sandbox_hint(env: Env, project: Path, plan: Plan) -> str | None:
    override = {c.path: c.new_text for c in plan.changes}
    eff = load_effective(env.home, project, env.managed_dir, override)
    found, val, _ = eff.scalar("sandbox.enabled")
    if found and val is True:
        return None
    return (
        "warning: the sandbox is not enabled in the effective settings, so Bash allow rules are NOT\n"
        "confined to granted folders. Enable it with:  guard allow --global lock"
    )


def _finish(
    env: Env, args: argparse.Namespace, guard: Guard, plan: Plan, project: Path, mkdir: Path | None
) -> int:
    for w in plan.warnings:
        env.p(f"warning: {w}")
    if mkdir is not None:
        env.p(f"{'Would create' if args.dry_run else 'Will create'} folder: {mkdir}")
    if plan.noop:
        env.p("Nothing to change.")
        return 0
    for ch in plan.changes:
        if ch.reformats:
            env.p(f"note: {ch.path} is not in standard JSON layout and will be reformatted")
    env.p(plan.diff() or "(settings files unchanged; only claude-guard state changes)")
    if args.dry_run:
        env.p("Dry run: nothing was changed.")
        return 0
    if not args.yes:
        if not env.interactive:
            raise GuardError("not a terminal: refusing to apply without --yes")
        if not env.confirm("Apply these changes?"):
            env.p("Aborted; nothing was changed.")
            return 1
    if mkdir is not None:
        mkdir.mkdir(parents=True, exist_ok=True)
    backups = guard.commit(plan)
    env.p(f"Applied. {len(backups)} backup(s) in {guard.backup_dir}")
    hint = _sandbox_hint(env, project, plan)
    if hint:
        env.p(hint)
    env.p("Claude Code reloads settings files in running sessions (verified for sandbox paths).")
    env.p(EDIT_TOOL_NOTE)
    return 0


def cmd_allow(env: Env, args: argparse.Namespace) -> int:
    project = _project(env, args.project)
    settings = _settings_file(env, args.scope, project)
    folder, names = _split_targets(args, ["edit"])
    guard = _guard(env)
    session = guard.new_session()
    mkdir = None
    if folder is not None:
        resolved = guard.policy.check_folder(folder, force_broad=args.force_broad)
        if args.create and not resolved.exists():
            mkdir = resolved
    guard.allow(
        session,
        folder,
        names,
        settings,
        args.protect,
        force_broad=args.force_broad,
        will_create=mkdir is not None,
    )
    first_time = not any(
        g["settings_file"] == str(settings) for g in session.old_state["grants"].values()
    )
    if first_time and not args.no_secrets and "secrets" not in names:
        guard.allow(session, None, ["secrets"], settings)
    return _finish(env, args, guard, session.plan(), project, mkdir)


def cmd_revoke(env: Env, args: argparse.Namespace) -> int:
    project = _project(env, args.project)
    settings = _settings_file(env, args.scope, project)
    targets = list(args.targets)
    if args.global_:
        folder, names = None, targets
    else:
        if not targets:
            raise GuardError("a folder is required (or use --global)")
        folder, names = targets[0], targets[1:]
    guard = _guard(env)
    session = guard.new_session()
    guard.revoke(session, folder, settings, names or None)
    return _finish(env, args, guard, session.plan(), project, None)


# ---- read-only commands ----------------------------------------------------------------------


def cmd_status(env: Env, args: argparse.Namespace) -> int:
    project = _project(env, args.project)
    status = build_status(env.home, project, env.state_dir / "state.json")
    env.p(json.dumps(status, indent=2) if args.json else render_status(status).rstrip("\n"))
    return 0


def _summary(prof) -> str:
    bits = []
    if prof.edit_paths:
        bits.append("Edit allow")
    if prof.edit_ask:
        bits.append("Edit ask")
    if prof.edit_deny:
        bits.append(f"{len(prof.edit_deny)} Edit deny")
    if prof.edit_hook:
        bits.append(f"Edit/Write hook ({prof.edit_hook})")
    if prof.sandbox_write:
        bits.append("sandbox write")
    if prof.sandbox_deny_write:
        bits.append("Bash read-only")
    for label, vals in (
        ("allow", prof.bash_allow),
        ("ask", prof.bash_ask),
        ("deny", prof.bash_deny),
    ):
        if vals:
            bits.append(f"{len(vals)} Bash {label}")
    if prof.read_deny:
        bits.append(f"{len(prof.read_deny)} Read deny")
    if prof.protect:
        bits.append(f"protect {len(prof.protect)}")
    if prof.settings:
        bits.append(f"{len(prof.settings)} setting(s)")
    return ", ".join(bits) or "nothing"


def cmd_profiles(env: Env, args: argparse.Namespace) -> int:
    ps = _profiles(env)
    width = max(len(n) for n in ps.names)
    for name in ps.names:
        prof = ps.resolve(name)
        env.p(f"{name:<{width}}  {prof.description}")
        env.p(f"{'':<{width}}  -> {_summary(prof)}")
    return 0


def cmd_profile_show(env: Env, args: argparse.Namespace) -> int:
    ps = _profiles(env)
    prof = ps.resolve(args.name)
    raw = ps.get(args.name)
    env.p(f"[{prof.name}]")
    if prof.description:
        env.p(f"description: {prof.description}")
    if prof.warning:
        env.p(f"WARNING: {prof.warning}")
    if raw.extends:
        env.p(f"extends: {', '.join(raw.extends)}")
    for label, vals in (
        ("edit_paths (per folder, Edit allow)", prof.edit_paths),
        ("edit_ask (per folder, Edit ask: prompts even in auto mode)", prof.edit_ask),
        ("edit_deny (global, Edit deny)", prof.edit_deny),
        ("bash_allow (global!)", prof.bash_allow),
        ("bash_ask (global)", prof.bash_ask),
        ("bash_deny (global)", prof.bash_deny),
        ("read_deny (global)", prof.read_deny),
        ("protect (Edit deny, relative to folder)", prof.protect),
    ):
        if vals:
            env.p(f"{label}:")
            for v in vals:
                env.p(f"  {v}")
    if prof.sandbox_write:
        env.p("sandbox_write: folder is added to sandbox.filesystem.allowWrite")
    if prof.edit_hook:
        env.p(f"edit_hook: {prof.edit_hook} (PreToolUse hook on Edit/Write, fail-closed)")
    if prof.sandbox_deny_write:
        env.p("sandbox_deny_write: Bash cannot write or delete in the folder (denyWrite)")
    if prof.settings:
        env.p("settings:")
        for path, value in prof.settings:
            env.p(f"  {path} = {json.dumps(value)}")
    if prof.bash_allow:
        env.p("")
        env.p(
            "Bash allow rules are global, not path-scoped: they are only safe because the sandbox"
        )
        env.p("bounds where writes can land. `guard doctor` warns if the sandbox is off.")
    return 0


def cmd_explain(env: Env, args: argparse.Namespace) -> int:
    from .explain import explain_command, explain_path, render_explanation, to_json

    project = _project(env, args.project)
    if args.command:
        exp = explain_command(
            args.target, env.home, project, env.managed_dir, env.state_dir / "state.json"
        )
    else:
        exp = explain_path(
            args.target, env.cwd, env.home, project, env.state_dir / "state.json", env.managed_dir
        )
    env.p(json.dumps(to_json(exp), indent=2) if args.json else render_explanation(exp).rstrip("\n"))
    return 0


def cmd_hook_edit(env: Env, args: argparse.Namespace) -> int:
    from .hook import run_hook

    text = run_hook(env.stdin.read(), Path(args.state_dir), args.unlisted, env.home)
    if text:
        env.p(text)
    return 0


def cmd_doctor(env: Env, args: argparse.Namespace) -> int:
    project = _project(env, args.project)
    checks = run_doctor(
        env.home,
        project,
        env.state_dir / "state.json",
        env.managed_dir,
        env.get_policy(),
        env.which,
    )
    if args.json:
        env.p(json.dumps([c.__dict__ for c in checks], indent=2))
    else:
        env.p(render_doctor(checks).rstrip("\n"))
    return 1 if any(c.level == "fail" for c in checks) else 0


# ---- parser ----------------------------------------------------------------------------------


def _add_common(sp: argparse.ArgumentParser, *, mutating: bool) -> None:
    sp.add_argument("--project", help="project directory (default: current directory)")
    if mutating:
        sp.add_argument(
            "--scope",
            choices=["user", "local"],
            default="local",
            help="user: ~/.claude/settings.json; local: <project>/.claude/settings.local.json",
        )
        sp.add_argument("--dry-run", action="store_true", help="show the diff, change nothing")
        sp.add_argument("--yes", "-y", action="store_true", help="apply without asking")
        sp.add_argument(
            "--global",
            dest="global_",
            action="store_true",
            help="folder-less grant: the arguments are profile names (secrets, lock)",
        )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="guard", description="Grant, inspect and revoke Claude Code write access to folders."
    )
    sub = ap.add_subparsers(dest="command", required=True)

    a = sub.add_parser("allow", help="grant profiles on a folder")
    a.add_argument(
        "targets",
        nargs="*",
        metavar="FOLDER [PROFILE ...]",
        help="folder then profile names (default profile: edit)",
    )
    _add_common(a, mutating=True)
    a.add_argument(
        "--protect",
        action="append",
        default=[],
        metavar="GLOB",
        help="folder-relative glob that must never be writable (repeatable)",
    )
    a.add_argument("--create", action="store_true", help="create the folder if missing")
    a.add_argument("--force-broad", action="store_true", help="allow a very shallow folder")
    a.add_argument(
        "--no-secrets",
        action="store_true",
        help="do not auto-apply the secrets profile when first managing a settings file",
    )
    a.set_defaults(fn=cmd_allow)

    r = sub.add_parser("revoke", help="remove profiles (or the whole grant) from a folder")
    r.add_argument("targets", nargs="*", metavar="FOLDER [PROFILE ...]")
    _add_common(r, mutating=True)
    r.set_defaults(fn=cmd_revoke)

    s = sub.add_parser("status", help="show grants and settings entries")
    _add_common(s, mutating=False)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    sub.add_parser("profiles", help="list profiles").set_defaults(fn=cmd_profiles)

    p = sub.add_parser("profile", help="profile details")
    psub = p.add_subparsers(dest="profile_command", required=True)
    ps = psub.add_parser("show", help="show what a profile expands to")
    ps.add_argument("name")
    ps.set_defaults(fn=cmd_profile_show)

    x = sub.add_parser("explain", help="what would happen to a path or a Bash command (read-only)")
    x.add_argument("target", help="a file or folder path, or a Bash command with --command")
    x.add_argument("--command", action="store_true", help="TARGET is a Bash command, not a path")
    _add_common(x, mutating=False)
    x.add_argument("--json", action="store_true")
    x.set_defaults(fn=cmd_explain)

    h = sub.add_parser("hook", help="hooks Claude Code runs (not for manual use)")
    hsub = h.add_subparsers(dest="hook_command", required=True)
    he = hsub.add_parser("edit", help="PreToolUse hook for the Edit/Write tools (reads stdin)")
    he.add_argument("--state-dir", required=True)
    he.add_argument("--unlisted", choices=("ask", "deny"), default="ask")
    he.set_defaults(fn=cmd_hook_edit)

    d = sub.add_parser("doctor", help="sanity checks (report only)")
    _add_common(d, mutating=False)
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_doctor)
    return ap


def main(argv: Sequence[str] | None = None, env: Env | None = None) -> int:
    env = env or Env()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(env, args)
    except GuardError as e:
        env.e(f"error: {e}")
        return 2
