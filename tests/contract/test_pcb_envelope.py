"""Contract test: PCB analyzer output validates against its declared schema."""
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
PCB = REPO_ROOT / "skills" / "kicad" / "scripts" / "analyze_pcb.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "simple-project" / "simple.kicad_pcb"


def _run(argv):
    return subprocess.run(
        [sys.executable, *argv],
        capture_output=True, text=True, check=True,
    ).stdout


def test_pcb_schema_is_draft_2020_12():
    schema = json.loads(_run([str(PCB), "--schema"]))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_pcb_output_validates():
    result = json.loads(_run([str(PCB), str(FIXTURE)]))
    schema = json.loads(_run([str(PCB), "--schema"]))
    Draft202012Validator(schema).validate(result)


def test_pcb_schema_version_and_analyzer_type_are_const():
    schema = json.loads(_run([str(PCB), "--schema"]))
    assert schema["properties"]["schema_version"]["const"] == "1.4.0"
    assert schema["properties"]["analyzer_type"]["const"] == "pcb"


def test_pcb_runtime_schema_version_is_1_4_0():
    result = json.loads(_run([str(PCB), str(FIXTURE)]))
    assert result["schema_version"] == "1.4.0"
