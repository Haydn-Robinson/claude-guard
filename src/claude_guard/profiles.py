"""Profile definitions (TOML), ``extends`` resolution and validation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import ProfileError

_LIST_FIELDS = (
    "edit_paths",
    "edit_ask",
    "edit_deny",
    "bash_allow",
    "bash_ask",
    "bash_deny",
    "read_deny",
    "protect",
)
_BOOL_FIELDS = ("sandbox_write", "sandbox_deny_write")
_KNOWN = {
    "description",
    "warning",
    "extends",
    "settings",
    "edit_hook",
    *_BOOL_FIELDS,
    *_LIST_FIELDS,
}


@dataclass(frozen=True)
class Profile:
    name: str
    description: str = ""
    warning: str = ""
    extends: tuple[str, ...] = ()
    edit_paths: tuple[str, ...] = ()
    edit_ask: tuple[str, ...] = ()
    edit_deny: tuple[str, ...] = ()
    sandbox_write: bool = False
    sandbox_deny_write: bool = False
    edit_hook: str = ""
    bash_allow: tuple[str, ...] = ()
    bash_ask: tuple[str, ...] = ()
    bash_deny: tuple[str, ...] = ()
    read_deny: tuple[str, ...] = ()
    protect: tuple[str, ...] = ()
    settings: tuple[tuple[str, Any], ...] = ()

    @property
    def needs_folder(self) -> bool:
        return bool(
            self.edit_paths
            or self.edit_ask
            or self.sandbox_write
            or self.sandbox_deny_write
            or self.protect
        )


def _dedup(*seqs: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x for seq in seqs for x in seq))


def _parse(name: str, raw: dict[str, Any]) -> Profile:
    unknown = set(raw) - _KNOWN
    if unknown:
        raise ProfileError(f"profile {name!r}: unknown keys {sorted(unknown)}")
    kwargs: dict[str, Any] = {"name": name}
    for key in ("description", "warning"):
        if key in raw:
            if not isinstance(raw[key], str):
                raise ProfileError(f"profile {name!r}: {key} must be a string")
            kwargs[key] = raw[key]
    for key in _BOOL_FIELDS:
        if key in raw:
            if not isinstance(raw[key], bool):
                raise ProfileError(f"profile {name!r}: {key} must be a boolean")
            kwargs[key] = raw[key]
    for key in ("extends", *_LIST_FIELDS):
        if key in raw:
            val = raw[key]
            if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
                raise ProfileError(f"profile {name!r}: {key} must be a list of strings")
            kwargs[key] = tuple(val)
    if "edit_hook" in raw:
        if raw["edit_hook"] not in ("ask", "deny"):
            raise ProfileError(f'profile {name!r}: edit_hook must be "ask" or "deny"')
        kwargs["edit_hook"] = raw["edit_hook"]
    for key in ("edit_paths", "edit_ask"):
        for tmpl in kwargs.get(key, ()):
            if not tmpl.startswith("{folder}"):
                raise ProfileError(f"profile {name!r}: {key} template must start with {{folder}}")
    if any(not pat.strip() for pat in kwargs.get("edit_deny", ())):
        raise ProfileError(f"profile {name!r}: edit_deny entries must not be empty")
    for key in ("bash_allow", "bash_ask", "bash_deny"):
        for rule in kwargs.get(key, ()):
            if not (rule.startswith("Bash(") and rule.endswith(")")):
                raise ProfileError(f"profile {name!r}: {key} entry {rule!r} must be Bash(...)")
    for glob in kwargs.get("protect", ()):
        if glob.startswith("/") or ".." in Path(glob).parts:
            raise ProfileError(f"profile {name!r}: protect glob {glob!r} must be relative")
    if "settings" in raw:
        items = []
        for item in raw["settings"]:
            if not isinstance(item, dict) or set(item) != {"path", "value"}:
                raise ProfileError(f"profile {name!r}: settings items need exactly path and value")
            items.append((str(item["path"]), item["value"]))
        kwargs["settings"] = tuple(items)
    return Profile(**kwargs)


def _read_toml(text: str, origin: str) -> dict[str, Profile]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"{origin}: invalid TOML: {e}") from e
    out = {}
    for name, raw in data.items():
        if not isinstance(raw, dict):
            raise ProfileError(f"{origin}: profile {name!r} must be a table")
        out[name] = _parse(name, raw)
    return out


@dataclass
class ProfileSet:
    profiles: dict[str, Profile] = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return sorted(self.profiles)

    def get(self, name: str) -> Profile:
        try:
            return self.profiles[name]
        except KeyError:
            raise ProfileError(
                f"unknown profile {name!r}; known: {', '.join(self.names)}"
            ) from None

    def resolve(self, name: str) -> Profile:
        """Flatten ``extends`` into a single profile (parents first, cycles rejected)."""
        return self._resolve(name, ())

    def _resolve(self, name: str, stack: tuple[str, ...]) -> Profile:
        if name in stack:
            raise ProfileError(f"profile extends cycle: {' -> '.join((*stack, name))}")
        prof = self.get(name)
        merged = Profile(name=name)
        for parent in prof.extends:
            merged = _merge(merged, self._resolve(parent, (*stack, name)))
        return replace(
            _merge(merged, prof), name=name, description=prof.description, warning=prof.warning
        )


def _merge(a: Profile, b: Profile) -> Profile:
    settings = dict(a.settings)
    settings.update(dict(b.settings))
    return Profile(
        name=b.name,
        description=b.description or a.description,
        warning=b.warning or a.warning,
        extends=(),
        edit_paths=_dedup(a.edit_paths, b.edit_paths),
        edit_ask=_dedup(a.edit_ask, b.edit_ask),
        edit_deny=_dedup(a.edit_deny, b.edit_deny),
        sandbox_write=a.sandbox_write or b.sandbox_write,
        sandbox_deny_write=a.sandbox_deny_write or b.sandbox_deny_write,
        edit_hook=b.edit_hook or a.edit_hook,
        bash_allow=_dedup(a.bash_allow, b.bash_allow),
        bash_ask=_dedup(a.bash_ask, b.bash_ask),
        bash_deny=_dedup(a.bash_deny, b.bash_deny),
        read_deny=_dedup(a.read_deny, b.read_deny),
        protect=_dedup(a.protect, b.protect),
        settings=tuple(settings.items()),
    )


def load_profiles(user_file: Path | None = None) -> ProfileSet:
    """Load shipped defaults, then overlay the user's profiles.toml (same name replaces)."""
    text = resources.files("claude_guard").joinpath("default_profiles.toml").read_text()
    profiles = _read_toml(text, "default_profiles.toml")
    if user_file is not None and user_file.exists():
        profiles.update(_read_toml(user_file.read_text(), str(user_file)))
    ps = ProfileSet(profiles)
    for name in ps.profiles:  # validate extends references and cycles eagerly
        ps.resolve(name)
    return ps
