"""Tests for shared envelope primitives."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "skills" / "kicad" / "scripts"))

from schema_codec import dataclass_to_json_schema  # noqa: E402
from analyzer_envelope import (  # noqa: E402
    TrustSummary,
    ByConfidence,
    ByEvidenceSource,
    BySeverity,
    BomCoverage,
    TitleBlock,
    Finding,
)
from jsonschema import Draft202012Validator  # noqa: E402


def _assert_valid(cls):
    schema = dataclass_to_json_schema(cls)
    Draft202012Validator.check_schema(schema)
    return schema


def test_by_confidence_shape():
    schema = _assert_valid(ByConfidence)
    props = schema["properties"]
    assert props["deterministic"]["type"] == "integer"
    assert props["heuristic"]["type"] == "integer"
    assert props["datasheet_backed"]["type"] == "integer"


def test_by_severity_matches_v1_3_vocab():
    schema = _assert_valid(BySeverity)
    assert set(schema["properties"].keys()) == {"error", "warning", "info"}


def test_trust_summary_fields():
    schema = _assert_valid(TrustSummary)
    props = schema["properties"]
    assert props["total_findings"]["type"] == "integer"
    assert props["trust_level"]["type"] == "string"
    assert props["by_confidence"]["type"] == "object"
    assert props["by_evidence_source"]["type"] == "object"
    assert props["provenance_coverage_pct"]["type"] == "number"
    # bom_coverage is schematic-only -> optional
    assert "bom_coverage" not in schema["required"]


def test_finding_required_fields():
    schema = _assert_valid(Finding)
    required = set(schema["required"])
    # Per v1.3 rich finding contract — these must always be present.
    assert "detector" in required
    assert "rule_id" in required
    assert "severity" in required
    assert "confidence" in required
    assert "evidence_source" in required
    assert "summary" in required


def test_title_block_all_optional():
    schema = _assert_valid(TitleBlock)
    # Title block fields are all optional (may be blank in a given .kicad_sch)
    assert schema["required"] == []
