"""Thermal analyzer output envelope (v1.4 SOT)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

# Make shared primitives importable when this module is loaded from
# tests, from the analyzer, or via generator tooling.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from analyzer_envelope import TrustSummary, Finding, BySeverity  # noqa: E402


@dataclass
class ThermalSummary:
    total_findings: int = field(metadata={"description": "Count of findings[]."})
    components_assessed: int = field(metadata={
        "description": "Number of components for which thermal estimation ran."})
    active: int = field(metadata={
        "description": "Non-suppressed findings count."})
    suppressed: int = field(metadata={
        "description": "Findings suppressed by project config."})
    by_severity: BySeverity = field(metadata={
        "description": "Breakdown by severity bucket."})
    thermal_score: float = field(metadata={
        "description": "Composite thermal risk score, 0-100."})


@dataclass
class ThermalMissingInfo:
    default_rtheta_ja: list[str] = field(metadata={
        "description": "Component references that used a package-default Rθ_JA "
                       "because no datasheet value was available."})
    default_tj_max: list[str] = field(metadata={
        "description": "Component references that used a default max junction "
                       "temperature (typically 125 °C)."})


@dataclass
class ThermalEnvelope:
    """Top-level output of analyze_thermal.py."""
    analyzer_type: str = field(metadata={
        "description": "Always 'thermal'."})
    schema_version: str = field(metadata={
        "description": "Schema semver. Value: '1.4.0' at Track 1.1 landing."})
    summary: ThermalSummary = field(metadata={
        "description": "Roll-up summary of thermal analysis."})
    findings: list[Finding] = field(metadata={
        "description": "All thermal findings: TS-001..005, TP-001..002, TH-DET assessments."})
    trust_summary: TrustSummary = field(metadata={
        "description": "Trust posture rollup."})
    elapsed_s: float = field(metadata={
        "description": "Wall-clock analysis time in seconds."})
    missing_info: Optional[ThermalMissingInfo] = field(default=None, metadata={
        "description": "Emitted when any component used default thermal params."})
