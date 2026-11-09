"""claude-guard: keep Claude Code's writes where they belong, from one policy file."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:2] == ["hook", "edit"]:
        # Fast path: runs on every Edit/Write, so skip the CLI, compiler and TOML imports. Any
        # error propagates and exits non-zero, which blocks the write (onFailure: block, VB-H3).
        return _hook_main(args[2:])
    from .cli import main as cli_main

    return cli_main(args)


def _hook_main(args: list[str]) -> int:
    from pathlib import Path

    from .hook import run_hook

    if len(args) != 2 or args[0] != "--guard-home":
        raise SystemExit("usage: guard hook edit --guard-home DIR")
    text = run_hook(sys.stdin.read(), Path(args[1]))
    if text:
        print(text)
    return 0


__all__ = ["main"]
