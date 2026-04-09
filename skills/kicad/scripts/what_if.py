#!/usr/bin/env python3
"""
Interactive "What-If" parameter sweep for KiCad designs.

Patches component values in analyzer JSON, re-runs affected subcircuit
calculations (and optionally SPICE simulations), and shows before/after
impact on circuit behavior.

Usage:
    python3 what_if.py analysis.json R5=4.7k
    python3 what_if.py analysis.json R5=4.7k C3=22n
    python3 what_if.py analysis.json R5=4.7k --spice
    python3 what_if.py analysis.json R5=4.7k --output patched.json
    python3 what_if.py analysis.json R5=4.7k --text

Zero dependencies — Python 3.8+ stdlib only.
"""

import argparse
import copy
import json
import math
import os
import sys
from dataclasses import dataclass

# Allow imports from same directory and spice scripts
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "spice", "scripts"))

from kicad_utils import parse_value


@dataclass
class Change:
    ref: str
    value: float
    value_str: str
    tolerance: float  # None = no tolerance specified; will be used in Task 3


@dataclass
class SweepSpec:
    ref: str
    values: list
    value_strs: list
    tolerance: float  # None = no tolerance; will be used in Task 3


# Value key -> unit name for display
_VALUE_UNITS = {"ohms": "ohms", "farads": "F", "henries": "H"}

# Derived fields to compare per detection type
_DERIVED_FIELDS = {
    "rc_filters": ["cutoff_hz"],
    "lc_filters": ["resonant_hz", "impedance_ohms"],
    "voltage_dividers": ["ratio"],
    "feedback_networks": ["ratio"],
    "opamp_circuits": ["gain", "gain_dB"],
    "crystal_circuits": ["effective_load_pF"],
    "current_sense": ["max_current_50mV_A", "max_current_100mV_A"],
    "power_regulators": ["estimated_vout"],
}


# ---------------------------------------------------------------------------
# Parse change specifications
# ---------------------------------------------------------------------------

def _parse_changes(change_args: list) -> tuple:
    """Parse REF=VALUE pairs, detecting sweep syntax.

    Returns (changes_dict, sweep_or_none).
    Sweep: R5=1k,2.2k,4.7k (comma) or R5=1k..100k:10 (log range).
    Only one component may use sweep syntax.
    """
    changes = {}
    sweep = None

    for arg in change_args:
        if "=" not in arg:
            print(f"Error: invalid change '{arg}' — expected REF=VALUE", file=sys.stderr)
            sys.exit(1)
        ref, val_str = arg.split("=", 1)
        ref = ref.strip()
        val_str = val_str.strip()

        # Component type hint
        prefix = ref.rstrip("0123456789")
        ctype = None
        if prefix in ("C", "VC"):
            ctype = "capacitor"
        elif prefix in ("L",):
            ctype = "inductor"

        # Extract tolerance suffix before checking sweep syntax
        tolerance = None
        for tol_sep in ("\u00b1", "+-"):
            if tol_sep in val_str:
                main_part, tol_part = val_str.rsplit(tol_sep, 1)
                tol_str = tol_part.strip().rstrip("%")
                try:
                    tolerance = float(tol_str) / 100.0
                except ValueError:
                    tolerance = None
                    break
                val_str = main_part.strip()
                break

        if ".." in val_str and ":" in val_str:
            # Log sweep: R5=1k..100k:10
            if sweep is not None:
                print("Error: only one component may use sweep syntax", file=sys.stderr)
                sys.exit(1)
            range_part, n_str = val_str.rsplit(":", 1)
            start_str, stop_str = range_part.split("..", 1)
            start = parse_value(start_str, component_type=ctype)
            stop = parse_value(stop_str, component_type=ctype)
            try:
                n = int(n_str)
            except ValueError:
                print(f"Error: invalid step count '{n_str}'", file=sys.stderr)
                sys.exit(1)
            if start is None or stop is None or n < 2:
                print(f"Error: invalid sweep '{val_str}'", file=sys.stderr)
                sys.exit(1)
            n = min(n, 50)
            values = [start * (stop / start) ** (i / (n - 1)) for i in range(n)]
            strs = [start_str] + [f"{v:.4g}" for v in values[1:-1]] + [stop_str]
            sweep = SweepSpec(ref=ref, values=values, value_strs=strs, tolerance=tolerance)

        elif "," in val_str:
            # Comma list: R5=1k,2.2k,4.7k
            if sweep is not None:
                print("Error: only one component may use sweep syntax", file=sys.stderr)
                sys.exit(1)
            parts = [p.strip() for p in val_str.split(",")]
            values = []
            for p in parts:
                v = parse_value(p, component_type=ctype)
                if v is None:
                    print(f"Error: cannot parse '{p}' in sweep for {ref}", file=sys.stderr)
                    sys.exit(1)
                values.append(v)
            sweep = SweepSpec(ref=ref, values=values, value_strs=parts, tolerance=tolerance)

        else:
            # Single value
            parsed = parse_value(val_str, component_type=ctype)
            if parsed is None:
                print(f"Error: cannot parse value '{val_str}' for {ref}", file=sys.stderr)
                sys.exit(1)
            changes[ref] = Change(ref=ref, value=parsed, value_str=val_str, tolerance=tolerance)

    return changes, sweep


# ---------------------------------------------------------------------------
# Find affected detections
# ---------------------------------------------------------------------------

