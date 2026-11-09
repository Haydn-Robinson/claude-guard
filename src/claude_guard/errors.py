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


class ProfileError(GuardError):
    """A profile definition is invalid or unknown."""


class StateError(GuardError):
    """The state file is malformed or inconsistent."""
