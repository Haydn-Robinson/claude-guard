"""Command-line interface (``guard``). argparse wiring plus the confirm-and-apply flow.

High-level commands (``init``, ``allow``, ``revoke``, ``protect``) edit ``policy.toml`` and then run
the same plan and apply as the low-level ``apply``: there is one code path, so they cannot drift
apart (design section 3).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .applied import atomic_write, load_applied
from .apply import commit, plan
from .context import Ctx, build, read_policy
from .errors import GuardError
from .machine import default_guard_home
from .model import Access, Compiled, Platform
from .policy import (
    NEW_POLICY,
    PolicyFile,
    expand_paths,
    parse_policy,
    resolve_project_dir,
    shipped_presets,
)
from .proposals import Op, accept, check_target, list_proposals, parse_op, propose, review


def _ask_yes_no(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _run_editor(path: Path) -> int:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    return subprocess.call([*shlex.split(editor), str(path)])


@dataclass
class Env:
    ctx: Ctx
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    interactive: bool = field(default_factory=lambda: sys.stdin.isatty() and sys.stdout.isatty())
    in_claude: bool = field(default_factory=lambda: bool(os.environ.get("CLAUDECODE")))
    confirm: Callable[[str], bool] = _ask_yes_no
    editor: Callable[[Path], int] = _run_editor

    def p(self, *parts: object) -> None:
        print(*parts, file=self.out)


def default_env() -> Env:
    return Env(Ctx(home=Path.home(), cwd=Path.cwd(), guard_home=default_guard_home()))


BASELINE_SUMMARY = """\
The baseline preset:
  - turns the sandbox on, forbids unsandboxed retries, disables bypass-permissions mode;
  - registers the Edit/Write hook: writes outside the project and your grants ask first;
  - locks Claude Code's settings files (change them with `guard propose` / `guard review`);
  - protects Windows system folders (never), /etc, credentials and shell files (ask at most),
    Windows drives (edit at most) and the scripts your settings run (ask at most);
  - hides secrets (~/.ssh, ~/.aws, .env files, tool credentials) from the Read tool;
  - lets Bash write to your tool caches, so builds work;
  - refuses `sudo` (advisory)."""


# ---- the shared confirm-and-apply flow ---------------------------------------------------------


def _apply(
    env: Env,
    args: argparse.Namespace,
    policy_text: str | None,
    *,
    before_commit: Callable[[], None] | None = None,
    compiled: Compiled | None = None,
) -> int:
    """Plan ``policy_text`` (or ``compiled``), show it, confirm, commit, then save the policy."""
    ctx = env.ctx
    if compiled is None:
        assert policy_text is not None
        compiled = build(ctx, policy_text).compiled
    p = plan(compiled, load_applied(ctx.guard_home))
    try:
        current = ctx.policy_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    policy_changes = policy_text is not None and policy_text != current

    for w in p.warnings:
        env.p(f"warning: {w}")
    for n in p.notes:
        env.p(f"note: {n}")
    if p.noop and not policy_changes:
        env.p("Nothing to change: the settings match the policy.")
        return 0
    if policy_changes:
        env.p(f"policy: {ctx.policy_path} will be updated")
    env.p(p.diff() or "(no settings file changes)")
    if getattr(args, "dry_run", False):
        env.p("Dry run: nothing was changed.")
        return 0
    if not getattr(args, "yes", False):
        if not env.interactive:
            raise GuardError(
                "not a terminal: refusing to apply without --yes (review with --dry-run)"
            )
        if not env.confirm("Apply these changes?"):
            env.p("Aborted: nothing was changed.")
            return 1
    if before_commit:
        before_commit()
    if policy_changes:
        atomic_write(ctx.policy_path, policy_text)  # type: ignore[arg-type]
    try:
        backups = commit(p, ctx.guard_home)
    except BaseException:
        if policy_changes:
            if current is None:
                ctx.policy_path.unlink(missing_ok=True)
            else:
                atomic_write(ctx.policy_path, current)
        raise
    env.p(f"Applied. {len(backups)} backup(s) in {ctx.guard_home / 'backups'}.")
    if p.changes:
        env.p("Running Claude sessions pick this up within seconds (VB-S6); no restart needed.")
    return 0


# ---- scope and path helpers ---------------------------------------------------------------------


def _display(path: Path, home: Path) -> str:
    try:
        return "~/" + str(path.relative_to(home)) if path != home else "~"
    except ValueError:
        return str(path)


def _resolve_arg(env: Env, raw: str) -> Path:
    return Path(os.path.realpath(env.ctx.cwd / Path(raw).expanduser()))


def _probe(env: Env) -> Platform:
    return Platform(home=env.ctx.home, guard_home=env.ctx.guard_home, hook_command=())


def _scope_key(env: Env, args: argparse.Namespace, pf: PolicyFile) -> str | None:
    """None for [everywhere]; else the policy's key for the target project (reused if present)."""
    if args.everywhere:
        if args.project:
            raise GuardError("use either --everywhere or --project, not both")
        return None
    if args.project:
        proj: Path | None = _resolve_arg(env, args.project)
        if not proj.is_dir():  # type: ignore[union-attr]
            raise GuardError(f"project folder does not exist: {proj}")
        resolve_project_dir(str(proj), _probe(env))  # refuses home and above
    else:
        proj = env.ctx.project
    if proj is None:
        raise GuardError(
            "not in a project (here is your home folder or above): use --everywhere or --project DIR"
        )
    for raw in pf.policy.projects:
        if resolve_project_dir(raw, _probe(env)) == proj:
            return raw
    return _display(proj, env.ctx.home)