def _find_refs_in_det(det: dict) -> dict:
    """Walk a detection dict and find all component refs with their value paths.

    Returns {ref: [(key_path_to_value, value_key), ...]}
    where key_path_to_value is like ["resistor"] and value_key is "ohms".
    """
    refs = {}

    def _check(sub, path):
        if not isinstance(sub, dict) or "ref" not in sub:
            return
        ref = sub["ref"]
        for vkey in ("ohms", "farads", "henries"):
            if vkey in sub and isinstance(sub[vkey], (int, float)):
                refs.setdefault(ref, []).append((path, vkey))

    for key, val in det.items():
        if isinstance(val, dict):
            _check(val, [key])
            for subkey, subval in val.items():
                if isinstance(subval, dict):
                    _check(subval, [key, subkey])
        elif isinstance(val, list):
            for idx, item in enumerate(val):
                if isinstance(item, dict):
                    _check(item, [key, idx])

    return refs


def _find_affected(signal_analysis: dict, changes: dict) -> list:
    """Find all detections referencing any changed component.

    Returns list of (det_type, index, det_dict, matched_refs_with_paths).
    """
    affected = []
    change_refs = set(changes.keys())

    for det_type, detections in signal_analysis.items():
        if not isinstance(detections, list):
            continue
        for idx, det in enumerate(detections):
            if not isinstance(det, dict):
                continue
            refs = _find_refs_in_det(det)
            matched = {r: paths for r, paths in refs.items() if r in change_refs}
            if matched:
                affected.append((det_type, idx, det, matched))

    return affected


# ---------------------------------------------------------------------------
# Apply changes and recalculate
# ---------------------------------------------------------------------------

def _apply_changes(det: dict, changes: dict, matched_refs: dict) -> dict:
    """Deep-copy detection, apply value changes, recalculate derived fields."""
    from spice_tolerance import _recalc_derived

    patched = copy.deepcopy(det)

    for ref, paths in matched_refs.items():
        new_val, new_str = changes[ref]
        for path, vkey in paths:
            # Navigate to the component sub-dict
            obj = patched
            for key in path:
                obj = obj[key]
            obj[vkey] = new_val
            # Update the value string too
            if "value" in obj:
                obj["value"] = new_str

    _recalc_derived(patched)
    return patched


# ---------------------------------------------------------------------------
# Before/after comparison
# ---------------------------------------------------------------------------

def _compare(original: dict, patched: dict, det_type: str) -> list:
    """Compare derived fields between original and patched detection.

    Returns list of {field, before, after, delta_pct} for changed fields.
    """
    fields = _DERIVED_FIELDS.get(det_type, [])
    # Also check common fields not in the registry
    for extra in ("cutoff_hz", "ratio", "resonant_hz", "gain", "gain_dB",
                  "impedance_ohms", "effective_load_pF", "estimated_vout",
                  "max_current_50mV_A", "max_current_100mV_A"):
        if extra not in fields and extra in original:
            fields = list(fields) + [extra]

    deltas = []
    for field in fields:
        bv = original.get(field)
        av = patched.get(field)
        if bv is None or av is None:
            continue
        if not isinstance(bv, (int, float)) or not isinstance(av, (int, float)):
            if bv != av:
                deltas.append({"field": field, "before": bv, "after": av})
            continue
        if bv == av:
            continue
        pct = ((av - bv) / abs(bv) * 100) if bv != 0 else None
        entry = {"field": field, "before": round(bv, 6), "after": round(av, 6)}
        if pct is not None:
            entry["delta_pct"] = round(pct, 1)
        deltas.append(entry)

    return deltas


def _get_det_label(det: dict, det_type: str) -> str:
    """Build a human-readable label for a detection."""
    refs = []
    for key in ("resistor", "r_top", "inductor", "shunt"):
        if key in det and isinstance(det[key], dict) and "ref" in det[key]:
            refs.append(det[key]["ref"])
    for key in ("capacitor", "r_bottom"):
        if key in det and isinstance(det[key], dict) and "ref" in det[key]:
            refs.append(det[key]["ref"])
    if "reference" in det:
        refs.append(det["reference"])
    for key in ("feedback_resistor", "input_resistor"):
        if key in det and isinstance(det[key], dict) and "ref" in det[key]:
            refs.append(det[key]["ref"])

    type_label = det_type.replace("_", " ").rstrip("s")
    ref_str = "/".join(refs) if refs else f"#{det_type}"
    return f"{type_label} {ref_str}"


# ---------------------------------------------------------------------------
# Optional SPICE re-simulation
# ---------------------------------------------------------------------------

def _run_spice_comparison(affected: list, patched_dets: list,
                          analysis_json: dict) -> dict:
    """Run SPICE on original and patched detections, return simulated deltas.

    Returns {(det_type, idx): {metric: {before, after, delta_pct}}}
    """
    try:
        from simulate_subcircuits import simulate_subcircuits
        from spice_simulator import detect_simulator
    except ImportError:
        print("Warning: SPICE scripts not found, skipping --spice",
              file=sys.stderr)
        return {}

    backend = detect_simulator("auto")
    if not backend:
        print("Warning: no SPICE simulator found, skipping --spice",
              file=sys.stderr)
        return {}

    results = {}

    for (det_type, idx, original_det, _matched), patched_det in zip(affected, patched_dets):
        # Build minimal analysis JSON for each detection
        def _run_one(det):
            mini_json = copy.deepcopy(analysis_json)
            mini_json["signal_analysis"] = {det_type: [det]}
            report = simulate_subcircuits(
                mini_json, timeout=5, types=[det_type],
                simulator_backend=backend)
            sim_results = report.get("simulation_results", [])
            if sim_results and sim_results[0].get("status") != "skip":
                return sim_results[0].get("simulated", {})
            return {}

        sim_before = _run_one(original_det)
        sim_after = _run_one(patched_det)

        spice_deltas = {}
        all_keys = set(list(sim_before.keys()) + list(sim_after.keys()))
        for key in all_keys:
            bv = sim_before.get(key)
            av = sim_after.get(key)
            if bv is None or av is None:
                continue
            if not isinstance(bv, (int, float)) or not isinstance(av, (int, float)):
                continue
            if bv == av:
                continue
            pct = ((av - bv) / abs(bv) * 100) if bv != 0 else None
            entry = {"before": round(bv, 6), "after": round(av, 6)}
            if pct is not None:
                entry["delta_pct"] = round(pct, 1)
            spice_deltas[key] = entry

        if spice_deltas:
            results[(det_type, idx)] = spice_deltas

    return results


