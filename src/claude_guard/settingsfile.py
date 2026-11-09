"""A Claude Code settings JSON file, edited in memory without clobbering anything.

Unknown keys and key order are preserved. Formatting (indent, trailing newline) is detected and
reused, so a file already in canonical ``json.dumps(indent=N)`` style round-trips byte-identically.
A file in some other style is normalised on first write (``reformats`` is True in that case).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import SettingsError

_MISSING = object()


def _detect_indent(text: str) -> str:
    m = re.search(r"^([ \t]+)\S", text, flags=re.MULTILINE)
    if not m:
        return "  "
    return "\t" if m.group(1).startswith("\t") else " " * len(m.group(1))


class SettingsDoc:
    def __init__(self, path: Path, original_text: str | None):
        self.path = path
        self.original_text = original_text
        self.exists = original_text is not None
        self.delete = False
        text = original_text or ""
        if text.strip():
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise SettingsError(f"{path}: invalid JSON ({e}); refusing to touch it") from e
            if not isinstance(data, dict):
                raise SettingsError(f"{path}: top level is not a JSON object")
        else:
            data = {}
        self.data: dict[str, Any] = data
        self.indent = _detect_indent(text)
        self.trailing_newline = text.endswith("\n") if text.strip() else True

    @classmethod
    def load(cls, path: Path) -> SettingsDoc:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(path, None)
        except OSError as e:
            raise SettingsError(f"{path}: cannot read: {e}") from e
        return cls(path, text)

    # ---- rendering -------------------------------------------------------------------------
    def render(self) -> str:
        text = json.dumps(self.data, indent=self.indent, ensure_ascii=False)
        return text + ("\n" if self.trailing_newline else "")

    @property
    def reformats(self) -> bool:
        """True if writing this file back unchanged would alter its formatting."""
        if not self.original_text or not self.original_text.strip():
            return False
        pristine = json.dumps(
            json.loads(self.original_text), indent=self.indent, ensure_ascii=False
        )
        pristine += "\n" if self.trailing_newline else ""
        return pristine != self.original_text

    # ---- navigation ------------------------------------------------------------------------
    def _walk(self, keys: list[str], create: bool) -> tuple[dict[str, Any] | None, list[str]]:
        node: dict[str, Any] = self.data
        created: list[str] = []
        for i, key in enumerate(keys):
            nxt = node.get(key, _MISSING)
            if nxt is _MISSING:
                if not create:
                    return None, created
                nxt = {}
                node[key] = nxt
                created.append(".".join(keys[: i + 1]))
            elif not isinstance(nxt, dict):
                raise SettingsError(
                    f"{self.path}: expected an object at {'.'.join(keys[: i + 1])!r}, "
                    f"found {type(nxt).__name__}"
                )
            node = nxt
        return node, created

    # ---- arrays ----------------------------------------------------------------------------
    def array_contains(self, path: str, value: Any) -> bool:
        *parents, leaf = path.split(".")
        node, _ = self._walk(parents, create=False)
        arr = node.get(leaf) if node is not None else None
        if arr is not None and not isinstance(arr, list):
            raise SettingsError(f"{self.path}: {path!r} is not an array")
        return arr is not None and value in arr

    def add_to_array(self, path: str, value: Any) -> list[str]:
        """Append ``value`` if absent. Returns dotted paths of containers this call created."""
        *parents, leaf = path.split(".")
        node, created = self._walk(parents, create=True)
        assert node is not None
        arr = node.get(leaf, _MISSING)
        if arr is _MISSING:
            arr = []
            node[leaf] = arr
            created.append(path)
        elif not isinstance(arr, list):
            raise SettingsError(f"{self.path}: {path!r} is not an array")
        if value not in arr:
            arr.append(value)
        return created

    def remove_from_array(self, path: str, value: Any) -> bool:
        *parents, leaf = path.split(".")
        node, _ = self._walk(parents, create=False)
        arr = node.get(leaf) if node is not None else None
        if not isinstance(arr, list) or value not in arr:
            return False
        arr.remove(value)
        return True

    # ---- scalars ---------------------------------------------------------------------------
    def get_scalar(self, path: str) -> tuple[bool, Any]:
        *parents, leaf = path.split(".")
        node, _ = self._walk(parents, create=False)
        if node is None or leaf not in node:
            return False, None
        return True, node[leaf]

    def set_scalar(self, path: str, value: Any) -> list[str]:
        *parents, leaf = path.split(".")
        node, created = self._walk(parents, create=True)
        assert node is not None
        node[leaf] = value
        return created

    def delete_scalar(self, path: str) -> None:
        *parents, leaf = path.split(".")
        node, _ = self._walk(parents, create=False)
        if node is not None:
            node.pop(leaf, None)

    # ---- pruning ---------------------------------------------------------------------------
    def prune(self, created: set[str]) -> set[str]:
        """Delete containers we created that are now empty (deepest first). Returns those removed."""
        removed: set[str] = set()
        for path in sorted(created, key=lambda p: p.count("."), reverse=True):
            *parents, leaf = path.split(".")
            node, _ = self._walk(parents, create=False)
            if node is None:
                removed.add(path)  # already gone
                continue
            val = node.get(leaf, _MISSING)
            if val is _MISSING:
                removed.add(path)
            elif isinstance(val, (list, dict)) and not val:
                del node[leaf]
                removed.add(path)
        return removed