def _scope_name(key: str | None) -> str:
    return "[everywhere]" if key is None else f'[projects."{key}"]'


def _load_policy_file(env: Env) -> PolicyFile:
    return PolicyFile(read_policy(env.ctx), str(env.ctx.policy_path))


# ---- high-level commands --------------------------------------------------------------------------


def cmd_init(env: Env, args: argparse.Namespace) -> int:
    if env.ctx.policy_path.exists():
        raise GuardError(f"{env.ctx.policy_path} already exists: use `guard edit` or `guard apply`")
    env.p(f"Creating {env.ctx.policy_path} with the baseline preset.\n")
    env.p(BASELINE_SUMMARY + "\n")
    rc = _apply(env, args, NEW_POLICY)
    if rc == 0 and not args.dry_run:
        env.p("")
        cmd_doctor(env, argparse.Namespace(json=False))
    return rc


def cmd_allow(env: Env, args: argparse.Namespace) -> int:
    pf = _load_policy_file(env)
    key = _scope_key(env, args, pf)
    target = _resolve_arg(env, args.path)
    access = Access.parse(args.access)
    pf.add_grant(key, _display(target, env.ctx.home), access, protect=args.protect)
    env.p(f"{_scope_name(key)}: adding a grant on {target}, access {access.value}")
    mkdir = None
    if args.create and not target.exists():
        env.p(f"{'Would create' if args.dry_run else 'Will create'} folder {target}")
        mkdir = lambda: target.mkdir(parents=True, exist_ok=True)
    return _apply(env, args, pf.text, before_commit=mkdir)


def _find_raw(env: Env, pf: PolicyFile, key: str | None, attr: str, target: Path) -> str | None:
    scope = pf.policy.everywhere if key is None else pf.policy.projects.get(key)
    if scope is None:
        return None
    for item in getattr(scope, attr):
        if target in expand_paths(item.path, _probe(env)):
            return item.path
    return None


def cmd_revoke(env: Env, args: argparse.Namespace) -> int:
    pf = _load_policy_file(env)
    key = _scope_key(env, args, pf)
    target = _resolve_arg(env, args.path)
    raw = _find_raw(env, pf, key, "write", target)
    if raw is None or not pf.remove_grant(key, raw):
        hint = " (try --everywhere)" if key is not None else ""
        raise GuardError(f"no grant for {target} in {_scope_name(key)}{hint}")
    env.p(f"{_scope_name(key)}: removing the grant on {target}")
    return _apply(env, args, pf.text)


