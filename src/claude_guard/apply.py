"""Plan and commit: reconcile compiled entries with the settings files and applied.json.

``plan`` reads files and writes nothing. For each settings file guard targets now or owned before
(design section 10):

* desired and already present but not owned: record as pre-existing, never remove;
* desired and missing: add (if guard owned it, it was removed by hand: say so);
* owned and no longer desired: remove (a pre-existing one is only forgotten);
* owned scalar changed by hand: restore the policy value (say so); on removal, restore the prior
  value only if the current value is still guard's;
* prune containers guard created that are now empty; delete a file guard created that is now empty.

``commit`` writes the plan under a lock: refuses if a file changed since the plan, validates the
JSON, backs up every changed file, writes atomically, then writes applied.json, and puts the
settings files back if anything fails.
"""

from __future__ import annotations

import copy
import difflib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import applied as applied_mod
from .applied import BACKUP_DIR, Applied, FileRecord, Owned, atomic_write, backup_file, lock
from .errors import GuardError, SettingsError
from .model import Compiled, Entry
from .settingsfile import SettingsDoc


@dataclass
class FileChange:
    path: Path
    old_text: str | None  # None: the file does not exist
    new_text: str | None  # None: delete the file


@dataclass
class Plan:
    changes: list[FileChange]
    old: Applied
    new: Applied
    notes: list[str] = field(default_factory=list)  # drift: hand edits found and what happens
    warnings: list[str] = field(default_factory=list)  # from the compiler

    @property
    def noop(self) -> bool:
        return not self.changes and self.old == self.new

    def diff(self) -> str:
        out: list[str] = []
        for ch in self.changes:
            out.extend(
                difflib.unified_diff(
                    (ch.old_text or "").splitlines(keepends=True),
                    (ch.new_text or "").splitlines(keepends=True),
                    fromfile=str(ch.path) if ch.old_text is not None else "/dev/null",
                    tofile=str(ch.path) if ch.new_text is not None else "/dev/null",
                )
            )
        return "".join(out)


def _show(e: Entry) -> str:
    v = json.dumps(e.value, ensure_ascii=False)
    return f"{e.path} = {v}" if e.kind == "scalar" else f"{e.path}: {v}"


def _plan_file(
    path: Path, desired: list[Entry], rec: FileRecord | None, notes: list[str]
) -> tuple[FileChange | None, FileRecord | None]:
    doc = SettingsDoc.load(path)
    before = copy.deepcopy(doc.data)
    rec = rec or FileRecord(file_created=not doc.exists)
    created = list(rec.created)
    owned = list(rec.entries)
    kept: list[Owned] = []

    def note(msg: str) -> None:
        notes.append(f"{path}: {msg}")

    def track(new: list[str]) -> None:
        created.extend(c for c in new if c not in created)

    for d in desired:
        o = next((x for x in owned if x.entry.same_slot(d)), None)
        if o is not None:
            owned.remove(o)
        if d.kind == "array":
            if doc.array_contains(d.path, d.value):
                kept.append(o or Owned(d, preexisting=True))
            else:
                if o is not None:
                    note(f"{_show(d)} was removed by hand; restoring it")
                track(doc.add_to_array(d.path, d.value))
                kept.append(Owned(d))
        else:
            present, current = doc.get_scalar(d.path)
            if o is None:
                prior = (present, current)
            else:
                prior = o.prior if o.prior is not None else (False, None)
                if not present or current != o.entry.value:
                    note(f"{d.path} was changed by hand; setting it to {json.dumps(d.value)}")
            if not present or current != d.value:
                track(doc.set_scalar(d.path, d.value))
            kept.append(Owned(d, prior=prior))

    for o in owned:  # owned, no longer desired
        e = o.entry
        if e.kind == "array":
            if not o.preexisting and not doc.remove_from_array(e.path, e.value):
                note(f"{_show(e)} was already removed by hand")
        else:
            present, current = doc.get_scalar(e.path)
            if present and current == e.value:
                was_present, prior_value = o.prior or (False, None)
                if was_present:
                    doc.set_scalar(e.path, prior_value)
                else:
                    doc.delete_scalar(e.path)
            else:
                note(f"{e.path} was changed by hand; leaving it as it is")

    pruned = doc.prune(set(created))
    created = [c for c in created if c not in pruned]
    new_rec = FileRecord(rec.file_created, created, kept) if kept else None

    if new_rec is None and rec.file_created and not doc.data:
        change = FileChange(path, doc.original_text, None) if doc.exists else None
    elif doc.data == before:
        change = None  # nothing to write: leave the file's formatting alone
    else:
        change = FileChange(path, doc.original_text, doc.render())
    return change, new_rec


def plan(compiled: Compiled, applied: Applied) -> Plan:
    targets = [*compiled.files, *(Path(f) for f in applied.files)]
    changes: list[FileChange] = []
    notes: list[str] = []
    new = Applied(files={}, hook=compiled.hook)
    for path in dict.fromkeys(targets):
        change, rec = _plan_file(
            path, compiled.files.get(path, []), applied.files.get(str(path)), notes
        )
        if change is not None:
            changes.append(change)
        if rec is not None:
            new.files[str(path)] = rec
    return Plan(changes, applied, new, notes, list(compiled.warnings))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _restore(ch: FileChange) -> None:
    if ch.old_text is None:
        ch.path.unlink(missing_ok=True)
    else:
        atomic_write(ch.path, ch.old_text)


def commit(p: Plan, guard_home: Path, *, now: datetime | None = None) -> list[Path]:
    """Write the plan. Returns the backups made."""
    if p.noop:
        return []
    with lock(guard_home):
        for ch in p.changes:
            if _read(ch.path) != ch.old_text:
                raise GuardError(f"{ch.path} changed since the plan was made; run it again")
            if ch.new_text is not None:
                try:
                    json.loads(ch.new_text)
                except json.JSONDecodeError as e:  # pragma: no cover - defensive
                    raise SettingsError(f"refusing to write invalid JSON to {ch.path}: {e}") from e
        backups: list[Path] = []
        done: list[FileChange] = []
        try:
            for ch in p.changes:
                b = backup_file(ch.path, guard_home / BACKUP_DIR, now)
                if b:
                    backups.append(b)
                if ch.new_text is None:
                    ch.path.unlink()
                else:
                    atomic_write(ch.path, ch.new_text)
                done.append(ch)
            applied_mod.save_applied(guard_home, p.new)
        except BaseException:
            for ch in reversed(done):
                _restore(ch)
            raise
    return backups
