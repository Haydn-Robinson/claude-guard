"""Permission-rule and sandbox path forms, and matching paths against rule patterns.

Two path conventions (VB-R4):

* permission *rule* paths: ``//abs/path`` is absolute (``/x`` would be relative to the settings
  source, a trap, so it is never emitted);
* *sandbox* paths: ``/abs/path`` is absolute.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

GLOB_CHARS = re.compile(r"[*?\[\]]")


def rule_path(path: Path) -> str:
    """Absolute path in permission-rule form: ``//mnt/d/x``."""
    return "/" + str(path)


def sandbox_path(path: Path) -> str:
    """Absolute path in sandbox form: ``/mnt/d/x``."""
    return str(path)


def has_glob(text: str) -> bool:
    return bool(GLOB_CHARS.search(text))


def concrete_patterns(path: Path, folder: bool = False) -> list[str]:
    """The rule pattern (without the tool name) covering one concrete path.

    A folder is covered by ``//p/**``, anything else by ``//p``. ``folder`` says it is a folder (a
    policy path written with a trailing ``/``); otherwise an existing folder is detected. One form
    only: the meaning of ``//p`` for a folder's contents is unknown (VB-U11).
    """
    rp = rule_path(path)
    return [f"{rp}/**"] if folder or path.is_dir() else [rp]


def pattern_rules(pattern: str) -> list[str]:
    """Rule patterns for a policy pattern: an absolute path (maybe with globs, maybe ending in
    ``/`` for a folder) or a ``**/`` glob."""
    if pattern.startswith("**/"):
        return ["//" + pattern]
    if has_glob(pattern):
        return ["/" + pattern]
    return concrete_patterns(Path(pattern), folder=pattern.endswith("/"))


@lru_cache(maxsize=256)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        else:
            ch = pattern[i]
            out.append({"*": "[^/]*", "?": "[^/]"}.get(ch) or re.escape(ch))
            i += 1
    return re.compile("".join(out))


def glob_matches(pattern: str, path: Path) -> bool:
    """Does an absolute path match a policy pattern? ``**/x`` matches ``x`` at any depth, ``*``
    stays within one path segment, and a pattern naming a folder matches everything inside it."""
    text = str(path)
    if pattern.startswith("**/"):
        rx = _compile_glob(pattern[3:])
        parts = text.strip("/").split("/")
        return any(
            rx.fullmatch("/".join(parts[i:j]))
            for i in range(len(parts))
            for j in range(i + 1, len(parts) + 1)
        )
    rx = _compile_glob(pattern.rstrip("/"))
    return bool(rx.fullmatch(text)) or any(rx.fullmatch(str(a)) for a in path.parents)


def is_within(path: Path, folder: Path) -> bool:
    """``path`` is ``folder`` or inside it."""
    return path == folder or folder in path.parents


def find_project(cwd: Path, home: Path) -> Path | None:
    """The git root containing ``cwd``, or ``cwd`` itself outside a repository. A folder at or
    above the home folder is no project (design 7.1, 8)."""
    here = Path(os.path.realpath(cwd))
    root = next((d for d in (here, *here.parents) if (d / ".git").exists()), here)
    if is_within(Path(os.path.realpath(home)), root):
        return None
    return root


def bash_match(spec: str, command: str) -> bool:
    """Match a ``Bash(spec)`` pattern against a command. ``cmd *`` also matches bare ``cmd``
    (VB-R1); a rule without ``*`` matches only the exact command (VB-R2)."""
    spec = spec.strip()
    if spec.endswith(":*"):
        spec = spec[:-2] + " *"
    trailing = spec.endswith(" *")
    body = spec[:-2] if trailing else spec
    rx = ".*".join(re.escape(part) for part in body.split("*"))
    if trailing:
        rx += "(?: .*)?"
    return re.fullmatch(rx, command.strip(), re.DOTALL) is not None