def cmd_protect(env: Env, args: argparse.Namespace) -> int:
    pf = _load_policy_file(env)
    key = _scope_key(env, args, pf)
    target = _resolve_arg(env, args.path)
    raw = _find_raw(env, pf, key, "protected", target) or _display(target, env.ctx.home)
    ceiling = Access.parse(args.ceiling)
    pf.set_protected(key, raw, ceiling)
    env.p(f"{_scope_name(key)}: protecting {target}, ceiling {ceiling.value}")
    if key is not None:
        env.p("note: a project can lower a ceiling, never raise one: raise it with --everywhere")
    return _apply(env, args, pf.text)


# ---- low-level commands ---------------------------------------------------------------------------


def cmd_plan(env: Env, args: argparse.Namespace) -> int:
    args.dry_run = True
    return _apply(env, args, read_policy(env.ctx))


def cmd_apply(env: Env, args: argparse.Namespace) -> int:
    return _apply(env, args, read_policy(env.ctx))


def cmd_edit(env: Env, args: argparse.Namespace) -> int:
    original = read_policy(env.ctx)
    scratch = env.ctx.guard_home / ".policy.edit.toml"
    atomic_write(scratch, original)
    try:
        while True:
            if env.editor(scratch) != 0:
                raise GuardError("the editor exited with an error; nothing was changed")
            text = scratch.read_text(encoding="utf-8")
            try:
                build(env.ctx, parse_policy(text, str(env.ctx.policy_path)))
                break
            except GuardError as e:
                env.p(f"error: {e}")
                if not env.interactive or not env.confirm("Edit again?"):
                    env.p("Nothing was changed.")
                    return 1
        if text == original:
            env.p("No changes to the policy.")
        return _apply(env, args, text)
    finally:
        scratch.unlink(missing_ok=True)


def cmd_uninstall(env: Env, args: argparse.Namespace) -> int:
    env.p("Removing every settings entry guard owns, and the hook. policy.toml is kept.")
    return _apply(env, args, None, compiled=Compiled(files={}, hook={}))


def _preset_summary(scope) -> str:
    bits = []
    if scope.presets:
        bits.append("includes " + ", ".join(scope.presets))
    for label, n in (
        ("grants", len(scope.write)),
        ("protected", len(scope.protected)),
        ("locked", len(scope.locked)),
        ("read deny", len(scope.read_deny)),
        (
            "command rules",
            sum(map(len, (scope.commands.allow, scope.commands.ask, scope.commands.deny))),
        ),
        ("settings", len(scope.raw.settings)),
    ):
        if n:
            bits.append(f"{n} {label}")
    if scope.closed:
        bits.append(f"hook: closed paths {scope.closed.value}")
    return ", ".join(bits) or "empty"


def cmd_presets(env: Env, args: argparse.Namespace) -> int:
    presets = dict(shipped_presets())
    try:
        presets.update(parse_policy(read_policy(env.ctx)).presets)
    except GuardError:
        pass
    width = max(map(len, presets))
    for name, scope in presets.items():
        env.p(f"{name:<{width}}  {_preset_summary(scope)}")
    return 0


def cmd_preset_show(env: Env, args: argparse.Namespace) -> int:
    try:
        user = read_policy(env.ctx)
        extra = user[user.index("[presets.") :] if "[presets." in user else ""
    except GuardError:
        extra = ""
    text = f"version = 1\n[everywhere]\npresets = [{json.dumps(args.name)}]\n{extra}"
    built = build(env.ctx, text)
    env.p(f"preset {args.name}, compiled on this machine into ~/.claude/settings.json:")
    for entries in built.compiled.files.values():
        for e in entries:
            env.p(f"  {e.path}: {json.dumps(e.value, ensure_ascii=False)}")
    scope = built.compiled.hook["scopes"]["*"]
    if scope["protected"]:
        env.p("protected (ceiling):")
        for p in scope["protected"]:
            env.p(f"  {p['ceiling']:<7} {p['path']}")
    for w in built.compiled.warnings:
        env.p(f"warning: {w}")
    return 0


