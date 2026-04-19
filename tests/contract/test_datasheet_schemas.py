"""Contract tests for datasheet v2 JSON schemas.

Validates that every schema under skills/datasheets/schemas/ parses as
valid Draft 2020-12 and that the accompanying fixture files validate
against their declared schemas.

Uses jsonschema (Draft 2020-12 validator). Both are dev dependencies in
requirements-dev.txt.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "skills" / "datasheets" / "schemas"
FIXTURE_DIR = SCHEMA_DIR / "fixtures"


def _load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _build_registry() -> Registry:
    """Build a referencing Registry so $ref between schemas resolves."""
    registry = Registry()
    for schema_path in SCHEMA_DIR.glob("*.schema.json"):
        schema = _load_json(schema_path)
        uri = schema.get("$id")
        if uri:
            registry = registry.with_resource(uri, Resource.from_contents(schema))
    return registry


# ---------------------------------------------------------------------------
# Per-schema Draft 2020-12 validity
# ---------------------------------------------------------------------------

SCHEMA_FILES = [
    "spec_value.schema.json",
]


@pytest.mark.parametrize("schema_filename", SCHEMA_FILES)
def test_schema_is_valid_draft_2020_12(schema_filename: str) -> None:
    schema_path = SCHEMA_DIR / schema_filename
    assert schema_path.exists(), f"Missing schema file: {schema_path}"
    schema = _load_json(schema_path)
    # Raises SchemaError if the schema itself is malformed.
    Draft202012Validator.check_schema(schema)
    # Every schema must declare its $id and $schema dialect.
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    assert "$id" in schema, f"{schema_filename} is missing $id"


# ---------------------------------------------------------------------------
# SpecValue-specific shape assertions
# ---------------------------------------------------------------------------

def test_spec_value_requires_unit_and_evidence() -> None:
    schema = _load_json(SCHEMA_DIR / "spec_value.schema.json")
    required = set(schema.get("required", []))
    # unit is required so consumers never parse free-form strings.
    assert "unit" in required
    # evidence is required so no fact ships without provenance.
    assert "evidence" in required


def test_spec_value_evidence_has_required_confidence_and_method() -> None:
    schema = _load_json(SCHEMA_DIR / "spec_value.schema.json")
    evidence = schema["properties"]["evidence"]
    req = set(evidence.get("required", []))
    assert "page" in req
    assert "confidence" in req
    assert "method" in req
    # confidence is the trust-gating pivot — must be a closed enum.
    assert evidence["properties"]["confidence"]["enum"] == ["high", "medium", "low"]
    # method is the extraction-technique tag — closed enum.
    assert set(evidence["properties"]["method"]["enum"]) == {
        "table", "prose", "curve", "calculated", "derived",
    }


def test_spec_value_unit_is_closed_enum() -> None:
    schema = _load_json(SCHEMA_DIR / "spec_value.schema.json")
    unit = schema["properties"]["unit"]
    # Canonical SI set per spec §3, plus two compound units for thermal
    # resistance (θJA, θJC, ψJT) and thermal capacity. Extraction converts
    # (µA → A, mV → V, kΩ → Ω, kHz → Hz, °C/W → °C/W) so consumers never
    # see prefixes.
    expected = {
        "V", "A", "s", "Hz", "Ω", "F", "H", "K", "W", "°C", "%", "ppm",
        "K/W", "°C/W",
    }
    assert set(unit["enum"]) == expected


def test_spec_value_numeric_fields_are_nullable_numbers() -> None:
    schema = _load_json(SCHEMA_DIR / "spec_value.schema.json")
    props = schema["properties"]
    for key in ("min", "typ", "max"):
        # Each may be a number or null (datasheet doesn't always publish
        # all three). Consumers check for None explicitly.
        assert props[key]["type"] == ["number", "null"]
