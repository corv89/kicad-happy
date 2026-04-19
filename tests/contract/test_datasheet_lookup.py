"""Contract tests for datasheet_lookup — the consumer API entry point.

Track 2.3 of the v1.4 datasheet extraction work. Tests the lookup()
facade, MPN sanitization, cache-path resolution, and PDF staleness
detection. All tests are self-contained via pytest.tmp_path — no
committed test cache files.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "skills" / "datasheets" / "schemas"
FIXTURE_DIR = SCHEMA_DIR / "fixtures"

# Make both the datasheet_types package and the scripts module importable.
sys.path.insert(0, str(REPO_ROOT / "skills" / "datasheets"))
sys.path.insert(0, str(REPO_ROOT / "skills" / "datasheets" / "scripts"))


def _load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# sanitize_mpn
# ---------------------------------------------------------------------------

def test_sanitize_mpn_clean_passthrough() -> None:
    """Already-clean MPNs pass through unchanged."""
    from datasheet_lookup import sanitize_mpn

    assert sanitize_mpn("LM2596-ADJ") == "LM2596-ADJ"
    assert sanitize_mpn("STM32F103C8T6") == "STM32F103C8T6"
    assert sanitize_mpn("RC0603FR-071KL") == "RC0603FR-071KL"


def test_sanitize_mpn_replaces_unsafe_chars() -> None:
    """Slashes, dots, spaces, percent signs → underscore."""
    from datasheet_lookup import sanitize_mpn

    assert sanitize_mpn("STM32/F103") == "STM32_F103"
    assert sanitize_mpn("LM2596.ADJ") == "LM2596_ADJ"
    assert sanitize_mpn("ACME 1234") == "ACME_1234"
    assert sanitize_mpn("PART%V2") == "PART_V2"


def test_sanitize_mpn_strips_whitespace() -> None:
    """Leading/trailing whitespace is stripped before sanitization."""
    from datasheet_lookup import sanitize_mpn

    assert sanitize_mpn("  LM2596-ADJ  ") == "LM2596-ADJ"
    assert sanitize_mpn("\tLM2596-ADJ\n") == "LM2596-ADJ"


def test_sanitize_mpn_preserves_underscore_and_hyphen() -> None:
    """_ and - are safe filename characters and preserved."""
    from datasheet_lookup import sanitize_mpn

    assert sanitize_mpn("LM_2596-ADJ") == "LM_2596-ADJ"


# ---------------------------------------------------------------------------
# cache_path_for
# ---------------------------------------------------------------------------

def test_cache_path_for_composes_sanitized_filename() -> None:
    """cache_path_for returns <cache_dir>/<sanitized_mpn>.json."""
    from datasheet_lookup import cache_path_for

    cache_dir = Path("/tmp/test-cache")
    assert cache_path_for("LM2596-ADJ", cache_dir) == cache_dir / "LM2596-ADJ.json"
    assert cache_path_for("STM32/F103", cache_dir) == cache_dir / "STM32_F103.json"


# ---------------------------------------------------------------------------
# lookup() — core read path (no staleness yet)
# ---------------------------------------------------------------------------

def test_lookup_returns_none_when_cache_dir_missing(tmp_path: Path) -> None:
    """lookup returns None when the cache directory doesn't exist."""
    from datasheet_lookup import lookup

    missing_cache_dir = tmp_path / "does-not-exist"
    assert lookup("LM2596-ADJ", cache_dir=missing_cache_dir) is None


def test_lookup_returns_none_when_cache_file_missing(tmp_path: Path) -> None:
    """lookup returns None when the cache file for the MPN doesn't exist."""
    from datasheet_lookup import lookup

    cache_dir = tmp_path / "extracted"
    cache_dir.mkdir()
    assert lookup("NOT-CACHED", cache_dir=cache_dir) is None


def test_lookup_returns_none_when_cache_json_malformed(tmp_path: Path) -> None:
    """lookup returns None when the cache file contains malformed JSON."""
    from datasheet_lookup import lookup

    cache_dir = tmp_path / "extracted"
    cache_dir.mkdir()
    (cache_dir / "BROKEN.json").write_text("{not valid json")
    assert lookup("BROKEN", cache_dir=cache_dir) is None


def test_lookup_returns_none_when_cache_violates_shape(tmp_path: Path) -> None:
    """lookup returns None when the cache file parses as JSON but is
    missing required DatasheetFacts fields."""
    from datasheet_lookup import lookup

    cache_dir = tmp_path / "extracted"
    cache_dir.mkdir()
    # Valid JSON but no 'schema_version', 'source', 'extraction', 'base'.
    (cache_dir / "INCOMPLETE.json").write_text(json.dumps({"hello": "world"}))
    assert lookup("INCOMPLETE", cache_dir=cache_dir) is None


def test_lookup_returns_datasheet_facts_for_valid_cache(tmp_path: Path) -> None:
    """lookup returns a populated DatasheetFacts when the cache is valid."""
    from datasheet_lookup import lookup
    from datasheet_types.extraction import DatasheetFacts

    cache_dir = tmp_path / "extracted"
    cache_dir.mkdir()
    # Copy the LM2596-ADJ fixture into the cache dir as the real cache file.
    fixture = _load_json(FIXTURE_DIR / "lm2596-adj.example.json")
    (cache_dir / "LM2596-ADJ.json").write_text(json.dumps(fixture))

    facts = lookup("LM2596-ADJ", cache_dir=cache_dir)
    assert facts is not None
    assert isinstance(facts, DatasheetFacts)
    assert facts.source.mpn == "LM2596-ADJ"
    assert facts.base.family == "step-down switching regulator"
    assert facts.regulator is not None
    assert facts.regulator.topology == "buck"


def test_lookup_attaches_cache_path_to_returned_facts(tmp_path: Path) -> None:
    """lookup attaches the cache path so ds.cache_path returns it."""
    from datasheet_lookup import lookup

    cache_dir = tmp_path / "extracted"
    cache_dir.mkdir()
    fixture = _load_json(FIXTURE_DIR / "lm2596-adj.example.json")
    cache_file = cache_dir / "LM2596-ADJ.json"
    cache_file.write_text(json.dumps(fixture))

    facts = lookup("LM2596-ADJ", cache_dir=cache_dir)
    assert facts is not None
    assert facts.cache_path == cache_file


def test_lookup_quality_passthrough(tmp_path: Path) -> None:
    """lookup-returned facts expose quality via the Task 1 property."""
    from datasheet_lookup import lookup

    cache_dir = tmp_path / "extracted"
    cache_dir.mkdir()
    fixture = _load_json(FIXTURE_DIR / "lm2596-adj.example.json")
    (cache_dir / "LM2596-ADJ.json").write_text(json.dumps(fixture))

    facts = lookup("LM2596-ADJ", cache_dir=cache_dir)
    # LM2596-ADJ fixture has quality_score = 87.
    assert facts.quality == 87
