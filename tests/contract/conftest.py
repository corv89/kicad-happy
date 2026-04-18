"""Shared fixtures for analyzer contract tests."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills" / "kicad" / "scripts"
EMC_SCRIPTS_DIR = REPO_ROOT / "skills" / "emc" / "scripts"
FIXTURE_PROJECT = REPO_ROOT / "tests" / "fixtures" / "simple-project"


@pytest.fixture
def fixture_project() -> Path:
    """Path to the minimal KiCad fixture project."""
    return FIXTURE_PROJECT


def _run_analyzer(script: Path, argv: list[str]) -> dict:
    result = subprocess.run(
        [sys.executable, str(script), *argv],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def _get_schema(script: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(script), "--schema"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture
def run_analyzer():
    return _run_analyzer


@pytest.fixture
def get_schema():
    return _get_schema
