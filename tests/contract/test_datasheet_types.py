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


# ---------------------------------------------------------------------------
# Pin + AltFunction + Pinout wrapper
# ---------------------------------------------------------------------------

def test_alt_function_roundtrip() -> None:
    from datasheet_types.pinout import AltFunction
    from datasheet_types.codec import from_dict, to_dict

    raw = {"name": "USART1_TX", "peripheral": "USART1", "role": "TX", "af_code": "AF7"}
    obj = from_dict(AltFunction, raw)
    assert obj.name == "USART1_TX"
    assert obj.peripheral == "USART1"
    assert obj.role == "TX"
    assert obj.af_code == "AF7"
    assert to_dict(obj) == raw


def test_pin_roundtrip_minimal() -> None:
    """Pin with only required fields — numbers, name, type."""
    from datasheet_types.pinout import Pin
    from datasheet_types.codec import from_dict, to_dict

    raw = {"numbers": ["1"], "name": "VIN", "type": "power_in"}
    obj = from_dict(Pin, raw)
    assert obj.numbers == ["1"]
    assert obj.name == "VIN"
    assert obj.type == "power_in"
    # Optional fields default to None.
    assert obj.subtype is None
    assert obj.evidence is None
    assert obj.alt_functions == []  # default_factory list
    # to_dict emits every field (including Nones).
    emitted = to_dict(obj)
    assert emitted["numbers"] == ["1"]
    assert emitted["name"] == "VIN"
    assert emitted["type"] == "power_in"
    assert emitted["subtype"] is None
    assert emitted["evidence"] is None
    assert emitted["alt_functions"] == []


def test_pin_roundtrip_full() -> None:
    """Pin with all optional fields populated — BGA-style."""
    from datasheet_types.pinout import Pin
    from datasheet_types.codec import from_dict, to_dict

    raw = {
        "numbers": ["A1"],
        "name": "PA9",
        "type": "bidirectional",
        "subtype": "open_drain",
        "description": "GPIO Port A bit 9",
        "power_domain": "VDDIO_1",
        "alt_functions": [
            {"name": "USART1_TX", "peripheral": "USART1", "role": "TX", "af_code": "AF7"},
        ],
        "is_5v_tolerant": True,
        "absolute_max": None,
        "recommended": None,
        "drive_strength": None,
        "notes": None,
        "evidence": {"page": 10, "section": "Pinout", "confidence": "high", "method": "table"},
    }
    obj = from_dict(Pin, raw)
    assert obj.numbers == ["A1"]
    assert obj.alt_functions[0].peripheral == "USART1"
    assert obj.is_5v_tolerant is True
    assert obj.evidence.page == 10
    assert to_dict(obj) == raw


def test_pin_with_populated_spec_value_lists() -> None:
    """Optional[list[SpecValue]] fields round-trip when populated.

    The first exercise of the codec's Optional[list[nested_dataclass]]
    path — a regression in that path would silently break base block's
    dict[str, list[SpecValue]] shape in Task 3.
    """
    from datasheet_types.pinout import Pin
    from datasheet_types.codec import from_dict, to_dict

    ev = {"page": 5, "section": "Electrical Characteristics",
          "confidence": "high", "method": "table"}
    raw = {
        "numbers": ["PA9"], "name": "PA9", "type": "bidirectional",
        "subtype": None, "description": None, "power_domain": None,
        "alt_functions": [],
        "is_5v_tolerant": None,
        "absolute_max": [{
            "min": None, "typ": None, "max": 5.5,
            "unit": "V", "condition": None, "notes": None, "evidence": ev,
        }],
        "recommended": None,
        "drive_strength": None,
        "notes": None,
        "evidence": None,
    }
    obj = from_dict(Pin, raw)
    assert obj.absolute_max is not None
    assert len(obj.absolute_max) == 1
    assert obj.absolute_max[0].max == 5.5
    assert obj.absolute_max[0].unit == "V"
    assert obj.absolute_max[0].evidence.page == 5
    assert obj.recommended is None
    assert obj.drive_strength is None
    # Round-trip preserves the populated-list shape.
    assert to_dict(obj) == raw


def test_pinout_find_by_number_and_name() -> None:
    from datasheet_types.pinout import Pin, Pinout

    pins = [
        Pin(numbers=["1"], name="VIN", type="power_in"),
        Pin(numbers=["2"], name="OUT", type="output"),
        Pin(numbers=["3"], name="GND", type="power_in"),
    ]
    pinout = Pinout(pins=pins)
    # Find by pin number
    p = pinout.find(pin="2")
    assert p is not None
    assert p.name == "OUT"
    # Find by name
    p = pinout.find(name="VIN")
    assert p is not None
    assert p.numbers == ["1"]
    # Missing
    assert pinout.find(pin="99") is None
    assert pinout.find(name="DOESNOTEXIST") is None
    # Calling find() with no arguments returns None (documented behavior).
    assert pinout.find() is None


def test_pinout_in_domain() -> None:
    from datasheet_types.pinout import Pin, Pinout

    pins = [
        Pin(numbers=["1"], name="VDDIO1", type="power_in", power_domain="VDDIO_1"),
        Pin(numbers=["2"], name="VDDIO2", type="power_in", power_domain="VDDIO_1"),
        Pin(numbers=["3"], name="VDDCORE", type="power_in", power_domain="VDDCORE"),
        Pin(numbers=["4"], name="GND", type="power_in"),
    ]
    pinout = Pinout(pins=pins)
    matches = pinout.in_domain("VDDIO_1")
    assert len(matches) == 2
    assert {p.name for p in matches} == {"VDDIO1", "VDDIO2"}


def test_pinout_iter_and_len() -> None:
    from datasheet_types.pinout import Pin, Pinout

    pinout = Pinout(pins=[
        Pin(numbers=["1"], name="A", type="input"),
        Pin(numbers=["2"], name="B", type="output"),
    ])
    assert len(pinout) == 2
    names = [p.name for p in pinout]
    assert names == ["A", "B"]


def test_pinout_equality() -> None:
    """Pinout compares by content, not identity — behaves as a value type."""
    from datasheet_types.pinout import Pin, Pinout

    p1 = Pin(numbers=["1"], name="VIN", type="power_in")
    p2 = Pin(numbers=["2"], name="GND", type="power_in")
    a = Pinout(pins=[p1, p2])
    b = Pinout(pins=[Pin(numbers=["1"], name="VIN", type="power_in"),
                     Pin(numbers=["2"], name="GND", type="power_in")])
    c = Pinout(pins=[p2, p1])  # different order

    assert a == b                # same content → equal
    assert a != c                # different order → not equal
    assert a != "not-a-pinout"   # different type → not equal (NotImplemented falls back)


def test_pinout_roundtrip_through_codec() -> None:
    """Pinout serializes as a bare list (root-array shape)."""
    from datasheet_types.pinout import Pinout
    from datasheet_types.codec import from_dict, to_dict

    raw = [
        {"numbers": ["1"], "name": "VIN", "type": "power_in"},
        {"numbers": ["2"], "name": "GND", "type": "power_in"},
    ]
    pinout = from_dict(Pinout, raw)
    assert len(pinout) == 2
    # to_dict emits back to a bare list (NOT a dict with a 'pins' key).
    emitted = to_dict(pinout)
    assert isinstance(emitted, list)
    assert emitted[0]["name"] == "VIN"
    assert emitted[1]["name"] == "GND"
