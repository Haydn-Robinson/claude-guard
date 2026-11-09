from __future__ import annotations

from pathlib import Path

import pytest

from claude_guard.engine import Guard
from claude_guard.pathsafety import PathPolicy
from claude_guard.profiles import load_profiles


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def policy(home: Path) -> PathPolicy:
    return PathPolicy(
        home, drive_roots=[Path("/mnt/d"), Path("/mnt/e")], system_drive=Path("/mnt/c")
    )


@pytest.fixture
def guard(tmp_path: Path, policy: PathPolicy) -> Guard:
    return Guard(tmp_path / "state", load_profiles(), policy, hook_command=["/opt/guard/bin/guard"])


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".claude").mkdir(parents=True)
    return p


@pytest.fixture
def settings(proj: Path) -> Path:
    return proj / ".claude" / "settings.local.json"


@pytest.fixture
def target(tmp_path: Path) -> Path:
    v = tmp_path / "My Projects" / "Sample Project"  # spaces on purpose
    v.mkdir(parents=True)
    return v
