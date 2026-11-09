"""Every Claude Code settings source for a project, in precedence order (VB-P1).

managed > project local > project shared > user local > user. Arrays merge across sources; for a
scalar the first source that sets it wins. Read-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def dig(data: Any, dotted: str) -> tuple[bool, Any]:
    node = data
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
    return True, node


@dataclass
class Source:
    label: str  # "managed", "local", "project", "user local", "user"
    path: Path
    data: dict[str, Any] | None = None  # None: missing or unreadable
    error: str | None = None


@dataclass
class Effective:
    sources: list[Source] = field(default_factory=list)

    @property
    def errors(self) -> list[Source]:
        return [s for s in self.sources if s.error]

    def present(self) -> list[Source]:
        return [s for s in self.sources if s.data is not None]

    def scalar(self, dotted: str) -> tuple[bool, Any, str | None]:
        for src in self.present():
            found, val = dig(src.data, dotted)
            if found:
                return True, val, src.label
        return False, None, None

    def array(self, dotted: str) -> list[tuple[Any, Source]]:
        out: list[tuple[Any, Source]] = []
        for src in self.present():
            found, val = dig(src.data, dotted)
            if found and isinstance(val, list):
                out.extend((v, src) for v in val)
        return out

    def strings(self, dotted: str) -> list[tuple[str, Source]]:
        return [(v, s) for v, s in self.array(dotted) if isinstance(v, str)]


def read_source(label: str, path: Path) -> Source:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Source(label, path)
    except OSError as e:
        return Source(label, path, error=str(e))
    if not text.strip():
        return Source(label, path, data={})
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return Source(label, path, error=f"invalid JSON: {e}")
    if not isinstance(data, dict):
        return Source(label, path, error="top level is not an object")
    return Source(label, path, data=data)


def managed_files(managed_dir: Path) -> list[Path]:
    files = [managed_dir / "managed-settings.json"]
    drop_in = managed_dir / "managed-settings.d"
    if drop_in.is_dir():
        files = sorted(drop_in.glob("*.json"), reverse=True) + files
    return files


def load_effective(home: Path, project: Path | None, managed_dir: Path) -> Effective:
    srcs = [read_source("managed", p) for p in managed_files(managed_dir)]
    if project is not None:
        srcs.append(read_source("local", project / ".claude" / "settings.local.json"))
        srcs.append(read_source("project", project / ".claude" / "settings.json"))
    srcs.append(read_source("user local", home / ".claude" / "settings.local.json"))
    srcs.append(read_source("user", home / ".claude" / "settings.json"))
    return Effective(srcs)
