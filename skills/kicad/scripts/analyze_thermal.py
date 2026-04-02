#!/usr/bin/env python3
"""
Thermal hotspot estimator for KiCad designs.

Consumes schematic and PCB analyzer JSON outputs, models each power-dissipating
component as a point heat source, estimates junction temperatures, and flags
components approaching or exceeding rated limits.

Usage:
    python3 analyze_thermal.py --schematic analysis.json --pcb pcb.json
    python3 analyze_thermal.py -s analysis.json -p pcb.json --output thermal.json
    python3 analyze_thermal.py -s analysis.json -p pcb.json --text
    python3 analyze_thermal.py -s analysis.json -p pcb.json --ambient 40

Requires both schematic and PCB JSON (schematic for power data, PCB for copper
and thermal via data). Zero dependencies — Python 3.8+ stdlib only.
"""

import argparse
import json
import math
import os
import re
import sys
import time


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_AMBIENT_C = 25.0
DEFAULT_TJ_MAX_C = 125.0
DEFAULT_RTHETA_JA = 150.0  # Conservative fallback °C/W
MIN_PDISS_W = 0.01  # 10mW threshold — below this, thermal is negligible

SEVERITY_WEIGHTS = {
    'CRITICAL': 15, 'HIGH': 8, 'MEDIUM': 3, 'LOW': 1, 'INFO': 0,
}
MAX_FINDINGS_PER_RULE = 5

# Switching regulator efficiency defaults by topology
SWITCHING_EFFICIENCY = {
    'buck': 0.87,
    'boost': 0.85,
    'buck-boost': 0.83,
    'switching': 0.85,  # generic
}

# ---------------------------------------------------------------------------
# Package thermal resistance lookup (Rθ_JA in °C/W)
# Values are JEDEC still-air conditions (no enhanced copper pour).
# PCB corrections applied separately.
# ---------------------------------------------------------------------------

PACKAGE_THERMAL_RESISTANCE = [
    # (regex pattern on footprint library string, Rθ_JA °C/W)
    # Order matters — first match wins. More specific patterns first.
    # Discrete power packages
    (r"TO-263|D2PAK", 30.0),
    (r"TO-252|DPAK", 40.0),
    (r"TO-220", 25.0),
    (r"TO-92", 200.0),
    (r"SOT-223", 60.0),
    (r"SOT-89", 100.0),
    (r"SOT-23", 250.0),  # SOT-23-3, SOT-23-5, SOT-23-6
    (r"SOT-363|SC-70", 300.0),
    # QFN/DFN — size matters
    (r"(?:QFN|DFN).*7[xX×]7", 20.0),
    (r"(?:QFN|DFN).*6[xX×]6", 22.0),
    (r"(?:QFN|DFN).*5[xX×]5", 25.0),
    (r"(?:QFN|DFN).*4[xX×]4", 35.0),
    (r"(?:QFN|DFN).*3[xX×]3", 50.0),
    (r"(?:QFN|DFN).*2[xX×]2", 70.0),
    (r"QFN|DFN", 40.0),  # generic QFN
    # TQFP/LQFP — pin count
    (r"[TL]QFP.*144", 30.0),
    (r"[TL]QFP.*100", 35.0),
    (r"[TL]QFP.*(?:64|80)", 40.0),
    (r"[TL]QFP.*48", 50.0),
    (r"[TL]QFP.*32", 60.0),
    (r"[TL]QFP", 50.0),
    # SOIC/SOP
    (r"SOIC.*16|SOP.*16", 80.0),
    (r"SOIC.*8|SOP.*8", 120.0),
    (r"SOIC|SOP", 100.0),
    # TSSOP/MSOP
    (r"TSSOP.*(?:20|24|28)", 80.0),
    (r"TSSOP.*(?:14|16)", 100.0),
    (r"TSSOP.*8", 150.0),
    (r"MSOP", 200.0),
    # BGA
    (r"BGA.*(?:256|324|400)", 20.0),
    (r"BGA", 25.0),
    # Passives — resistor packages
    (r"2512", 40.0),
    (r"2010", 60.0),
    (r"1210", 80.0),
    (r"1206", 100.0),
    (r"0805", 150.0),
    (r"0603", 200.0),
    (r"0402", 250.0),
    (r"0201", 350.0),
]

