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
import os
import sys

# Allow imports from same directory and spice scripts
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "spice", "scripts"))

from kicad_utils import parse_value


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

def _parse_changes(change_args: list) -> dict:
    """Parse REF=VALUE pairs into {ref: (new_si_value, value_string)}.

    Examples: R5=4.7k -> {"R5": (4700.0, "4.7k")}
              C3=22n  -> {"C3": (2.2e-8, "22n")}
    """
    changes = {}
    for arg in change_args:
        if "=" not in arg:
            print(f"Error: invalid change '{arg}' — expected REF=VALUE (e.g., R5=4.7k)",
                  file=sys.stderr)
            sys.exit(1)
        ref, val_str = arg.split("=", 1)
        ref = ref.strip()
        val_str = val_str.strip()

        # Determine component type hint from ref prefix
        prefix = ref.rstrip("0123456789")
        ctype = None
        if prefix in ("C", "VC"):
            ctype = "capacitor"
        elif prefix in ("L",):
            ctype = "inductor"

        parsed = parse_value(val_str, component_type=ctype)
        if parsed is None:
            print(f"Error: cannot parse value '{val_str}' for {ref}",
                  file=sys.stderr)
            sys.exit(1)

        changes[ref] = (parsed, val_str)
    return changes


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

        # SPICE results
        for key, d in sc.get("spice_delta", {}).items():
            before = _format_value(d["before"], key)
            after = _format_value(d["after"], key)
            pct = d.get("delta_pct")
            pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
            lines.append(f"    SPICE {key}: {before} -> {after}{pct_str}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="What-if parameter sweep for KiCad designs"
    )
    parser.add_argument("input", help="Analyzer JSON (from analyze_schematic.py)")
    parser.add_argument("changes", nargs="+",
                        help="REF=VALUE pairs (e.g., R5=4.7k C3=22n)")
    parser.add_argument("--spice", action="store_true",
                        help="Re-run SPICE simulations on affected subcircuits")
    parser.add_argument("--output", "-o",
                        help="Write patched analysis JSON to file")
    parser.add_argument("--text", action="store_true",
                        help="Human-readable text output")
    args = parser.parse_args()

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

    # Parse changes
    changes = _parse_changes(args.changes)

    # Verify refs exist in the analysis
    all_refs = set()
    for comp in analysis.get("components", []):
        if "reference" in comp:
            all_refs.add(comp["reference"])
    for ref in changes:
        if ref not in all_refs:
            print(f"Warning: {ref} not found in component list", file=sys.stderr)

    # Find affected detections
    affected = _find_affected(signal, changes)
    if not affected:
        print(f"No subcircuits reference {', '.join(changes.keys())}",
              file=sys.stderr)
        result = {
            "changes": {ref: {"before": None, "after": val, "after_str": vstr}
                        for ref, (val, vstr) in changes.items()},
            "affected_subcircuits": [],
            "summary": {"components_changed": len(changes),
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
        patched = _apply_changes(det, changes, matched)
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
    for ref, (new_val, new_str) in changes.items():
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
            "components_changed": len(changes),
            "subcircuits_affected": len(affected),
            "spice_verified": bool(spice_results),
        },
    }

    # Export patched JSON if requested
    if args.output:
        patched_json = _patch_full_json(analysis, affected, patched_dets, changes)
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
