"""Facts about this machine that the policy compiles against: drives, caches, the scripts that
settings run, the project, and how Claude Code should run ``guard``."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .effective import Effective, dig
from .model import Platform
from .pathsafety import DEFAULT_SYSTEM_DRIVE, detect_drive_roots
from .rules import find_project, is_within

__all__ = ["find_project"]

# Each tool's own answer to "where is your cache?" (VB-S16): never a hard-coded ~/.cache.
CACHE_QUERIES = (
    ("uv", "cache", "dir"),
    ("npm", "config", "get", "cache"),
    ("pip", "cache", "dir"),
    ("go", "env", "GOCACHE"),
    ("go", "env", "GOMODCACHE"),
)

Runner = Callable[[tuple[str, ...]], str | None]


def default_guard_home() -> Path:
    if os.environ.get("CLAUDE_GUARD_HOME"):
        return Path(os.environ["CLAUDE_GUARD_HOME"])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claude-guard"


def is_wsl(osrelease: Path = Path("/proc/sys/kernel/osrelease")) -> bool:
    try:
        return "microsoft" in osrelease.read_text().lower()
    except OSError:
        return False


def _run(cmd: tuple[str, ...]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except OSError, subprocess.SubprocessError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def detect_caches(home: Path, run: Runner = _run) -> tuple[Path, ...]:
    """Existing cache folders the tools report, below the home folder or deep enough to be safe."""
    out: dict[Path, None] = {}
    for query in CACHE_QUERIES:
        text = run(query)
        if not text:
            continue
        p = Path(os.path.realpath(text.splitlines()[0].strip()))
        if p.is_absolute() and p.is_dir() and len(p.parts) > 3 and not is_within(home, p):
            out[p] = None
    return tuple(out)


def _commands(data: dict[str, Any]) -> Iterable[str]:
    for key in ("apiKeyHelper", "statusLine.command", "awsAuthRefresh", "awsCredentialExport"):
        found, val = dig(data, key)
        if found and isinstance(val, str):
            yield val
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for groups in hooks.values():
            for group in groups if isinstance(groups, list) else []:
                for h in group.get("hooks", []) if isinstance(group, dict) else []:
                    if isinstance(h, dict) and isinstance(h.get("command"), str):
                        yield shlex.join([h["command"], *map(str, h.get("args", []))])


def settings_scripts(effectives: Iterable[Effective], home: Path) -> tuple[Path, ...]:
    """Files that settings run (hooks, statusLine, apiKeyHelper). They run outside the sandbox, so
    editing one is as powerful as editing the settings (design 7.2)."""
    out: dict[Path, None] = {}
    for eff in effectives:
        for src in eff.present():
            project = src.path.parent.parent if src.label in ("local", "project") else None
            for cmd in _commands(src.data or {}):
                try:
                    tokens = shlex.split(cmd)
                except ValueError:
                    tokens = cmd.split()
                for tok in tokens:
                    tok = tok.replace("$HOME", str(home)).replace("${HOME}", str(home))
                    if project is not None:
                        for var in ("$CLAUDE_PROJECT_DIR", "${CLAUDE_PROJECT_DIR}"):
                            tok = tok.replace(var, str(project))
                    if tok.startswith("~/"):
                        tok = str(home) + tok[1:]
                    if not tok.startswith("/"):
                        continue
                    p = Path(os.path.realpath(tok))
                    if p.is_file():
                        out[p] = None
    return tuple(out)


def hook_command() -> tuple[str, ...]:
    """An absolute path, so the hook does not depend on PATH (VB-H6)."""
    found = shutil.which("guard")
    if found:
        return (str(Path(found).absolute()),)
    return (sys.executable, "-m", "claude_guard")


def detect(
    home: Path,
    guard_home: Path,
    scripts: tuple[Path, ...] = (),
    *,
    run: Runner = _run,
    managed: Path = Path("/etc/claude-code"),
    hook: tuple[str, ...] | None = None,
    wsl: bool | None = None,
) -> Platform:
    drives: tuple[Path, ...] = ()
    system: Path | None = None
    if is_wsl() if wsl is None else wsl:
        drives = tuple(sorted(detect_drive_roots()))
        system = DEFAULT_SYSTEM_DRIVE if DEFAULT_SYSTEM_DRIVE in drives else None
    return Platform(
        home=home,
        guard_home=guard_home,
        hook_command=hook or hook_command(),
        system_drive=system,
        drives=drives,
        caches=detect_caches(home, run),
        settings_scripts=scripts,
        managed_settings=managed,
    )
