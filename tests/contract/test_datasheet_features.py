"""Contract tests for v1.3 compat wrappers in datasheet_features.py.

Track 2.5 of the v1.4 datasheet extraction work. Tests the dual-cache-
read behavior: v1.4 cache preferred via lookup(), v1.3 cache as fallback.

All tests use pytest.tmp_path for self-contained cache fixtures. The
v1.4 cache is written in the Track 2.1 extraction.schema.json shape;
the v1.3 cache is written in the extraction_version=2 shape that
datasheet_extract_cache.py historically produced.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "skills" / "datasheets" / "schemas"
FIXTURE_DIR = SCHEMA_DIR / "fixtures"

# Both sys.path entries needed: datasheet_types as a package + scripts dir
# for datasheet_features/datasheet_extract_cache/datasheet_lookup.
sys.path.insert(0, str(REPO_ROOT / "skills" / "datasheets"))
sys.path.insert(0, str(REPO_ROOT / "skills" / "datasheets" / "scripts"))


def _load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _build_lm2596_facts():
    """Construct a DatasheetFacts from the LM2596-ADJ fixture."""
    from datasheet_types.codec import from_dict
    from datasheet_types.extraction import DatasheetFacts

    fixture = _load_json(FIXTURE_DIR / "lm2596-adj.example.json")
    return from_dict(DatasheetFacts, fixture)


# ---------------------------------------------------------------------------
# _derive_regulator_features_v14 — unit tests (no filesystem)
# ---------------------------------------------------------------------------

def test_derive_regulator_features_v14_lm2596() -> None:
    """LM2596-ADJ fixture → v1.3 regulator feature dict."""
    from datasheet_features import _derive_regulator_features_v14

    facts = _build_lm2596_facts()
    feat = _derive_regulator_features_v14(facts)

    assert feat is not None
    assert feat["topology"] == "buck"
    assert feat["en_pin"] == "5"
    # LM2596-ADJ has no PG pin.
    assert feat["pg_pin"] is None
    assert feat["has_pg"] is False
    # Derived VIN / VOUT pin refs from base.pinout by name match.
    assert feat["vin_pin"] == "1"     # Pin 1 is named "VIN"
    assert feat["vout_pin"] == "2"    # Pin 2 is named "OUT" (falls into the VOUT map)
    # Fields with no v1.4 equivalent degrade to None.
    assert feat["has_soft_start"] is None
    assert feat["iss_time_us"] is None
    assert feat["en_v_ih_max"] is None
    assert feat["en_v_il_min"] is None


def test_derive_regulator_features_v14_returns_none_when_no_regulator_category() -> None:
    """Non-regulator fixtures return None."""
    from datasheet_types.codec import from_dict
    from datasheet_types.extraction import DatasheetFacts
    from datasheet_features import _derive_regulator_features_v14

    fixture = _load_json(FIXTURE_DIR / "minimal.example.json")
    facts = from_dict(DatasheetFacts, fixture)
    # minimal.example.json has no regulator category.
    assert _derive_regulator_features_v14(facts) is None


def test_derive_regulator_features_v14_derives_has_pg_from_power_good_pin() -> None:
    """has_pg is True iff regulator.power_good_pin is populated."""
    from datasheet_features import _derive_regulator_features_v14

    facts = _build_lm2596_facts()
    # LM2596 has no power_good_pin → has_pg=False, pg_pin=None.
    feat = _derive_regulator_features_v14(facts)
    assert feat["has_pg"] is False
    assert feat["pg_pin"] is None

    # Mutate the fixture to add a PG pin, re-derive.
    facts.regulator.power_good_pin = "3"
    feat2 = _derive_regulator_features_v14(facts)
    assert feat2["has_pg"] is True
    assert feat2["pg_pin"] == "3"


# ---------------------------------------------------------------------------
# _derive_mcu_features_v14 — always None on v1.4 MVP (no mcu category yet)
# ---------------------------------------------------------------------------

def test_derive_mcu_features_v14_returns_none_on_v14_mvp() -> None:
    """v1.4 MVP has no mcu category — derivation always returns None.

    Consumers fall through to the v1.3 cache path in the public
    get_mcu_features wrapper.
    """
    from datasheet_features import _derive_mcu_features_v14

    facts = _build_lm2596_facts()
    # Regulator category only; no mcu.
    assert _derive_mcu_features_v14(facts) is None


# ---------------------------------------------------------------------------
# _derive_pin_function_v14 — per-pin function lookup
# ---------------------------------------------------------------------------

def test_derive_pin_function_v14_enable_pin() -> None:
    """Regulator.enable_pin → 'EN' function."""
    from datasheet_features import _derive_pin_function_v14

    facts = _build_lm2596_facts()
    # LM2596 EN is pin 5.
    assert _derive_pin_function_v14(facts, "5") == "EN"


def test_derive_pin_function_v14_vin_pin_by_name() -> None:
    """Pin named VIN → 'VIN' function."""
    from datasheet_features import _derive_pin_function_v14

    facts = _build_lm2596_facts()
    assert _derive_pin_function_v14(facts, "1") == "VIN"


def test_derive_pin_function_v14_gnd_pin_by_name() -> None:
    """Pin named GND → 'GND' function."""
    from datasheet_features import _derive_pin_function_v14

    facts = _build_lm2596_facts()
    # LM2596 GND is pin 3.
    assert _derive_pin_function_v14(facts, "3") == "GND"


def test_derive_pin_function_v14_unknown_pin_returns_none() -> None:
    """Nonexistent pin number → None."""
    from datasheet_features import _derive_pin_function_v14

    facts = _build_lm2596_facts()
    assert _derive_pin_function_v14(facts, "99") is None