# Compiled patterns for performance
_COMPILED_PATTERNS = [(re.compile(pat, re.IGNORECASE), rtheta)
                      for pat, rtheta in PACKAGE_THERMAL_RESISTANCE]


# ---------------------------------------------------------------------------
# Package classification
# ---------------------------------------------------------------------------

def _classify_package(library_str: str) -> tuple:
    """Extract package type and Rθ_JA from PCB footprint library string.

    Returns (package_name, rtheta_ja). Falls back to ("unknown", DEFAULT_RTHETA_JA).
    """
    if not library_str:
        return ("unknown", DEFAULT_RTHETA_JA)

    # Use the footprint name (after the colon)
    name = library_str.split(":")[-1] if ":" in library_str else library_str

    for pattern, rtheta in _COMPILED_PATTERNS:
        if pattern.search(name):
            # Extract the matched portion as the package name
            m = pattern.search(name)
            return (m.group(0), rtheta)

    return ("unknown", DEFAULT_RTHETA_JA)


# ---------------------------------------------------------------------------
# Datasheet thermal data lookup
# ---------------------------------------------------------------------------

def _sanitize_mpn(mpn: str) -> str:
    """Sanitize MPN for filesystem lookup."""
    return re.sub(r'[^\w\-.]', '_', mpn.strip())


def _get_datasheet_thermal(mpn: str, extract_dir: str) -> dict:
    """Look up thermal data from datasheet extraction cache.

    Returns dict with optional keys: tj_max_c, temp_max_c.
    """
    if not mpn or not extract_dir:
        return {}

    safe = _sanitize_mpn(mpn)
    path = os.path.join(extract_dir, f"{safe}.json")
    if not os.path.isfile(path):
        return {}

    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    result = {}
    abs_max = data.get("absolute_maximum_ratings", {})
    if isinstance(abs_max, dict):
        tj = abs_max.get("junction_temp_max_c")
        if isinstance(tj, (int, float)) and tj > 0:
            result["tj_max_c"] = float(tj)

    rec_op = data.get("recommended_operating_conditions", {})
    if isinstance(rec_op, dict):
        tmax = rec_op.get("temp_max_c")
        if isinstance(tmax, (int, float)) and tmax > 0:
            result["temp_max_c"] = float(tmax)

    return result


# ---------------------------------------------------------------------------
# Power dissipation estimators
# ---------------------------------------------------------------------------

def _estimate_all_power_dissipation(schematic: dict) -> list:
    """Build list of all components with estimated power dissipation.

    Returns list of dicts with ref, value, type, pdiss_w, pdiss_source, etc.
    Only includes components with P > MIN_PDISS_W.
    """
    results = []
    signal = schematic.get("signal_analysis", {})
    power_budget = schematic.get("power_budget", {})
    seen_refs = set()

    # 1. Linear regulators (LDOs) — use pre-computed power_dissipation
    for reg in signal.get("power_regulators", []):
        ref = reg.get("ref", "")
        topology = reg.get("topology", "").lower()
        pdiss = reg.get("power_dissipation", {})

        if topology in ("ldo", "linear") and isinstance(pdiss, dict):
            p_w = pdiss.get("estimated_pdiss_W", 0)
            if p_w and p_w > MIN_PDISS_W:
                results.append({
                    "ref": ref,
                    "value": reg.get("value", ""),
                    "type": "ldo",
                    "pdiss_w": round(p_w, 4),
                    "pdiss_source": (f"({pdiss.get('vin_estimated_V', '?')}V - "
                                     f"{pdiss.get('vout_V', '?')}V) × "
                                     f"{pdiss.get('estimated_iout_A', '?')}A"),
                    "vin_v": pdiss.get("vin_estimated_V"),
                    "vout_v": pdiss.get("vout_V"),
                    "iout_a": pdiss.get("estimated_iout_A"),
                })
                seen_refs.add(ref)

    # 2. Switching regulators — estimate from efficiency
    for reg in signal.get("power_regulators", []):
        ref = reg.get("ref", "")
        if ref in seen_refs:
            continue
        topology = reg.get("topology", "").lower()
        if topology in ("ldo", "linear", ""):
            continue

        # Get output current estimate from power_budget
        output_rail = reg.get("output_rail", "")
        iout_a = 0
        for rail_name, rail_data in power_budget.get("rails", {}).items():
            if isinstance(rail_data, dict) and rail_name == output_rail:
                iout_a = rail_data.get("estimated_load_mA", 0) / 1000.0
                break

        vout = reg.get("estimated_vout")
        if not vout or not iout_a or iout_a <= 0:
            continue

        eta = SWITCHING_EFFICIENCY.get(topology, 0.85)
        p_out = vout * iout_a
        p_in = p_out / eta
        p_loss = p_in - p_out

        if p_loss > MIN_PDISS_W:
            results.append({
                "ref": ref,
                "value": reg.get("value", ""),
                "type": "switching_reg",
                "pdiss_w": round(p_loss, 4),
                "pdiss_source": f"{vout}V × {iout_a:.3f}A × (1/{eta:.0%} - 1)",
                "vout_v": vout,
                "iout_a": iout_a,
            })
            seen_refs.add(ref)

    # 3. Current sense shunt resistors — P = I²R
    for cs in signal.get("current_sense", []):
        shunt = cs.get("shunt", {})
        if not isinstance(shunt, dict):
            continue
        ref = shunt.get("ref", "")
        if ref in seen_refs:
            continue
        r_ohms = shunt.get("ohms", 0)
        i_max = cs.get("max_current_100mV_A", 0)
        if not r_ohms or not i_max:
            continue

        p_w = i_max * i_max * r_ohms
        if p_w > MIN_PDISS_W:
            results.append({
                "ref": ref,
                "value": shunt.get("value", ""),
                "type": "shunt_resistor",
                "pdiss_w": round(p_w, 4),
                "pdiss_source": f"{i_max:.3f}A² × {r_ohms}Ω",
            })
            seen_refs.add(ref)

    return results


