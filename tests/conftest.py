"""Pytest configuration isolated from shared Windows temporary directories."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Give each test process a unique repository-local base temp directory."""

    root = Path(str(config.rootpath)).resolve()
    run_id = f".pytest-run-{os.getpid()}-{uuid.uuid4().hex}"
    config.option.basetemp = str(root / run_id)