# ---------------------------------------------------------------------------
# Sweep execution
# ---------------------------------------------------------------------------

def _run_sweep(analysis: dict, sweep: SweepSpec, fixed_changes: dict,
               spice: bool = False) -> dict:
    """Run the what-if pipeline for each sweep value, collect tabular results."""
    signal = analysis.get("signal_analysis", {})
    results_per_step = []

    for val, val_str in zip(sweep.values, sweep.value_strs):
        # Build changes dict for this step (legacy format for existing functions)
        step_changes = {ref: (c.value, c.value_str) for ref, c in fixed_changes.items()}
        step_changes[sweep.ref] = (val, val_str)

        affected = _find_affected(signal, step_changes)
        step_subcircuits = []
        for det_type, idx, det, matched in affected:
            patched = _apply_changes(det, step_changes, matched)
            deltas = _compare(det, patched, det_type)
            label = _get_det_label(det, det_type)
            step_subcircuits.append({
                "type": det_type, "label": label,
                "delta": deltas,
                "after": {d["field"]: d["after"] for d in deltas},
            })
        results_per_step.append({
            "value": val, "value_str": val_str,
            "affected_subcircuits": step_subcircuits,
        })

    return {
        "ref": sweep.ref,
        "values": sweep.values,
        "value_strs": sweep.value_strs,
        "results": results_per_step,
    }


# ---------------------------------------------------------------------------
# Tolerance corner-case engine
# ---------------------------------------------------------------------------

def _run_tolerance(analysis: dict, changes: dict, spice: bool = False) -> list:
    """Compute worst-case tolerance bounds for each derived field.

    Evaluates all 2^N corner combinations (each component at +tol and -tol).
    Capped at 6 components (64 corners).
    """
    signal = analysis.get("signal_analysis", {})

    # Resolve tolerances (use defaults for components without explicit tolerance)
    _DEFAULT_TOL = {"C": 0.10, "VC": 0.10, "L": 0.20}  # everything else = 0.05

    tol_info = {}  # ref -> (value, value_str, tolerance)
    for ref, c in changes.items():
        tol = c.tolerance
        if tol is None:
            prefix = ref.rstrip("0123456789")
            tol = _DEFAULT_TOL.get(prefix, 0.05)
        tol_info[ref] = (c.value, c.value_str, tol)

    changes_legacy = {ref: (c.value, c.value_str) for ref, c in changes.items()}
    affected = _find_affected(signal, changes_legacy)
    if not affected:
        return []

    results = []
    for det_type, idx, det, matched in affected:
        # Nominal
        patched_nom = _apply_changes(det, changes_legacy, matched)
        nominal_deltas = _compare(det, patched_nom, det_type)
        label = _get_det_label(det, det_type)

        # Identify toleranced refs in this detection
        tol_refs = [(ref, tol_info[ref]) for ref in matched if ref in tol_info]
        if not tol_refs:
            results.append({"type": det_type, "label": label,
                           "delta": nominal_deltas, "tolerance": []})
            continue

        # Generate 2^N corners (cap at 6 components = 64 corners)
        n = min(len(tol_refs), 6)
        corners = []
        for bits in range(1 << n):
            corner_changes = dict(changes_legacy)
            for i in range(n):
                ref, (val, vstr, tol) = tol_refs[i]
                factor = (1 + tol) if (bits >> i) & 1 else (1 - tol)
                corner_changes[ref] = (val * factor, vstr)
            corner_patched = _apply_changes(det, corner_changes, matched)
            corners.append(corner_patched)

        # For each derived field, find worst-case bounds
        tol_results = []
        fields = [d["field"] for d in nominal_deltas]
        for field in fields:
            nom_val = patched_nom.get(field)
            if not isinstance(nom_val, (int, float)):
                continue
            corner_vals = [c.get(field) for c in corners
                          if isinstance(c.get(field), (int, float))]
            if not corner_vals:
                continue
            worst_low = min(corner_vals)
            worst_high = max(corner_vals)
            spread = worst_high - worst_low
            spread_pct = (spread / abs(nom_val) * 100) if nom_val != 0 else 0
            tol_results.append({
                "field": field,
                "nominal": round(nom_val, 6),
                "worst_low": round(worst_low, 6),
                "worst_high": round(worst_high, 6),
                "spread_pct": round(spread_pct, 1),
            })

        results.append({"type": det_type, "label": label,
                       "delta": nominal_deltas, "tolerance": tol_results})

    return results


# ---------------------------------------------------------------------------
# Patch full JSON for export
# ---------------------------------------------------------------------------

def _patch_full_json(analysis_json: dict, affected: list,
                     patched_dets: list, changes: dict) -> dict:
    """Create a patched copy of the full analysis JSON."""
    patched = copy.deepcopy(analysis_json)

    # Replace affected detections
    for (det_type, idx, _orig, _matched), new_det in zip(affected, patched_dets):
        patched["signal_analysis"][det_type][idx] = new_det

    # Update components[] parsed_value
    for comp in patched.get("components", []):
        ref = comp.get("reference", "")
        if ref in changes:
            new_val, new_str = changes[ref]
            comp["value"] = new_str
            if "parsed_value" in comp and isinstance(comp["parsed_value"], dict):
                comp["parsed_value"]["value"] = new_val

    return patched