# ---------------------------------------------------------------------------
# PCB thermal correction
# ---------------------------------------------------------------------------

def _get_pcb_thermal_correction(ref: str, pcb: dict) -> dict:
    """Compute PCB-based correction factor for a component's Rθ_JA.

    Returns dict with correction_factor (0.4-1.0), has_thermal_pad, etc.
    """
    result = {
        "correction_factor": 1.0,
        "has_thermal_pad": False,
        "thermal_vias": 0,
        "notes": [],
    }

    # Check thermal_pad_vias for this component
    for tpv in pcb.get("thermal_pad_vias", []):
        if not isinstance(tpv, dict):
            continue
        if tpv.get("component") != ref:
            continue

        result["has_thermal_pad"] = True
        result["thermal_vias"] = tpv.get("via_count", 0)
        adequacy = tpv.get("adequacy", "none")

        if adequacy == "good":
            result["correction_factor"] *= 0.50
            result["notes"].append(
                f"thermal pad with {tpv.get('via_count', 0)} vias (good)")
        elif adequacy == "adequate":
            result["correction_factor"] *= 0.65
            result["notes"].append(
                f"thermal pad with {tpv.get('via_count', 0)} vias (adequate)")
        elif adequacy == "insufficient":
            result["correction_factor"] *= 0.80
            result["notes"].append(
                f"thermal pad with {tpv.get('via_count', 0)} vias (insufficient)")
        else:
            result["notes"].append("thermal pad but no vias")
        break

    # Check thermal_analysis.thermal_pads for additional info
    thermal = pcb.get("thermal_analysis", {})
    for tp in thermal.get("thermal_pads", []):
        if not isinstance(tp, dict):
            continue
        if tp.get("component") != ref:
            continue
        nearby = tp.get("nearby_thermal_vias", 0)
        if nearby > 4 and not result["has_thermal_pad"]:
            result["correction_factor"] *= 0.70
            result["notes"].append(f"{nearby} nearby thermal vias")
        break

    # Clamp to reasonable range
    result["correction_factor"] = max(0.40, min(1.0, result["correction_factor"]))
    return result


# ---------------------------------------------------------------------------
# Junction temperature computation
# ---------------------------------------------------------------------------

def _get_footprint_map(pcb: dict) -> dict:
    """Build ref -> footprint dict from PCB data."""
    fp_map = {}
    for fp in pcb.get("footprints", []):
        if isinstance(fp, dict) and "reference" in fp:
            fp_map[fp["reference"]] = fp
    return fp_map


