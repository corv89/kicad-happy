"""Contract tests for skills/datasheets/datasheet_types/ dataclasses.

Verifies that:
1. Dataclasses load from dict inputs matching the Track 2.1 JSON schemas.
2. to_dict() emits dicts that re-validate against the Track 2.1 schemas.
3. Round-trip (dict → dataclass → dict) preserves content.

Source of truth is the hand-written JSON schemas under
skills/datasheets/schemas/. The Python dataclasses are ergonomic
typed access on top; they do not re-derive the schemas.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "skills" / "datasheets" / "schemas"
FIXTURE_DIR = SCHEMA_DIR / "fixtures"  # Used by fixture round-trip tests in Tasks 3 and 5.

# Make skills/datasheets/datasheet_types/ importable as a top-level 'datasheet_types' package.
sys.path.insert(0, str(REPO_ROOT / "skills" / "datasheets"))


def _load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _build_registry() -> Registry:
    registry = Registry()
    for schema_path in SCHEMA_DIR.glob("*.schema.json"):
        schema = _load_json(schema_path)
        uri = schema.get("$id")
        if uri:
            registry = registry.with_resource(uri, Resource.from_contents(schema))
    return registry


# ---------------------------------------------------------------------------
# SpecValue + Evidence round-trip
# ---------------------------------------------------------------------------

def test_spec_value_from_dict_roundtrip() -> None:
    from datasheet_types.spec_value import SpecValue
    from datasheet_types.codec import from_dict, to_dict

    raw = {
        "min": 1.18, "typ": 1.23, "max": 1.28,
        "unit": "V",
        "condition": None, "notes": None,
        "evidence": {"page": 5, "section": "Electrical Characteristics",
                     "confidence": "high", "method": "table"},
    }
    obj = from_dict(SpecValue, raw)
    assert obj.min == 1.18
    assert obj.typ == 1.23
    assert obj.max == 1.28
    assert obj.unit == "V"
    assert obj.condition is None
    assert obj.evidence.page == 5
    assert obj.evidence.confidence == "high"
    # to_dict round-trips without drift.
    assert to_dict(obj) == raw


def test_spec_value_to_dict_validates_against_schema() -> None:
    from datasheet_types.spec_value import SpecValue
    from datasheet_types.codec import from_dict, to_dict

    raw = {
        "min": None, "typ": 50, "max": None,
        "unit": "°C/W",
        "condition": "TO-263, 1oz Cu, 2in² pour",
        "notes": "θJA",
        "evidence": {"page": 3, "section": "Thermal Information",
                     "confidence": "high", "method": "table"},
    }
    obj = from_dict(SpecValue, raw)
    emitted = to_dict(obj)

    schema = _load_json(SCHEMA_DIR / "spec_value.schema.json")
    registry = _build_registry()
    validator = Draft202012Validator(schema, registry=registry)
    errors = list(validator.iter_errors(emitted))
    assert errors == [], "\n".join(str(e.message) for e in errors)


def test_spec_value_required_fields_enforced() -> None:
    """Missing 'unit' or 'evidence' should raise, matching schema required[]."""
    from datasheet_types.spec_value import SpecValue
    from datasheet_types.codec import from_dict

    # Missing unit
    with pytest.raises((KeyError, TypeError)):
        from_dict(SpecValue, {"evidence": {"page": 1, "confidence": "high", "method": "table"}})
    # Missing evidence
    with pytest.raises((KeyError, TypeError)):
        from_dict(SpecValue, {"unit": "V"})


def test_evidence_from_dict_roundtrip() -> None:
    from datasheet_types.spec_value import Evidence
    from datasheet_types.codec import from_dict, to_dict

    raw = {"page": 14, "section": "External Components",
           "confidence": "medium", "method": "prose"}
    obj = from_dict(Evidence, raw)
    assert obj.page == 14
    assert obj.section == "External Components"
    assert obj.confidence == "medium"
    assert obj.method == "prose"
    assert to_dict(obj) == raw


def test_evidence_nullable_section_preserved() -> None:
    from datasheet_types.spec_value import Evidence
    from datasheet_types.codec import from_dict, to_dict

    raw = {"page": 1, "section": None, "confidence": "high", "method": "table"}
    obj = from_dict(Evidence, raw)
    assert obj.section is None
    assert to_dict(obj) == raw


def test_codec_list_field_rejects_non_list() -> None:
    """from_dict raises TypeError when a list-typed field gets a non-list value.

    Guards against silent string-iteration bugs that would confuse
    downstream dict[str, list[T]] consumers (Task 3's BaseBlock).
    """
    from datasheet_types.codec import _from_value

    with pytest.raises(TypeError, match="Expected list"):
        _from_value(list[str], "not-a-list")


def test_codec_dict_field_rejects_non_dict() -> None:
    """from_dict raises TypeError when a dict-typed field gets a non-dict value."""
    from datasheet_types.codec import _from_value

    with pytest.raises(TypeError, match="Expected dict"):
        _from_value(dict[str, int], [1, 2, 3])
