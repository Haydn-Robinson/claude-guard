"""The pipeline the CLI and the views share: policy -> platform facts -> resolved -> compiled.

``Ctx`` holds everything that depends on the machine (home, current folder, guard's folder, how to
ask tools for their caches), so tests can run the whole tool against a temporary home.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .compile import compile_policy
from .effective import Effective, load_effective
from .errors import PolicyError
from .machine import Runner, _run, detect, hook_command, settings_scripts
from .model import Compiled, Platform, Policy
from .policy import Resolved, parse_policy, resolve, resolve_project_dir
from .rules import find_project

POLICY_FILE = "policy.toml"


@dataclass
class Ctx:
    home: Path
    cwd: Path
    guard_home: Path
    managed: Path = Path("/etc/claude-code")
    hook: tuple[str, ...] | None = None  # how settings should run guard (default: detected)
    run: Runner = _run  # asks tools for their cache folders
    which: Callable[[str], str | None] = shutil.which
    wsl: bool | None = None  # None: detect
    queue: list[Path] | None = None  # where proposals wait (default: proposals.proposal_dirs())
    _hook: tuple[str, ...] | None = field(default=None, repr=False)

    @property
    def policy_path(self) -> Path:
        return self.guard_home / POLICY_FILE

    @property
    def project(self) -> Path | None:
        return find_project(self.cwd, self.home)

    def hook_command(self) -> tuple[str, ...]:
        if self._hook is None:
            self._hook = self.hook or hook_command()
        return self._hook

    def proposal_dirs(self) -> list[Path]:
        from .proposals import proposal_dirs

        return self.queue if self.queue is not None else proposal_dirs()

    def effective(self, project: Path | None = None) -> Effective:
        return load_effective(self.home, project, self.managed)


@dataclass
class Built:
    policy: Policy
    platform: Platform
    resolved: Resolved
    compiled: Compiled


def read_policy(ctx: Ctx) -> str:
    try:
        return ctx.policy_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PolicyError(f"{ctx.policy_path} does not exist: run `guard init`") from None


def build(ctx: Ctx, policy: Policy | str) -> Built:
    if isinstance(policy, str):
        policy = parse_policy(policy, str(ctx.policy_path))
    probe = Platform(home=ctx.home, guard_home=ctx.guard_home, hook_command=())
    # Only projects named in the policy: the result must not depend on where guard is run.
    projects = {resolve_project_dir(raw, probe) for raw in policy.projects}
    effectives = [ctx.effective(p) for p in sorted(projects)] or [ctx.effective(None)]
    hook = ctx.hook_command()
    # guard's own executable is listed whether or not the hook is registered yet, so the result
    # does not change between the first and second apply.
    exe = [Path(os.path.realpath(hook[0]))] if Path(hook[0]).is_file() else []
    scripts = tuple(dict.fromkeys([*settings_scripts(effectives, ctx.home), *exe]))
    plat = detect(
        ctx.home, ctx.guard_home, scripts, run=ctx.run, managed=ctx.managed, hook=hook, wsl=ctx.wsl
    )
    resolved = resolve(policy, plat)
    return Built(policy, plat, resolved, compile_policy(resolved, plat))