def _compute_junction_temps(power_comps: list, pcb: dict,
                            extract_dir: str, ambient_c: float) -> list:
    """Compute estimated junction temperature for each power component."""
    fp_map = _get_footprint_map(pcb)
    assessments = []

    for comp in power_comps:
        ref = comp["ref"]
        fp = fp_map.get(ref, {})

        # Package Rθ_JA
        library = fp.get("library", fp.get("lib_id", ""))
        pkg_name, pkg_rtheta = _classify_package(library)
        rtheta_source = "package_table" if pkg_name != "unknown" else "default"

        # Datasheet lookup for Tj_max
        mpn = ""
        for c in pcb.get("footprints", []):
            if isinstance(c, dict) and c.get("reference") == ref:
                mpn = c.get("mpn", "") or c.get("value", "")
                break
        ds_thermal = _get_datasheet_thermal(mpn, extract_dir) if extract_dir else {}

        tj_max = ds_thermal.get("tj_max_c", DEFAULT_TJ_MAX_C)
        tj_max_source = "datasheet" if "tj_max_c" in ds_thermal else "default_125"

        # PCB correction
        pcb_corr = _get_pcb_thermal_correction(ref, pcb)
        rtheta_effective = pkg_rtheta * pcb_corr["correction_factor"]

        # Junction temperature: Tj = Ta + P × Rθ_JA
        pdiss = comp["pdiss_w"]
        tj = ambient_c + pdiss * rtheta_effective
        margin = tj_max - tj

        # Position from PCB
        position = None
        if "x" in fp and "y" in fp:
            position = {"x": fp["x"], "y": fp["y"]}

        assessments.append({
            "ref": ref,
            "value": comp.get("value", ""),
            "component_type": comp["type"],
            "pdiss_w": pdiss,
            "pdiss_source": comp.get("pdiss_source", ""),
            "package": pkg_name,
            "rtheta_ja_raw": round(pkg_rtheta, 1),
            "rtheta_ja_source": rtheta_source,
            "pcb_correction": round(pcb_corr["correction_factor"], 2),
            "pcb_correction_notes": pcb_corr["notes"],
            "rtheta_ja_effective": round(rtheta_effective, 1),
            "ambient_c": ambient_c,
            "tj_estimated_c": round(tj, 1),
            "tj_max_c": tj_max,
            "tj_max_source": tj_max_source,
            "margin_c": round(margin, 1),
            "position": position,
        })

    # Sort by Tj descending (hottest first)
    assessments.sort(key=lambda a: -a["tj_estimated_c"])
    return assessments


# ---------------------------------------------------------------------------
# Finding generation
# ---------------------------------------------------------------------------