# ---- views ---------------------------------------------------------------------------------------


def cmd_status(env: Env, args: argparse.Namespace) -> int:
    from .views import status

    s = status.build_status(env.ctx, build(env.ctx, read_policy(env.ctx)), verbose=args.verbose)
    env.p(json.dumps(s, indent=2) if args.json else status.render(s).rstrip("\n"))
    return 0


def cmd_explain(env: Env, args: argparse.Namespace) -> int:
    from .views import explain

    e = (
        explain.explain_command(env.ctx, args.target)
        if args.command
        else explain.explain_path(env.ctx, args.target)
    )
    env.p(json.dumps(explain.to_json(e), indent=2) if args.json else explain.render(e).rstrip("\n"))
    return 0


def cmd_doctor(env: Env, args: argparse.Namespace) -> int:
    from .views import doctor

    try:
        built, error = build(env.ctx, read_policy(env.ctx)), None
    except GuardError as e:
        built, error = None, str(e)
    checks = doctor.run_doctor(env.ctx, built, error)
    if args.json:
        env.p(json.dumps([c.__dict__ for c in checks], indent=2))
    else:
        env.p(doctor.render(checks).rstrip("\n"))
    return 1 if any(c.level == "fail" for c in checks) else 0


# ---- propose and review -----------------------------------------------------------------------------


class _OpAction(argparse.Action):
    """Collect --set/--unset/--add/--remove in command-line order."""

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[override]
        ops = getattr(namespace, "ops", None) or []
        ops.append((self.dest, values))
        namespace.ops = ops


def cmd_propose(env: Env, args: argparse.Namespace) -> int:
    target = check_target(args.target, env.ctx.home)
    ops: list[Op] = [parse_op(kind, text) for kind, text in (args.ops or [])]
    path = propose(target, ops, args.reason, env.ctx.proposal_dirs()[0])
    env.p(f"Proposal saved: {path}")
    env.p(
        "Ask the user to run `guard review` in a separate terminal (not with `!`: it needs a "
        "keyboard). Nothing changes until they accept it."
    )
    return 0


def cmd_review(env: Env, args: argparse.Namespace) -> int:
    if not env.interactive or env.in_claude:
        raise GuardError(
            "guard review needs you at a keyboard: run it in a separate terminal, not from Claude "
            "or with `!` (VB-P7)"
        )
    pending = list_proposals(env.ctx.proposal_dirs())
    if not pending:
        env.p("No pending proposals.")
        return 0
    applied = load_applied(env.ctx.guard_home)
    accepted = 0
    for path, prop in pending:
        env.p(f"\n=== Proposal {prop.id} ({prop.created})")
        env.p(f"target: {prop.target}")
        env.p(f"reason: {prop.reason}")
        for op in prop.ops:
            env.p(f"  {op.describe()}")
        try:
            r = review(prop, applied, env.ctx.home)
        except GuardError as e:
            env.p(f"cannot apply: {e}")
            continue
        env.p(r.diff or "(no change to the file)")
        if r.notes:
            env.p("What this changes:")
            for n in r.notes:
                env.p(f"  - {n}")
        if r.refusals:
            env.p("Refused:")
            for n in r.refusals:
                env.p(f"  - {n}")
            continue
        if env.confirm("Accept this change?"):
            accept(r, path, env.ctx.guard_home)
            accepted += 1
            env.p("Accepted.")
        elif env.confirm("Delete this proposal?"):
            path.unlink(missing_ok=True)
            env.p("Rejected and deleted.")
        else:
            env.p("Left pending.")
    if accepted and env.ctx.policy_path.exists():
        env.p("\nRe-applying the policy, so any newly referenced scripts are protected:")
        return _apply(env, argparse.Namespace(yes=False, dry_run=False), read_policy(env.ctx))
    return 0


def cmd_hook_edit(env: Env, args: argparse.Namespace) -> int:
    from .hook import run_hook

    text = run_hook(env.stdin.read(), Path(args.guard_home))
    if text:
        env.p(text)
    return 0


# ---- parser ----------------------------------------------------------------------------------------

