"""Test configuration and shared fixtures."""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_sessions_path() -> Path:
    return FIXTURES_DIR / "sample_sessions.json"


@pytest.fixture
def sample_sessions() -> list[dict]:
    with open(FIXTURES_DIR / "sample_sessions.json") as f:
        return json.load(f)