def _generate_findings(assessments: list) -> list:
    """Generate thermal findings from assessments."""
    findings = []

    for a in assessments:
        ref = a["ref"]
        val = a["value"]
        tj = a["tj_estimated_c"]
        tj_max = a["tj_max_c"]
        margin = a["margin_c"]
        pdiss = a["pdiss_w"]
        pkg = a["package"]

        label = f"{ref} ({val})" if val else ref

        if tj > tj_max:
            findings.append({
                "category": "thermal_safety",
                "severity": "CRITICAL",
                "rule_id": "TS-001",
                "title": f"{label} estimated Tj {tj:.0f}°C exceeds abs max {tj_max:.0f}°C",
                "description": (
                    f"{a['component_type']} dissipates {pdiss:.3f}W in {pkg} package "
                    f"(Rθ_JA={a['rtheta_ja_effective']:.0f}°C/W). "
                    f"Source: {a['pdiss_source']}."
                ),
                "components": [ref],
                "recommendation": (
                    "Reduce power dissipation (lower Vin, reduce load), improve "
                    "thermal path (add thermal vias, larger copper pour), or use "
                    "a more efficient topology (switching regulator instead of LDO)."
                ),
            })
        elif margin < 15:
            findings.append({
                "category": "thermal_safety",
                "severity": "HIGH",
                "rule_id": "TS-002",
                "title": f"{label} estimated Tj {tj:.0f}°C — only {margin:.0f}°C margin to abs max",
                "description": (
                    f"{a['component_type']} dissipates {pdiss:.3f}W in {pkg} package "
                    f"(Rθ_JA={a['rtheta_ja_effective']:.0f}°C/W). "
                    f"Margin to {tj_max:.0f}°C abs max is {margin:.0f}°C — "
                    f"may exceed limit at elevated ambient."
                ),
                "components": [ref],
                "recommendation": (
                    "Verify thermal design at worst-case ambient temperature. "
                    "Consider improving thermal path or reducing input-output "
                    "voltage differential."
                ),
            })
        elif tj > 85:
            findings.append({
                "category": "thermal_safety",
                "severity": "MEDIUM",
                "rule_id": "TS-003",
                "title": f"{label} estimated Tj {tj:.0f}°C may affect nearby components",
                "description": (
                    f"{a['component_type']} runs hot at {tj:.0f}°C "
                    f"({pdiss:.3f}W in {pkg}). "
                    f"Nearby MLCCs may lose capacitance due to temperature "
                    f"coefficient effects."
                ),
                "components": [ref],
                "recommendation": (
                    "Verify nearby ceramic capacitors maintain adequate "
                    "capacitance at this temperature. Consider spacing "
                    "temperature-sensitive components away from heat source."
                ),
            })
        elif pdiss > 0.1:
            findings.append({
                "category": "thermal_safety",
                "severity": "INFO",
                "rule_id": "TS-005",
                "title": f"{label} Tj {tj:.0f}°C, margin {margin:.0f}°C",
                "description": (
                    f"{a['component_type']} dissipates {pdiss:.3f}W in {pkg} — "
                    f"within safe thermal limits."
                ),
                "components": [ref],
                "recommendation": "",
            })

    # TS-004: High-power component with no thermal vias
    for a in assessments:
        if a["pdiss_w"] > 0.5 and a["pcb_correction"] >= 0.95:
            ref = a["ref"]
            val = a["value"]
            label = f"{ref} ({val})" if val else ref
            findings.append({
                "category": "thermal_safety",
                "severity": "MEDIUM",
                "rule_id": "TS-004",
                "title": f"{label} dissipates {a['pdiss_w']:.2f}W with no thermal vias",
                "description": (
                    f"{a['component_type']} in {a['package']} package dissipates "
                    f"{a['pdiss_w']:.3f}W but no thermal pad vias were detected. "
                    f"Heat removal relies entirely on surface copper."
                ),
                "components": [ref],
                "recommendation": (
                    "Add thermal vias under the component's thermal pad or "
                    "exposed pad. Minimum 5 vias for QFN, more for larger pads."
                ),
            })

    return findings


# ---------------------------------------------------------------------------
# Thermal proximity warnings
# ---------------------------------------------------------------------------