# ---------------------------------------------------------------------------
# PCB parasitic awareness
# ---------------------------------------------------------------------------

_RHO_CU = 1.72e-8  # Copper resistivity (Ω·m)
_CU_THICKNESS_1OZ = 35e-6  # 1oz copper thickness (m)

# Footprint -> typical max capacitance (ceramic MLCC)
_FOOTPRINT_MAX_CAP = {
    "0402": 100e-9, "0603": 1e-6, "0805": 10e-6,
    "1206": 22e-6, "1210": 47e-6,
}


def _find_pcb_analysis(schematic_json_path: str) -> str:
    """Try to find PCB analysis JSON in the same analysis folder."""
    sch_dir = os.path.dirname(os.path.abspath(schematic_json_path))
    parent = os.path.dirname(sch_dir)
    # Convention: analysis/schematic/foo.json -> analysis/pcb/foo.json
    if os.path.basename(sch_dir) == "schematic":
        pcb_dir = os.path.join(parent, "pcb")
    elif "schematic" in sch_dir:
        pcb_dir = sch_dir.replace("schematic", "pcb")
    else:
        return None
    if os.path.isdir(pcb_dir):
        for f in sorted(os.listdir(pcb_dir)):
            if f.endswith(".json"):
                return os.path.join(pcb_dir, f)
    return None


def _extract_parasitics(pcb_analysis: dict, det: dict, det_type: str) -> dict:
    """Extract trace parasitics for components in a detection."""
    parasitics = {}
    tracks = pcb_analysis.get("tracks", {})
    if not tracks:
        tracks = pcb_analysis.get("track_summary", {})

    refs_in_det = _find_refs_in_det(det)
    for ref in refs_in_det:
        # Find component's nets from pin_nets or detection context
        comp_nets = set()
        for path, vkey in refs_in_det.get(ref, []):
            obj = det
            for k in path:
                obj = obj[k]
            net = obj.get("net", "")
            if net:
                comp_nets.add(net)

        total_r = 0.0
        total_l = 0.0
        net_name = None
        for net in comp_nets:
            net_tracks = tracks.get(net, [])
            if isinstance(net_tracks, dict):
                net_tracks = [net_tracks]
            for t in net_tracks:
                length_m = t.get("length_mm", 0) * 1e-3
                width_m = t.get("width_mm", 0) * 1e-3
                if length_m > 0 and width_m > 0:
                    r = _RHO_CU * length_m / (width_m * _CU_THICKNESS_1OZ)
                    l = (2e-7 * length_m * math.log(2 * length_m / width_m)
                         if width_m > 0 and length_m > width_m else 0)
                    total_r += r
                    total_l += abs(l)
            if net_tracks:
                net_name = net

        if total_r > 0 or total_l > 0:
            parasitics[ref] = {
                "net": net_name,
                "R_trace_ohms": round(total_r, 6),
                "L_trace_H": round(total_l, 12),
            }

    return parasitics


def _check_footprint_fit(suggestions: list, pcb_analysis: dict) -> list:
    """Check if suggested cap values fit in current footprints."""
    warnings = []
    fp_map = {}
    for comp in pcb_analysis.get("footprints", []):
        ref = comp.get("reference", "")
        fp = comp.get("footprint", "")
        fp_map[ref] = fp

    for s in suggestions:
        ref = s.get("ref", "")
        if s.get("field") != "farads":
            continue
        fp = fp_map.get(ref, "")
        for size, max_cap in _FOOTPRINT_MAX_CAP.items():
            if size in fp:
                for series in ("E96", "E24", "E12"):
                    ev = s.get("e_series", {}).get(series, {}).get("value", 0)
                    if ev > max_cap:
                        warnings.append(
                            f"\u26a0 {ref}: suggested {_format_value(ev, 'farads')}"
                            f" may require larger package than current {size}"
                            f" (typical max {_format_value(max_cap, 'farads')})"
                        )
                break
    return warnings


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

def _format_value(val, field):
    """Format a value with appropriate units."""
    if not isinstance(val, (int, float)):
        return str(val)
    if "hz" in field.lower():
        if val >= 1e6:
            return f"{val/1e6:.2f}MHz"
        if val >= 1e3:
            return f"{val/1e3:.2f}kHz"
        return f"{val:.2f}Hz"
    if "ohms" in field.lower():
        if val >= 1e6:
            return f"{val/1e6:.2f}MΩ"
        if val >= 1e3:
            return f"{val/1e3:.2f}kΩ"
        return f"{val:.2f}Ω"
    if "farad" in field.lower():
        if val >= 1e-3:
            return f"{val*1e3:.2f}mF"
        if val >= 1e-6:
            return f"{val*1e6:.2f}µF"
        if val >= 1e-9:
            return f"{val*1e9:.2f}nF"
        return f"{val*1e12:.2f}pF"
    if field.endswith("_pF"):
        return f"{val:.1f}pF"
    if field.endswith("_A"):
        if val < 1:
            return f"{val*1000:.1f}mA"
        return f"{val:.3f}A"
    if "ratio" in field:
        return f"{val:.4f}"
    if "gain" in field.lower() and "dB" not in field:
        return f"{val:.3f}"
    if "dB" in field:
        return f"{val:.1f}dB"
    if field.startswith("estimated_vout") or field.endswith("_V") or field.endswith("_v"):
        return f"{val:.3f}V"
    return f"{val:.4g}"


