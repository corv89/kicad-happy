"""Contract test: schematic analyzer output validates against its declared schema."""
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMATIC = REPO_ROOT / "skills" / "kicad" / "scripts" / "analyze_schematic.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "simple-project" / "simple.kicad_sch"


def _run(argv):
    return subprocess.run(
        [sys.executable, *argv],
        capture_output=True, text=True, check=True,
    ).stdout


def test_schematic_schema_is_draft_2020_12():
    schema = json.loads(_run([str(SCHEMATIC), "--schema"]))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_schematic_output_validates():
    out = _run([str(SCHEMATIC), str(FIXTURE)])
    result = json.loads(out)
    schema = json.loads(_run([str(SCHEMATIC), "--schema"]))
    Draft202012Validator(schema).validate(result)


def test_schematic_declares_pin_coverage_warnings_as_optional():
    """KH-323: pin_coverage_warnings must be an OPTIONAL documented field."""
    schema = json.loads(_run([str(SCHEMATIC), "--schema"]))
    assert "pin_coverage_warnings" in schema["properties"]
    assert "pin_coverage_warnings" not in schema["required"]


def test_schematic_schema_version_and_analyzer_type_are_const():
    schema = json.loads(_run([str(SCHEMATIC), "--schema"]))
    assert schema["properties"]["schema_version"]["const"] == "1.4.0"
    assert schema["properties"]["analyzer_type"]["const"] == "schematic"


def test_schematic_runtime_schema_version_is_1_4_0():
    out = _run([str(SCHEMATIC), str(FIXTURE)])
    result = json.loads(out)
    assert result["schema_version"] == "1.4.0"