def _check_thermal_proximity(assessments: list, pcb: dict) -> list:
    """Check for temperature-sensitive components near hot spots."""
    findings = []
    fp_map = _get_footprint_map(pcb)

    # Find hot components (Tj > 70°C) with known positions
    hot_comps = [a for a in assessments
                 if a["tj_estimated_c"] > 70 and a.get("position")]

    if not hot_comps:
        return findings

    # Find capacitors in PCB footprints
    caps = []
    for fp in pcb.get("footprints", []):
        if not isinstance(fp, dict):
            continue
        ref = fp.get("reference", "")
        if not ref.startswith("C"):
            continue
        if "x" not in fp or "y" not in fp:
            continue
        value = fp.get("value", "").lower()
        is_elec = any(k in value for k in ("elec", "tant", "polar", "aluminum"))
        caps.append({
            "ref": ref, "x": fp["x"], "y": fp["y"],
            "value": fp.get("value", ""), "is_electrolytic": is_elec,
        })

    # Check proximity
    for hot in hot_comps:
        hx, hy = hot["position"]["x"], hot["position"]["y"]
        for cap in caps:
            dx = cap["x"] - hx
            dy = cap["y"] - hy
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > 10.0:  # 10mm threshold
                continue

            hot_label = f"{hot['ref']} ({hot.get('value', '')})"
            if cap["is_electrolytic"]:
                findings.append({
                    "category": "thermal_proximity",
                    "severity": "MEDIUM",
                    "rule_id": "TP-002",
                    "title": (f"Electrolytic {cap['ref']} ({cap['value']}) "
                              f"is {dist:.1f}mm from {hot_label} "
                              f"(Tj={hot['tj_estimated_c']:.0f}°C)"),
                    "description": (
                        "Electrolytic and tantalum capacitors have reduced "
                        "lifetime at elevated temperatures. Every 10°C above "
                        "rated temperature halves expected lifetime."
                    ),
                    "components": [cap["ref"], hot["ref"]],
                    "recommendation": (
                        "Move capacitor away from heat source or use a "
                        "ceramic capacitor rated for higher temperature."
                    ),
                })
            else:
                findings.append({
                    "category": "thermal_proximity",
                    "severity": "LOW",
                    "rule_id": "TP-001",
                    "title": (f"MLCC {cap['ref']} is {dist:.1f}mm from "
                              f"{hot_label} (Tj={hot['tj_estimated_c']:.0f}°C)"),
                    "description": (
                        "Ceramic capacitors lose effective capacitance at "
                        "elevated temperatures. X7R loses ~15% at 85°C, "
                        "X5R loses ~30%."
                    ),
                    "components": [cap["ref"], hot["ref"]],
                    "recommendation": (
                        "Verify capacitor maintains adequate capacitance "
                        "at the elevated temperature, or use C0G/NP0 "
                        "dielectric for temperature-critical applications."
                    ),
                })

    return findings


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_thermal_score(findings: list) -> int:
    """Compute thermal safety score from 0 (worst) to 100 (best)."""
    by_rule = {}
    for f in findings:
        rule = f.get("rule_id", "")
        by_rule.setdefault(rule, []).append(f)

    penalty = 0
    sev_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, 'INFO': 4}
    for rule, rule_findings in by_rule.items():
        rule_findings.sort(key=lambda f: sev_order.get(f.get('severity', 'INFO'), 4))
        for f in rule_findings[:MAX_FINDINGS_PER_RULE]:
            penalty += SEVERITY_WEIGHTS.get(f.get('severity', 'INFO'), 0)

    return max(0, min(100, 100 - penalty))


# ---------------------------------------------------------------------------
# Board thermal summary
# ---------------------------------------------------------------------------

def _board_summary(assessments: list, ambient_c: float) -> dict:
    """Generate board-level thermal statistics."""
    if not assessments:
        return {
            "total_board_dissipation_w": 0,
            "components_analyzed": 0,
            "components_above_85c": 0,
            "components_above_tjmax": 0,
            "ambient_c": ambient_c,
        }

    total_p = sum(a["pdiss_w"] for a in assessments)
    above_85 = sum(1 for a in assessments if a["tj_estimated_c"] > 85)
    above_max = sum(1 for a in assessments if a["margin_c"] < 0)
    hottest = max(assessments, key=lambda a: a["tj_estimated_c"])

    return {
        "total_board_dissipation_w": round(total_p, 3),
        "hottest_component": {
            "ref": hottest["ref"],
            "tj_estimated_c": hottest["tj_estimated_c"],
        },
        "components_analyzed": len(assessments),
        "components_above_85c": above_85,
        "components_above_tjmax": above_max,
        "ambient_c": ambient_c,
    }


# ---------------------------------------------------------------------------
# Text report formatter
# ---------------------------------------------------------------------------

