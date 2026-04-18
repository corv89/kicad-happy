"""Contract test: thermal analyzer output validates against its declared schema."""
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
THERMAL = REPO_ROOT / "skills" / "kicad" / "scripts" / "analyze_thermal.py"
SCHEMATIC = REPO_ROOT / "skills" / "kicad" / "scripts" / "analyze_schematic.py"
PCB = REPO_ROOT / "skills" / "kicad" / "scripts" / "analyze_pcb.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "simple-project"


def _run(argv):
    return subprocess.run(
        [sys.executable, *argv],
        capture_output=True, text=True, check=True,
    ).stdout


def test_thermal_schema_is_draft_2020_12():
    out = _run([str(THERMAL), "--schema"])
    schema = json.loads(out)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_thermal_output_matches_schema(tmp_path):
    sch_json = tmp_path / "sch.json"
    pcb_json = tmp_path / "pcb.json"
    sch_json.write_text(_run([str(SCHEMATIC), str(FIXTURE / "simple.kicad_sch")]))
    pcb_json.write_text(_run([str(PCB), str(FIXTURE / "simple.kicad_pcb")]))
    th_output = _run([str(THERMAL), "--schematic", str(sch_json), "--pcb", str(pcb_json)])
    result = json.loads(th_output)
    schema = json.loads(_run([str(THERMAL), "--schema"]))
    Draft202012Validator(schema).validate(result)


def test_thermal_schema_version_is_1_4():
    # Just check the --schema declares 1.4.0 somewhere recognizable.
    schema = json.loads(_run([str(THERMAL), "--schema"]))
    sv = schema["properties"]["schema_version"]
    assert sv.get("const") == "1.4.0" or "1.4.0" in sv.get("description", "")
