"""The state file: exactly which settings entries each grant added.

Lives in the state dir (default ``~/.config/claude-guard/state.json``), outside anything Claude can
write. Format (version 1)::

    {
      "version": 1,
      "grants": {
        "<folder>|<settings file>": {            # folder is "*" for folder-less grants
          "folder": "/mnt/d/x" | null,
          "settings_file": "/abs/.claude/settings.local.json",
          "profiles": ["edit", ...],
          "protect": ["glob", ...],               # extra --protect globs
          "created_at": "ISO-8601",
          "entries": [[<settings file>, <entry key>], ...]   # everything this grant references
        }
      },
      "files": {
        "<settings file>": {
          "file_created": true,    # file did not exist before we wrote it; deleted when emptied
          "created": ["permissions", "permissions.allow"],   # containers we created (pruned if empty)
          "entries": {
            "<entry key>": {
              "kind": "array" | "scalar",
              "path": "permissions.allow",         # dotted key path
              "value": "Edit(//mnt/d/x/**)",
              "refs": ["<grant id>", ...],          # reference count: removed when empty
              "preexisting": false,                 # array entry already present: never removed
              "prior": {"present": bool, "value": ...}   # scalars: value to restore
            }
          }
        }
      }
    }
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .applied import atomic_write, backup_file, timestamp
from .errors import StateError

__all__ = ["atomic_write", "backup_file", "timestamp"]

STATE_VERSION = 1


def empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "grants": {}, "files": {}}


def load_state(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_state()
    try:
        state = json.loads(text)
    except json.JSONDecodeError as e:
        raise StateError(f"{path}: invalid JSON ({e}); refusing to continue") from e
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        raise StateError(f"{path}: unsupported state format (expected version {STATE_VERSION})")
    for key in ("grants", "files"):
        if not isinstance(state.get(key), dict):
            raise StateError(f"{path}: missing or invalid {key!r}")
    return state


def clone(state: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(state)


def save_state(path: Path, state: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")