def format_text(result: dict) -> str:
    """Format what-if results as human-readable text."""
    lines = []

    # Header
    changes = result.get("changes", {})
    change_strs = []
    for ref, info in changes.items():
        before = info.get("before_str", str(info.get("before", "?")))
        after = info.get("after_str", str(info.get("after", "?")))
        change_strs.append(f"{ref} {before} -> {after}")
    lines.append(f"What-If Analysis: {', '.join(change_strs)}")
    lines.append("")

    subcircuits = result.get("affected_subcircuits", [])
    lines.append(f"Affected subcircuits: {len(subcircuits)}")
    if not subcircuits:
        lines.append("  No subcircuits reference the changed component(s).")
        return "\n".join(lines)

    lines.append("")

    for sc in subcircuits:
        label = sc.get("label", sc.get("type", "?"))
        lines.append(f"  {label}:")

        for d in sc.get("delta", []):
            field = d["field"]
            before = _format_value(d["before"], field)
            after = _format_value(d["after"], field)
            pct = d.get("delta_pct")
            pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
            lines.append(f"    {field}: {before} -> {after}{pct_str}")

        for t in sc.get("tolerance", []):
            field = t["field"]
            low = _format_value(t["worst_low"], field)
            high = _format_value(t["worst_high"], field)
            spread = t["spread_pct"]
            lines.append(f"    {field} tolerance: {low} .. {high} (\u00b1{spread/2:.1f}%)")

        # SPICE results
        for key, d in sc.get("spice_delta", {}).items():
            before = _format_value(d["before"], key)
            after = _format_value(d["after"], key)
            pct = d.get("delta_pct")
            pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
            lines.append(f"    SPICE {key}: {before} -> {after}{pct_str}")

        # PCB parasitics
        for ref, p in sc.get("parasitics", {}).items():
            r = p.get("R_trace_ohms", 0)
            l = p.get("L_trace_H", 0)
            net = p.get("net", "?")
            parts = []
            if r > 0:
                parts.append(f"R_trace={_format_value(r, 'ohms')}")
            if l > 0:
                parts.append(f"L_trace={l*1e9:.1f}nH")
            if parts:
                lines.append(f"    (PCB parasitics on {net}: {', '.join(parts)})")

        lines.append("")

    emc = result.get("emc_delta")
    if emc:
        lines.append("EMC impact preview:")
        lines.append(f"  Overall risk: {emc['before_risk']} \u2192 {emc['after_risk']}")
        for r in emc.get("resolved", []):
            lines.append(f"  {r['rule']}: RESOLVED \u2014 {r['detail']}")
        for r in emc.get("improved", []):
            lines.append(f"  {r['rule']}: IMPROVED \u2014 {r['before']} \u2192 {r['after']}")
        for r in emc.get("new_findings", []):
            lines.append(f"  {r['rule']}: NEW \u2014 {r['detail']}")
        if not emc.get("resolved") and not emc.get("improved") and not emc.get("new_findings"):
            lines.append("  No EMC findings changed.")
        lines.append("")

    return "\n".join(lines)


def _format_sweep_table(sweep_result: dict) -> str:
    """Format sweep results as markdown tables."""
    lines = []
    ref = sweep_result["ref"]
    strs = sweep_result["value_strs"]
    results = sweep_result["results"]
    lines.append(f"Sweep: {ref} = {', '.join(strs)}")
    lines.append("")

    if not results or not results[0].get("affected_subcircuits"):
        lines.append("  No subcircuits affected.")
        return "\n".join(lines)

    n_subs = len(results[0]["affected_subcircuits"])
    for si in range(n_subs):
        label = results[0]["affected_subcircuits"][si]["label"]
        lines.append(f"### {label}")
        lines.append("")

        # Collect all fields across all steps
        all_fields = []
        for step in results:
            if si < len(step["affected_subcircuits"]):
                for d in step["affected_subcircuits"][si].get("delta", []):
                    if d["field"] not in all_fields:
                        all_fields.append(d["field"])

        if not all_fields:
            continue

        # Build markdown table
        col_w = max(8, max(len(s) for s in strs) + 2)
        header = f"| {'Metric':<16}|"
        sep = f"|{'-' * 17}|"
        for s in strs:
            header += f" {s:>{col_w - 1}} |"
            sep += f"{'-' * (col_w + 1)}:|"
        lines.append(header)
        lines.append(sep)

        for field in all_fields:
            row = f"| {field:<16}|"
            for step in results:
                val = None
                if si < len(step["affected_subcircuits"]):
                    val = step["affected_subcircuits"][si].get("after", {}).get(field)
                cell = _format_value(val, field) if val is not None else "-"
                row += f" {cell:>{col_w - 1}} |"
            lines.append(row)

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inverse solver for --fix mode
# ---------------------------------------------------------------------------

