"""Grant / revoke planning and committing.

Everything is *declarative*: a grant is (folder, settings file, profiles, extra protect globs). From
that we compute the full list of settings entries it wants ("wants"), then reconcile against what the
grant previously contributed (recorded in the state file). Each settings entry is reference counted
by grant, so shared entries survive until the last grant using them is revoked, and entries that
already existed in the file before we touched it (``preexisting``) are never removed.

``plan_*`` are pure (they read files but write nothing); ``commit`` performs the writes.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import GuardError, SettingsError
from .hook import HOOK_MATCHER
from .paths import PathPolicy, rule_path, sandbox_path
from .profiles import ProfileSet
from .settingsfile import SettingsDoc
from .state import atomic_write, backup_file, clone, empty_state, load_state, save_state

ALLOW_WRITE = "sandbox.filesystem.allowWrite"
# denyWrite beats every allow and has no sub-path exception (VB-S8); the plain folder form is used (VB-S9)
DENY_WRITE = "sandbox.filesystem.denyWrite"
HOOKS_PRE = "hooks.PreToolUse"
PERM_ALLOW, PERM_ASK, PERM_DENY = "permissions.allow", "permissions.ask", "permissions.deny"


@dataclass(frozen=True)
class Want:
    file: str
    kind: str  # "array" | "scalar"
    path: str
    value: Any

    @property
    def key(self) -> str:
        return json.dumps(
            [self.kind, self.path, self.value if self.kind == "array" else None], sort_keys=True
        )


@dataclass
class FileChange:
    path: Path
    old_text: str | None
    new_text: str | None  # None = delete the file
    reformats: bool = False


@dataclass
class Plan:
    changes: list[FileChange]
    old_state: dict[str, Any]
    new_state: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def noop(self) -> bool:
        return not self.changes and self.old_state == self.new_state

    def diff(self) -> str:
        out = []
        for ch in self.changes:
            old = (ch.old_text or "").splitlines(keepends=True)
            new = (ch.new_text or "").splitlines(keepends=True)
            out.extend(
                difflib.unified_diff(
                    old,
                    new,
                    fromfile=str(ch.path) if ch.old_text is not None else "/dev/null",
                    tofile=str(ch.path) if ch.new_text is not None else "/dev/null",
                )
            )
        return "".join(out)


class Session:
    """Accumulates several grant/revoke operations into one Plan (one diff, one confirmation)."""

    def __init__(self, old_state: dict[str, Any]):
        self.old_state = old_state
        self.state = clone(old_state)
        self.docs: dict[str, SettingsDoc] = {}
        self.warnings: list[str] = []

    def doc_for(self, file: str) -> SettingsDoc:
        if file not in self.docs:
            self.docs[file] = SettingsDoc.load(Path(file))
        return self.docs[file]

    def plan(self) -> Plan:
        changes = []
        for file, doc in self.docs.items():
            new_text = None if doc.delete else doc.render()
            if doc.delete and not doc.exists:
                continue
            if not doc.delete and new_text == doc.original_text:
                continue
            if not doc.exists and not doc.data:
                continue
            changes.append(FileChange(Path(file), doc.original_text, new_text, doc.reformats))
        return Plan(changes, self.old_state, self.state, self.warnings)


def grant_id(folder: Path | None, settings_file: Path) -> str:
    return f"{folder if folder is not None else '*'}|{settings_file}"


class Guard:
    def __init__(
        self,
        state_dir: Path,
        profiles: ProfileSet,
        policy: PathPolicy,
        hook_command: list[str] | None = None,
    ):
        self.hook_command = hook_command
        self.state_dir = state_dir
        self.state_file = state_dir / "state.json"
        self.backup_dir = state_dir / "backups"
        self.profiles = profiles
        self.policy = policy

    # ---- planning --------------------------------------------------------------------------
    def new_session(self) -> Session:
        return Session(load_state(self.state_file))

    def plan_allow(
        self,
        folder: str | Path | None,
        profile_names: list[str],
        settings_file: Path,
        protect: list[str] | None = None,
        *,
        force_broad: bool = False,
        now: datetime | None = None,
    ) -> Plan:
        session = self.new_session()
        self.allow(
            session, folder, profile_names, settings_file, protect, force_broad=force_broad, now=now
        )
        return session.plan()

    def allow(
        self,
        session: Session,
        folder: str | Path | None,
        profile_names: list[str],
        settings_file: Path,
        protect: list[str] | None = None,
        *,
        force_broad: bool = False,
        will_create: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Grant ``profile_names`` (added to any existing grant for the same folder+file)."""
        settings_file = settings_file.absolute()
        resolved_folder = (
            self.policy.check_folder(folder, force_broad=force_broad)
            if folder is not None
            else None
        )
        for name in profile_names:
            self.profiles.resolve(name)  # raises on unknown / cyclic
        for glob in protect or []:
            self._check_glob(glob)
        gid = grant_id(resolved_folder, settings_file)
        prev = session.state["grants"].get(gid, {})
        names = list(dict.fromkeys([*prev.get("profiles", []), *profile_names]))
        extra = list(dict.fromkeys([*prev.get("protect", []), *(protect or [])]))
        warnings = session.warnings
        if resolved_folder is not None and not resolved_folder.exists() and not will_create:
            warnings.append(
                f"{resolved_folder} does not exist. The sandbox cannot create it: writes will "
                "fail until it exists (create it first, or use --create)."
            )
        for n in profile_names:
            w = self.profiles.resolve(n).warning
            if w:
                warnings.append(f"profile {n}: {w}")
        warnings.extend(self._conflicts(names))
        self._reconcile(session, gid, resolved_folder, settings_file, names, extra, now)

    def plan_revoke(
        self,
        folder: str | Path | None,
        settings_file: Path,
        profile_names: list[str] | None = None,
        *,
        now: datetime | None = None,
    ) -> Plan:
        session = self.new_session()
        self.revoke(session, folder, settings_file, profile_names, now=now)
        return session.plan()

    def revoke(
        self,
        session: Session,
        folder: str | Path | None,
        settings_file: Path,
        profile_names: list[str] | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        """Remove ``profile_names`` from a grant, or the whole grant if none are given."""
        settings_file = settings_file.absolute()
        resolved_folder = (
            Path(folder).expanduser().resolve(strict=False) if folder is not None else None
        )
        gid = grant_id(resolved_folder, settings_file)
        grant = session.state["grants"].get(gid)
        if grant is None:
            raise GuardError(f"no grant for {resolved_folder or 'global'} in {settings_file}")
        remaining = list(grant["profiles"])
        for name in profile_names or []:
            if name not in remaining:
                raise GuardError(f"grant {gid} does not have profile {name!r}")
            remaining.remove(name)
        if not profile_names or not remaining:
            self._reconcile(session, gid, resolved_folder, settings_file, None, [], now)
        else:
            self._reconcile(
                session, gid, resolved_folder, settings_file, remaining, grant["protect"], now
            )

    def _hook_entry(self, mode: str) -> dict[str, Any]:
        if not self.hook_command:
            raise GuardError("cannot register the hook: no command to run `guard hook edit`")
        exe, *pre = self.hook_command
        return {
            "matcher": HOOK_MATCHER,
            "hooks": [
                {
                    "type": "command",
                    "command": exe,
                    "args": [
                        *pre,
                        "hook",
                        "edit",
                        "--state-dir",
                        str(self.state_dir),
                        "--unlisted",
                        mode,
                    ],
                    "onFailure": "block",  # without this a crashing hook lets the write through (VB-H3)
                }
            ],
        }

    def _conflicts(self, names: list[str]) -> list[str]:
        """Warn about profile combinations that cancel each other out (VB-S8: deny beats allow)."""
        resolved = {n: self.profiles.resolve(n) for n in names}
        readonly = [n for n, p in resolved.items() if p.sandbox_deny_write]
        if not readonly:
            return []
        out = []
        writers = [n for n, p in resolved.items() if p.sandbox_write]
        if writers:
            out.append(
                f"{', '.join(readonly)} overrides the sandbox write access given by "
                f"{', '.join(writers)}: Bash cannot write in the folder (deny beats allow)."
            )
        git = [
            n for n, p in resolved.items() if any(r.startswith("Bash(git ") for r in p.bash_allow)
        ]
        if git:
            out.append(
                f"{', '.join(git)} allows git commands, but Bash is read-only in the folder "
                f"({', '.join(readonly)}), so git commands that write will fail there."
            )
        return out

    @staticmethod
    def _check_glob(glob: str) -> None:
        if glob.startswith("/") or ".." in Path(glob).parts or not glob:
            raise GuardError(f"protect glob {glob!r} must be relative to the folder, without '..'")

    # ---- wants -----------------------------------------------------------------------------
    def _wants(
        self, folder: Path | None, settings_file: Path, names: list[str], extra_protect: list[str]
    ) -> list[Want]:
        f = str(settings_file)
        wants: list[Want] = []

        def arr(path: str, value: Any) -> None:
            wants.append(Want(f, "array", path, value))

        protects: list[str] = []
        for name in names:
            prof = self.profiles.resolve(name)
            if folder is None and prof.needs_folder:
                raise GuardError(f"profile {name!r} needs a folder")
            for tmpl in prof.edit_paths:
                arr(PERM_ALLOW, f"Edit({rule_path(Path(tmpl.format(folder=folder)))})")
            for tmpl in prof.edit_ask:
                arr(PERM_ASK, f"Edit({rule_path(Path(tmpl.format(folder=folder)))})")
            if prof.sandbox_write:
                arr(ALLOW_WRITE, sandbox_path(folder))  # type: ignore[arg-type]
            if prof.sandbox_deny_write:
                arr(DENY_WRITE, sandbox_path(folder))  # type: ignore[arg-type]
            for pat in prof.edit_deny:
                arr(PERM_DENY, f"Edit({pat})")
            if prof.edit_hook:
                wants.append(Want(f, "array", HOOKS_PRE, self._hook_entry(prof.edit_hook)))
            for rule in prof.bash_allow:
                arr(PERM_ALLOW, rule)
            for rule in prof.bash_ask:
                arr(PERM_ASK, rule)
            for rule in prof.bash_deny:
                arr(PERM_DENY, rule)
            for pat in prof.read_deny:
                arr(PERM_DENY, f"Read({pat})")
            for path, value in prof.settings:
                wants.append(Want(f, "scalar", path, value))
            protects.extend(prof.protect)
        protects.extend(extra_protect)
        for glob in dict.fromkeys(protects):
            if folder is None:
                raise GuardError("protect globs need a folder")
            arr(PERM_DENY, f"Edit({rule_path(folder / glob)})")
        # Keep the agent from rewriting its own policy through the (unsandboxed) Edit tool.
        arr(PERM_DENY, f"Edit({rule_path(settings_file)})")
        arr(PERM_DENY, f"Edit({rule_path(self.state_dir / '**')})")
        return list({(w.file, w.key): w for w in wants}.values())

    # ---- reconcile -------------------------------------------------------------------------
    def _reconcile(
        self,
        session: Session,
        gid: str,
        folder: Path | None,
        settings_file: Path,
        names: list[str] | None,
        extra_protect: list[str],
        now: datetime | None,
    ) -> None:
        state = session.state
        wants = [] if names is None else self._wants(folder, settings_file, names, extra_protect)
        want_map = {(w.file, w.key): w for w in wants}
        grant = state["grants"].get(gid)
        old_entries = [tuple(e) for e in grant["entries"]] if grant else []

        for fk in reversed(old_entries):
            if fk not in want_map:
                self._release(state, session.doc_for(fk[0]), fk[0], fk[1], gid, session.warnings)
        kept = [fk for fk in old_entries if fk in want_map]
        for fk, w in want_map.items():
            if fk not in kept:
                self._acquire(state, session.doc_for(w.file), w, gid)
                kept.append(fk)

        if names is None or not kept:
            state["grants"].pop(gid, None)
        else:
            state["grants"][gid] = {
                "folder": str(folder) if folder is not None else None,
                "settings_file": str(settings_file),
                "profiles": names,
                "protect": extra_protect,
                "created_at": (grant or {}).get("created_at")
                or (now or datetime.now(UTC)).isoformat(),
                "entries": [list(fk) for fk in kept],
            }

    @staticmethod
    def _acquire(state: dict[str, Any], doc: SettingsDoc, w: Want, gid: str) -> None:
        frec = state["files"].setdefault(
            w.file, {"file_created": not doc.exists, "created": [], "entries": {}}
        )
        erec = frec["entries"].get(w.key)
        if erec is not None:
            if gid not in erec["refs"]:
                erec["refs"].append(gid)
            if w.kind == "scalar" and erec["value"] != w.value:
                raise GuardError(
                    f"conflict on {w.path}: another grant sets {erec['value']!r}, this one {w.value!r}"
                )
            return
        if w.kind == "array":
            pre = doc.array_contains(w.path, w.value)
            created = [] if pre else doc.add_to_array(w.path, w.value)
            erec = {
                "kind": "array",
                "path": w.path,
                "value": w.value,
                "refs": [gid],
                "preexisting": pre,
            }
        else:
            present, current = doc.get_scalar(w.path)
            created = doc.set_scalar(w.path, w.value)
            erec = {
                "kind": "scalar",
                "path": w.path,
                "value": w.value,
                "refs": [gid],
                "preexisting": False,
                "prior": {"present": present, "value": current},
            }
        for c in created:
            if c not in frec["created"]:
                frec["created"].append(c)
        frec["entries"][w.key] = erec

    @staticmethod
    def _release(
        state: dict[str, Any], doc: SettingsDoc, file: str, key: str, gid: str, warnings: list[str]
    ) -> None:
        frec = state["files"].get(file)
        erec = frec["entries"].get(key) if frec else None
        if erec is None:
            return
        if gid in erec["refs"]:
            erec["refs"].remove(gid)
        if erec["refs"]:
            return
        if erec["kind"] == "array":
            if not erec["preexisting"] and not doc.remove_from_array(erec["path"], erec["value"]):
                warnings.append(f"{file}: {erec['value']!r} was already removed by hand")
        else:
            present, current = doc.get_scalar(erec["path"])
            if present and current == erec["value"]:
                prior = erec["prior"]
                if prior["present"]:
                    doc.set_scalar(erec["path"], prior["value"])
                else:
                    doc.delete_scalar(erec["path"])
            else:
                warnings.append(f"{file}: {erec['path']} was changed by hand; leaving it as it is")
        del frec["entries"][key]
        pruned = doc.prune(set(frec["created"]))
        frec["created"] = [c for c in frec["created"] if c not in pruned]
        if not frec["entries"]:
            if frec["file_created"] and not doc.data:
                doc.delete = True
            del state["files"][file]

    # ---- commit ----------------------------------------------------------------------------
    def commit(self, plan: Plan, *, now: datetime | None = None) -> list[Path]:
        """Write the planned changes. Returns the backups made. Restores files if anything fails."""
        if plan.noop:
            return []
        for ch in plan.changes:  # validate before touching anything
            if ch.new_text is not None:
                try:
                    json.loads(ch.new_text)
                except json.JSONDecodeError as e:  # pragma: no cover - defensive
                    raise SettingsError(f"refusing to write invalid JSON to {ch.path}: {e}") from e
        backups: list[Path] = []
        done: list[FileChange] = []
        try:
            for ch in plan.changes:
                b = backup_file(ch.path, self.backup_dir, now)
                if b:
                    backups.append(b)
                if ch.new_text is None:
                    ch.path.unlink(missing_ok=True)
                else:
                    atomic_write(ch.path, ch.new_text)
                done.append(ch)
            b = backup_file(self.state_file, self.backup_dir, now)
            if b:
                backups.append(b)
            save_state(self.state_file, plan.new_state)
        except BaseException:
            for ch in reversed(done):
                if ch.old_text is None:
                    ch.path.unlink(missing_ok=True)
                else:
                    atomic_write(ch.path, ch.old_text)
            raise
        return backups


__all__ = ["FileChange", "Guard", "Plan", "Session", "Want", "empty_state", "grant_id"]