def format_text_report(result: dict) -> str:
    """Format thermal analysis as human-readable text."""
    lines = []
    summary = result.get("summary", {})
    findings = result.get("findings", [])
    assessments = result.get("thermal_assessments", [])

    lines.append("=" * 60)
    lines.append("THERMAL HOTSPOT ANALYSIS")
    lines.append("=" * 60)
    lines.append("")

    score = summary.get("thermal_score", 0)
    lines.append(f"Thermal score:   {score}/100")
    lines.append(f"Ambient temp:    {summary.get('ambient_c', 25)}°C")
    total_p = summary.get("total_board_dissipation_w", 0)
    lines.append(f"Total dissipation: {total_p:.3f}W")
    lines.append("")

    lines.append(f"Total checks:  {summary.get('total_checks', 0)}")
    lines.append(f"  CRITICAL:    {summary.get('critical', 0)}")
    lines.append(f"  HIGH:        {summary.get('high', 0)}")
    lines.append(f"  MEDIUM:      {summary.get('medium', 0)}")
    lines.append(f"  LOW:         {summary.get('low', 0)}")
    lines.append(f"  INFO:        {summary.get('info', 0)}")
    lines.append("")

    # Component thermal table
    if assessments:
        lines.append("-" * 60)
        lines.append("Component Thermal Summary")
        lines.append("-" * 60)
        lines.append(f"{'Ref':<8} {'Type':<14} {'P(W)':<8} {'Rθ_JA':<8} "
                     f"{'Tj(°C)':<8} {'Tj_max':<8} {'Margin':<8}")
        lines.append("-" * 60)
        for a in assessments:
            lines.append(
                f"{a['ref']:<8} {a['component_type']:<14} "
                f"{a['pdiss_w']:<8.3f} {a['rtheta_ja_effective']:<8.1f} "
                f"{a['tj_estimated_c']:<8.1f} {a['tj_max_c']:<8.0f} "
                f"{a['margin_c']:<8.1f}"
            )
        lines.append("")

    # Findings by severity
    if findings:
        lines.append("-" * 60)
        lines.append("Findings")
        lines.append("-" * 60)

        for f in findings:
            sev = f["severity"]
            lines.append(f"  [{sev}] {f['rule_id']}: {f['title']}")
            desc = f.get("description", "")
            for i in range(0, len(desc), 70):
                prefix = "    " if i == 0 else "      "
                lines.append(prefix + desc[i:i + 70])
            if f.get("recommendation"):
                lines.append(f"    -> {f['recommendation']}")
            lines.append("")
    else:
        lines.append("No thermal findings.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Thermal hotspot estimator for KiCad designs"
    )
    parser.add_argument("--schematic", "-s", required=True,
                        help="Schematic analyzer JSON (from analyze_schematic.py)")
    parser.add_argument("--pcb", "-p", required=True,
                        help="PCB analyzer JSON (from analyze_pcb.py)")
    parser.add_argument("--output", "-o",
                        help="Output JSON file path (default: stdout)")
    parser.add_argument("--text", action="store_true",
                        help="Print human-readable text report")
    parser.add_argument("--ambient", type=float, default=DEFAULT_AMBIENT_C,
                        help=f"Ambient temperature in °C (default: {DEFAULT_AMBIENT_C})")
    parser.add_argument("--datasheets", "-d",
                        help="Path to datasheets/extracted/ directory")
    args = parser.parse_args()

    # Load inputs
    try:
        with open(args.schematic) as f:
            schematic = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading schematic: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.pcb) as f:
            pcb = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading PCB: {e}", file=sys.stderr)
        sys.exit(1)

    # Resolve datasheets directory
    extract_dir = args.datasheets
    if not extract_dir:
        sch_file = schematic.get("file", "")
        if sch_file:
            candidate = os.path.join(os.path.dirname(sch_file),
                                     "datasheets", "extracted")
            if os.path.isdir(candidate):
                extract_dir = candidate

    t0 = time.monotonic()

    # Estimate power dissipation
    power_comps = _estimate_all_power_dissipation(schematic)

    # Compute junction temperatures
    assessments = _compute_junction_temps(
        power_comps, pcb, extract_dir, args.ambient)

    # Generate findings
    findings = _generate_findings(assessments)
    findings.extend(_check_thermal_proximity(assessments, pcb))

    # Score
    score = compute_thermal_score(findings)

    # Severity counts
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
    for f in findings:
        sev = f.get("severity", "INFO")
        counts[sev] = counts.get(sev, 0) + 1

    # Board summary
    board = _board_summary(assessments, args.ambient)

    elapsed = time.monotonic() - t0

    result = {
        "summary": {
            "total_checks": len(findings),
            "critical": counts["CRITICAL"],
            "high": counts["HIGH"],
            "medium": counts["MEDIUM"],
            "low": counts["LOW"],
            "info": counts["INFO"],
            "thermal_score": score,
            **board,
        },
        "findings": findings,
        "thermal_assessments": assessments,
        "elapsed_s": round(elapsed, 3),
    }

    # Output
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Thermal analysis complete: {len(findings)} findings "
              f"(score {score}/100) -> {args.output}", file=sys.stderr)
    elif args.text:
        print(format_text_report(result))
    else:
        json.dump(result, sys.stdout, indent=2)
        print(file=sys.stdout)


if __name__ == "__main__":
    main()