def _solve_fix(det: dict, det_type: str, target_field: str,
               target_value: float) -> list:
    """Compute ideal component values to achieve target.

    Returns list of suggestions, each with E-series snapped alternatives.
    """
    from kicad_utils import snap_to_e_series

    suggestions = []
    pi2 = 2.0 * math.pi

    if det_type in ("voltage_dividers", "feedback_networks") and target_field == "ratio":
        r_top = det.get("r_top", {})
        r_bot = det.get("r_bottom", {})
        rt = r_top.get("ohms", 0)
        rb = r_bot.get("ohms", 0)
        if rt > 0 and 0 < target_value < 1:
            # Fix R_top, solve R_bottom
            ideal_rb = rt * target_value / (1 - target_value)
            suggestions.append({
                "ref": r_bot.get("ref", "R_bottom"), "field": "ohms",
                "current": rb, "ideal": ideal_rb,
                "anchor_ref": r_top.get("ref", "R_top"), "anchor_value": rt,
            })
            # Fix R_bottom, solve R_top
            if rb > 0:
                ideal_rt = rb * (1 - target_value) / target_value
                suggestions.append({
                    "ref": r_top.get("ref", "R_top"), "field": "ohms",
                    "current": rt, "ideal": ideal_rt,
                    "anchor_ref": r_bot.get("ref", "R_bottom"), "anchor_value": rb,
                })

    elif det_type == "rc_filters" and target_field == "cutoff_hz":
        r = det.get("resistor", {})
        c = det.get("capacitor", {})
        rv = r.get("ohms", 0)
        cv = c.get("farads", 0)
        if rv > 0 and target_value > 0:
            ideal_c = 1.0 / (pi2 * rv * target_value)
            suggestions.append({
                "ref": c.get("ref", "C"), "field": "farads",
                "current": cv, "ideal": ideal_c,
                "anchor_ref": r.get("ref", "R"), "anchor_value": rv,
            })
        if cv > 0 and target_value > 0:
            ideal_r = 1.0 / (pi2 * cv * target_value)
            suggestions.append({
                "ref": r.get("ref", "R"), "field": "ohms",
                "current": rv, "ideal": ideal_r,
                "anchor_ref": c.get("ref", "C"), "anchor_value": cv,
            })

    elif det_type == "lc_filters" and target_field == "resonant_hz":
        l = det.get("inductor", {})
        c = det.get("capacitor", {})
        lv = l.get("henries", 0)
        cv = c.get("farads", 0)
        if lv > 0 and target_value > 0:
            ideal_c = 1.0 / ((pi2 * target_value) ** 2 * lv)
            suggestions.append({
                "ref": c.get("ref", "C"), "field": "farads",
                "current": cv, "ideal": ideal_c,
                "anchor_ref": l.get("ref", "L"), "anchor_value": lv,
            })
        if cv > 0 and target_value > 0:
            ideal_l = 1.0 / ((pi2 * target_value) ** 2 * cv)
            suggestions.append({
                "ref": l.get("ref", "L"), "field": "henries",
                "current": lv, "ideal": ideal_l,
                "anchor_ref": c.get("ref", "C"), "anchor_value": cv,
            })

    elif det_type == "opamp_circuits" and target_field in ("gain", "gain_dB"):
        target_gain = target_value
        if target_field == "gain_dB":
            target_gain = 10 ** (target_value / 20.0)
        rf = det.get("feedback_resistor", {})
        ri = det.get("input_resistor", {})
        rfv = rf.get("ohms", 0)
        riv = ri.get("ohms", 0)
        config = det.get("configuration", "")
        if riv > 0:
            if "non-inverting" in config or "non_inverting" in config:
                ideal_rf = riv * (abs(target_gain) - 1)
            else:
                ideal_rf = riv * abs(target_gain)
            if ideal_rf > 0:
                suggestions.append({
                    "ref": rf.get("ref", "Rf"), "field": "ohms",
                    "current": rfv, "ideal": ideal_rf,
                    "anchor_ref": ri.get("ref", "Ri"), "anchor_value": riv,
                })

    elif det_type == "crystal_circuits" and target_field == "effective_load_pF":
        caps = det.get("load_caps", [])
        stray = det.get("stray_capacitance_pF", 3.0)
        if len(caps) >= 2 and target_value > stray:
            ideal_pf = 2 * (target_value - stray)
            ideal_f = ideal_pf * 1e-12
            for cap in caps[:2]:
                suggestions.append({
                    "ref": cap.get("ref", "C"), "field": "farads",
                    "current": cap.get("farads", 0), "ideal": ideal_f,
                    "anchor_ref": None, "anchor_value": None,
                })

    elif det_type == "current_sense":
        shunt = det.get("shunt", {})
        rv = shunt.get("ohms", 0)
        if target_field == "max_current_100mV_A" and target_value > 0:
            ideal_r = 0.100 / target_value
            suggestions.append({
                "ref": shunt.get("ref", "R"), "field": "ohms",
                "current": rv, "ideal": ideal_r,
                "anchor_ref": None, "anchor_value": None,
            })
        elif target_field == "max_current_50mV_A" and target_value > 0:
            ideal_r = 0.050 / target_value
            suggestions.append({
                "ref": shunt.get("ref", "R"), "field": "ohms",
                "current": rv, "ideal": ideal_r,
                "anchor_ref": None, "anchor_value": None,
            })

    # Add E-series snapped values
    for s in suggestions:
        s["e_series"] = {}
        for series in ("E12", "E24", "E96"):
            snapped, err = snap_to_e_series(s["ideal"], series)
            s["e_series"][series] = {"value": snapped, "error_pct": err}

    return suggestions


