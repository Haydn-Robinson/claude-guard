"""Exception types for claude-guard."""


class GuardError(Exception):
    """Base class for all expected, user-facing errors."""


class UnsafeFolderError(GuardError):
    """A folder was refused as a grant target.

    ``overridable`` is True only for the "too broad" depth rule, which ``--force-broad`` may bypass.
    """

    def __init__(self, message: str, overridable: bool = False):
        super().__init__(message)
        self.overridable = overridable


class SettingsError(GuardError):
    """A Claude Code settings file is malformed or has an unexpected shape."""


class PolicyError(GuardError):
    """policy.toml is invalid: bad syntax, unknown keys or values, unsafe paths."""


class CompileError(GuardError):
    """The policy is valid but cannot be enforced as written (conflicts, ceilings, locked paths)."""
