from __future__ import annotations

from pathlib import Path

import pytest

from claude_guard.pathsafety import PathPolicy


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