def _format_fix(fix_result: dict) -> str:
    """Format fix suggestion results as text."""
    lines = []
    for fix in fix_result.get("fix_suggestions", []):
        det_type = fix["detection_type"]
        target = fix["target_value"]
        field = fix["target_field"]
        lines.append(f"Fix suggestion for {det_type}[{fix['detection_index']}]"
                     f" \u2014 target {field}={_format_value(target, field)}")
        lines.append("")
        for s in fix.get("suggestions", []):
            ref = s["ref"]
            current = s["current"]
            ideal = s["ideal"]
            vkey = s["field"]
            anchor = s.get("anchor_ref")
            anchor_note = f" (keeping {anchor})" if anchor else ""
            lines.append(f"  {ref}{anchor_note}:")
            lines.append(f"    Ideal:  {_format_value(ideal, vkey)}")
            for series in ("E96", "E24", "E12"):
                e = s["e_series"].get(series, {})
                ev = e.get("value", 0)
                err = e.get("error_pct", 0)
                lines.append(f"    {series}:    {_format_value(ev, vkey):>10}  ({err:+.1f}%)")
            lines.append("")
    for fix in fix_result.get("fix_suggestions", []):
        for w in fix.get("footprint_warnings", []):
            lines.append(f"  {w}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# EMC impact preview
# ---------------------------------------------------------------------------

def _run_emc_preview(analysis: dict, patched_json: dict,
                     pcb_path: str = None) -> dict:
    """Run EMC analysis on original and patched JSON, return delta."""
    import subprocess
    import tempfile

    emc_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "..", "emc", "scripts", "analyze_emc.py")
    if not os.path.exists(emc_script):
        print("Warning: analyze_emc.py not found, skipping EMC preview", file=sys.stderr)
        return None

    def _run_emc(schematic_json: dict) -> dict:
        fd, sch_path = tempfile.mkstemp(suffix=".json")
        out_path = sch_path + ".emc.json"
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(schematic_json, f, indent=2)
            cmd = [sys.executable, emc_script, "--schematic", sch_path, "--output", out_path]
            if pcb_path:
                cmd.extend(["--pcb", pcb_path])
            subprocess.run(cmd, capture_output=True, timeout=30, check=False)
            if os.path.exists(out_path):
                with open(out_path) as f:
                    return json.load(f)
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
            print(f"Warning: EMC analysis failed: {e}", file=sys.stderr)
        finally:
            for p in (sch_path, out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        return None

    before = _run_emc(analysis)
    after = _run_emc(patched_json)
    if not before or not after:
        return None

    before_risk = before.get("overall_risk", "UNKNOWN")
    after_risk = after.get("overall_risk", "UNKNOWN")
    before_findings = {f.get("rule_id", ""): f for f in before.get("findings", [])}
    after_findings = {f.get("rule_id", ""): f for f in after.get("findings", [])}

    resolved = []
    improved = []
    new_findings = []
    for rule_id, bf in before_findings.items():
        af = after_findings.get(rule_id)
        if af is None:
            resolved.append({"rule": rule_id, "detail": bf.get("summary", "")})
        elif af.get("risk_level") != bf.get("risk_level"):
            improved.append({"rule": rule_id,
                           "before": bf.get("risk_level"),
                           "after": af.get("risk_level")})
    for rule_id, af in after_findings.items():
        if rule_id not in before_findings:
            new_findings.append({"rule": rule_id, "detail": af.get("summary", "")})

    return {
        "before_risk": before_risk, "after_risk": after_risk,
        "resolved": resolved, "improved": improved,
        "new_findings": new_findings,
        "unchanged": len(before_findings) - len(resolved) - len(improved),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="What-if parameter sweep for KiCad designs"
    )
    parser.add_argument("input", help="Analyzer JSON (from analyze_schematic.py)")
    parser.add_argument("changes", nargs="*", default=[],
                        help="REF=VALUE pairs (e.g., R5=4.7k C3=22n)")
    parser.add_argument("--spice", action="store_true",
                        help="Re-run SPICE simulations on affected subcircuits")
    parser.add_argument("--output", "-o",
                        help="Write patched analysis JSON to file")
    parser.add_argument("--text", action="store_true",
                        help="Human-readable text output")
    parser.add_argument("--emc", action="store_true",
                        help="Show EMC impact preview (runs analyze_emc.py)")
    parser.add_argument("--pcb",
                        help="PCB analysis JSON for parasitic awareness")
    parser.add_argument("--fix",
                        help="Detection to fix (e.g., voltage_dividers[0])")
    parser.add_argument("--target", type=float,
                        help="Target value for --fix (e.g., 3.3 for Vout, 1000 for Hz)")
    args = parser.parse_args()

    if not args.changes and not args.fix:
        parser.error("at least one REF=VALUE change or --fix is required")

    # Load analysis JSON
    try:
        with open(args.input) as f:
            analysis = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {args.input}: {e}", file=sys.stderr)
        sys.exit(1)

    signal = analysis.get("signal_analysis", {})
    if not signal:
        print("Error: no signal_analysis in input JSON", file=sys.stderr)
        sys.exit(1)

    # Load PCB analysis if available
    pcb_path = args.pcb
    pcb_analysis = None
    if pcb_path:
        try:
            with open(pcb_path) as f:
                pcb_analysis = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: cannot load PCB analysis: {e}", file=sys.stderr)
    else:
        auto_pcb = _find_pcb_analysis(args.input)
        if auto_pcb:
            try:
                with open(auto_pcb) as f:
                    pcb_analysis = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    # --- Fix branch ---
    if args.fix:
        import re as _re
        m = _re.match(r'(\w+)\[(\d+)\]', args.fix)
        if not m:
            print(f"Error: invalid --fix target '{args.fix}' \u2014 use type[index] "
                  f"(e.g., voltage_dividers[0])", file=sys.stderr)
            sys.exit(1)
        fix_det_type = m.group(1)
        fix_idx = int(m.group(2))
        dets = signal.get(fix_det_type, [])
        if fix_idx >= len(dets):
            print(f"Error: {fix_det_type}[{fix_idx}] does not exist "
                  f"(have {len(dets)} detections)", file=sys.stderr)
            sys.exit(1)
        det = dets[fix_idx]

        if args.target is not None:
            fields = _DERIVED_FIELDS.get(fix_det_type, [])
            target_field = fields[0] if fields else "ratio"
            target_value = args.target
        else:
            # Try to infer target from context
            target_field, target_value = None, None
            if fix_det_type in ("voltage_dividers", "feedback_networks"):
                vref = det.get("regulator_vref")
                vout = det.get("target_vout")
                if vref and vout and vout > 0:
                    target_field, target_value = "ratio", vref / vout
            elif fix_det_type == "crystal_circuits":
                tl = det.get("target_load_pF")
                if tl:
                    target_field, target_value = "effective_load_pF", tl
            if target_field is None:
                print(f"Error: cannot infer target for {fix_det_type} \u2014 use --target",
                      file=sys.stderr)
                sys.exit(1)

        suggestions = _solve_fix(det, fix_det_type, target_field, target_value)
        result = {"fix_suggestions": [{
            "detection_type": fix_det_type,
            "detection_index": fix_idx,
            "target_field": target_field,
            "target_value": target_value,
            "suggestions": suggestions,
        }]}
        if pcb_analysis:
            fp_warnings = _check_footprint_fit(suggestions, pcb_analysis)
            if fp_warnings:
                result["fix_suggestions"][0]["footprint_warnings"] = fp_warnings
        if args.text:
            print(_format_fix(result))
        else:
            json.dump(result, sys.stdout, indent=2)
            print()
        sys.exit(0)

    # Parse changes — returns (dict of Change, optional SweepSpec)
    changes, sweep = _parse_changes(args.changes)

    # Verify refs exist in the analysis
    all_refs = set()
    for comp in analysis.get("components", []):
        if "reference" in comp:
            all_refs.add(comp["reference"])
    for ref in changes:
        if ref not in all_refs:
            print(f"Warning: {ref} not found in component list", file=sys.stderr)
    if sweep and sweep.ref not in all_refs:
        print(f"Warning: {sweep.ref} not found in component list", file=sys.stderr)

    # --- Sweep branch ---
    if sweep is not None:
        sweep_result = _run_sweep(analysis, sweep, changes, spice=args.spice)
        if args.text:
            print(_format_sweep_table(sweep_result))
        else:
            json.dump(sweep_result, sys.stdout, indent=2)
            print()
        sys.exit(0)

    # --- Single-value branch ---
    # Convert Change objects to legacy (value, str) tuples for downstream functions
    changes_legacy = {ref: (c.value, c.value_str) for ref, c in changes.items()}

    # Find affected detections
    affected = _find_affected(signal, changes_legacy)
    if not affected:
        print(f"No subcircuits reference {', '.join(changes_legacy.keys())}",
              file=sys.stderr)
        result = {
            "changes": {ref: {"before": None, "after": val, "after_str": vstr}
                        for ref, (val, vstr) in changes_legacy.items()},
            "affected_subcircuits": [],
            "summary": {"components_changed": len(changes_legacy),
                        "subcircuits_affected": 0, "spice_verified": False},
        }
        if args.text:
            print(format_text(result))
        else:
            json.dump(result, sys.stdout, indent=2)
            print()
        sys.exit(0)

    # Apply changes to each affected detection
    patched_dets = []
    for det_type, idx, det, matched in affected:
        patched = _apply_changes(det, changes_legacy, matched)
        patched_dets.append(patched)

    # Build before/after comparisons
    subcircuit_results = []
    for (det_type, idx, det, matched), patched in zip(affected, patched_dets):
        deltas = _compare(det, patched, det_type)
        label = _get_det_label(det, det_type)
        comps = []
        refs_in_det = _find_refs_in_det(det)
        for r in refs_in_det:
            comps.append(r)

        entry = {
            "type": det_type,
            "label": label,
            "components": comps,
            "delta": deltas,
            "before": {d["field"]: d["before"] for d in deltas},
            "after": {d["field"]: d["after"] for d in deltas},
        }
        subcircuit_results.append(entry)

    # PCB parasitics
    if pcb_analysis:
        for (det_type, idx, det, matched), sc in zip(affected, subcircuit_results):
            paras = _extract_parasitics(pcb_analysis, det, det_type)
            if paras:
                sc["parasitics"] = paras

    # Tolerance analysis
    has_tolerance = any(c.tolerance is not None for c in changes.values())
    if has_tolerance:
        tol_results = _run_tolerance(analysis, changes, spice=args.spice)
        for tr in tol_results:
            for sc in subcircuit_results:
                if sc["type"] == tr["type"] and sc["label"] == tr["label"]:
                    sc["tolerance"] = tr.get("tolerance", [])

    # Optional SPICE
    spice_results = {}
    if args.spice:
        spice_results = _run_spice_comparison(affected, patched_dets, analysis)
        for i, (det_type, idx, _det, _matched) in enumerate(affected):
            key = (det_type, idx)
            if key in spice_results:
                subcircuit_results[i]["spice_delta"] = spice_results[key]

    # Build change info with before values
    change_info = {}
    for ref, (new_val, new_str) in changes_legacy.items():
        # Find the original value
        old_val = None
        old_str = ""
        for comp in analysis.get("components", []):
            if comp.get("reference") == ref:
                old_str = comp.get("value", "")
                pv = comp.get("parsed_value", {})
                if isinstance(pv, dict):
                    old_val = pv.get("value")
                break
        change_info[ref] = {
            "before": old_val,
            "after": new_val,
            "before_str": old_str,
            "after_str": new_str,
            "unit": "ohms" if ref.startswith("R") else
                    "farads" if ref.startswith("C") else
                    "henries" if ref.startswith("L") else "unknown",
        }

    result = {
        "changes": change_info,
        "affected_subcircuits": subcircuit_results,
        "summary": {
            "components_changed": len(changes_legacy),
            "subcircuits_affected": len(affected),
            "spice_verified": bool(spice_results),
        },
    }

    # EMC impact preview
    if args.emc:
        patched_json = _patch_full_json(analysis, affected, patched_dets, changes_legacy)
        pcb = getattr(args, "pcb", None)
        emc_delta = _run_emc_preview(analysis, patched_json, pcb_path=pcb)
        if emc_delta:
            result["emc_delta"] = emc_delta

    # Export patched JSON if requested
    if args.output:
        patched_json = _patch_full_json(analysis, affected, patched_dets, changes_legacy)
        with open(args.output, "w") as f:
            json.dump(patched_json, f, indent=2)
        print(f"Patched JSON written to {args.output}", file=sys.stderr)

    # Output results
    if args.text:
        print(format_text(result))
    elif not args.output:
        json.dump(result, sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