ACCESS = [a.value for a in Access]


def _changes(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--dry-run", action="store_true", help="show the diff; change nothing")
    sp.add_argument("--yes", "-y", action="store_true", help="apply without asking")


def _scoped(sp: argparse.ArgumentParser) -> None:
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--everywhere", action="store_true", help="every session ([everywhere])")
    g.add_argument("--project", metavar="DIR", help="that project (default: the current one)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="guard",
        description="Keep Claude Code's writes where they belong, from one policy file.",
    )
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")

    s = sub.add_parser("init", help="create policy.toml with the baseline and apply it")
    _changes(s)
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("allow", help="grant access to a folder")
    s.add_argument("path")
    s.add_argument("--access", choices=ACCESS, default="full", help="default: full")
    s.add_argument(
        "--protect",
        action="append",
        default=[],
        metavar="GLOB",
        help="folder-relative path the Edit/Write tools may never write (repeatable)",
    )
    s.add_argument("--create", action="store_true", help="create the folder if it does not exist")
    _scoped(s)
    _changes(s)
    s.set_defaults(fn=cmd_allow)

    s = sub.add_parser("revoke", help="remove a grant")
    s.add_argument("path")
    _scoped(s)
    _changes(s)
    s.set_defaults(fn=cmd_revoke)

    s = sub.add_parser("protect", help="protect a path, or change its ceiling")
    s.add_argument("path")
    s.add_argument("--ceiling", choices=ACCESS, required=True)
    _scoped(s)
    _changes(s)
    s.set_defaults(fn=cmd_protect)

    s = sub.add_parser("status", help="what is in force, per scope")
    s.add_argument("--verbose", "-v", action="store_true", help="also the raw settings entries")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("explain", help="what would happen to a path or a Bash command")
    s.add_argument("target", help="a path, or a command with --command")
    s.add_argument("--command", action="store_true", help="TARGET is a Bash command")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_explain)

    s = sub.add_parser("doctor", help="health checks; exit 1 on failure")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("propose", help="for Claude: queue a settings change for review")
    s.add_argument("target", help="settings file to change")
    for kind, meta in (
        ("set", "KEY=VALUE"),
        ("unset", "KEY"),
        ("add", "KEY=VALUE"),
        ("remove", "KEY=VALUE"),
    ):
        s.add_argument(f"--{kind}", dest=kind, action=_OpAction, metavar=meta)
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_propose, ops=None)

    s = sub.add_parser("review", help="for you, in a terminal: accept or reject proposals")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("edit", help="edit policy.toml in $EDITOR, then plan and apply")
    _changes(s)
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("plan", help="show what apply would change")
    s.set_defaults(fn=cmd_plan, yes=False)

    s = sub.add_parser("apply", help="make the settings match the policy")
    _changes(s)
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("presets", help="list presets")
    s.set_defaults(fn=cmd_presets)
    s = sub.add_parser("preset", help="preset details")
    ps = s.add_subparsers(dest="preset_command", required=True)
    show = ps.add_parser("show", help="what a preset compiles to on this machine")
    show.add_argument("name")
    show.set_defaults(fn=cmd_preset_show)

    s = sub.add_parser("uninstall", help="remove every entry guard owns, and the hook")
    _changes(s)
    s.set_defaults(fn=cmd_uninstall)

    s = sub.add_parser("hook", help="run by Claude Code (not for manual use)")
    hs = s.add_subparsers(dest="hook_command", required=True)
    he = hs.add_parser("edit", help="PreToolUse hook for the Edit/Write tools (reads stdin)")
    he.add_argument("--guard-home", required=True)
    he.set_defaults(fn=cmd_hook_edit)
    return ap


def main(argv: Sequence[str] | None = None, env: Env | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.fn is cmd_hook_edit:  # the hook must not fall into the error handler below: a crash
        return cmd_hook_edit(env or default_env(), args)  # blocks the write (VB-H3)
    env = env or default_env()
    try:
        return args.fn(env, args)
    except GuardError as e:
        print(f"error: {e}", file=env.err)
        return 2
