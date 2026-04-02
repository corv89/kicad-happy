#!/usr/bin/env python3
"""
Diff-aware design review for KiCad analysis outputs.

Compares two analysis JSON files (base vs head) and reports changes:
new/removed/modified components, signal parameter shifts, EMC finding
deltas, and SPICE status transitions.

Usage:
    python3 diff_analysis.py base.json head.json
    python3 diff_analysis.py base.json head.json --text
    python3 diff_analysis.py base.json head.json --output diff.json
    python3 diff_analysis.py base.json head.json --threshold 2.0

Supports: schematic, PCB, EMC, and SPICE analyzer outputs (auto-detected).
Zero dependencies — Python 3.8+ stdlib only.
"""

import argparse
import json
import sys


# ---------------------------------------------------------------------------
# Signal analysis identity & value registry
# ---------------------------------------------------------------------------
# Maps detection type -> (identity_fields, value_fields)
# identity_fields: dotpath fields that uniquely identify a detection
# value_fields: numeric/string fields to compare for changes

SIGNAL_REGISTRY = {
    "voltage_dividers": (["r_top.ref", "r_bottom.ref"], ["ratio", "vout_estimated"]),
    "rc_filters": (["resistor.ref", "capacitor.ref"], ["cutoff_hz"]),
    "lc_filters": (["inductor.ref", "capacitor.ref"], ["resonant_hz"]),
    "power_regulators": (["ref"], ["vout_estimated", "topology"]),
    "opamp_circuits": (["reference"], ["gain", "gain_dB", "configuration"]),
    "crystal_circuits": (["reference"], ["frequency", "effective_load_pF"]),
    "transistor_circuits": (["reference"], ["type"]),
    "protection_devices": (["reference", "type"], ["protected_net"]),
    "current_sense": (["shunt.ref"], ["max_current_50mV_A", "max_current_100mV_A"]),
    "feedback_networks": (["r_top.ref", "r_bottom.ref"], ["ratio"]),
    "bridge_circuits": (["topology"], []),
    "rf_matching": (["antenna_ref"], []),
    "bms_systems": (["bms_reference"], ["cell_count"]),
    "decoupling_analysis": (["rail_net"], []),
    "rf_chains": ([], []),
    "ethernet_interfaces": (["phy_ref"], []),
    "memory_interfaces": (["type"], []),
    "isolation_barriers": (["isolator_ref"], []),
}


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

def _resolve(data, dotpath):
    """Navigate a dotted path like 'statistics.total_components' to a value."""
    obj = data
    for key in dotpath.split("."):
        if isinstance(obj, dict) and key in obj:
            obj = obj[key]
        else:
            return None
    return obj


def _diff_counts(base, head, paths):
    """Compare numeric values at dotted paths. Returns only changed paths."""
    deltas = {}
    for path in paths:
        bv = _resolve(base, path)
        hv = _resolve(head, path)
        if bv is None and hv is None:
            continue
        bv = bv if bv is not None else 0
        hv = hv if hv is not None else 0
        if bv != hv:
            deltas[path] = {"base": bv, "head": hv, "delta": hv - bv}
    return deltas


def _identity_key(item, fields):
    """Build a stable identity string from dotpath fields on a dict item."""
    parts = []
    for field in fields:
        val = item
        for key in field.split("."):
            if isinstance(val, dict) and key in val:
                val = val[key]
            else:
                val = None
                break
        if val is None:
            return None
        if isinstance(val, list):
            parts.append("|".join(str(v) for v in sorted(val)))
        else:
            parts.append(str(val))
    return "::".join(parts) if parts else None


def _generic_identity(item):
    """Fallback identity extraction for unknown detection types."""
    for field in ("reference", "ref"):
        if field in item and isinstance(item[field], str):
            return item[field]
    # Try nested ref fields
    for key, val in item.items():
        if isinstance(val, dict) and "ref" in val:
            return val["ref"]
    return None


def _diff_lists(base_items, head_items, id_fields, value_fields, threshold):
    """Match items by identity, return added/removed/modified/unchanged.

    Returns:
        {added: [...], removed: [...], modified: [...], unchanged_count: int}
    """
    result = {"added": [], "removed": [], "modified": [], "unchanged_count": 0}

    if not isinstance(base_items, list):
        base_items = []
    if not isinstance(head_items, list):
        head_items = []

    # Build identity maps
    base_map = {}
    for item in base_items:
        if id_fields:
            key = _identity_key(item, id_fields)
        else:
            key = _generic_identity(item)
        if key:
            base_map[key] = item

    head_map = {}
    for item in head_items:
        if id_fields:
            key = _identity_key(item, id_fields)
        else:
            key = _generic_identity(item)
        if key:
            head_map[key] = item

    # Find added, removed, modified
    for key, item in head_map.items():
        if key not in base_map:
            result["added"].append(_summarize_detection(item, id_fields))
        else:
            changes = _compare_fields(base_map[key], item, value_fields, threshold)
            if changes:
                result["modified"].append({
                    "identity": key.replace("::", "/"),
                    "changes": changes,
                })
            else:
                result["unchanged_count"] += 1

    for key in base_map:
        if key not in head_map:
            result["removed"].append(_summarize_detection(base_map[key], id_fields))

    return result


def _compare_fields(base_item, head_item, fields, threshold):
    """Compare specific fields between two matched items. Returns list of changes."""
    changes = []
    for field in fields:
        bv = _resolve(base_item, field)
        hv = _resolve(head_item, field)
        if bv == hv:
            continue
        if bv is None or hv is None:
            changes.append({"field": field, "base": bv, "head": hv})
            continue
        if isinstance(bv, (int, float)) and isinstance(hv, (int, float)):
            if bv != 0:
                pct = (hv - bv) / abs(bv) * 100
                if abs(pct) < threshold:
                    continue
                changes.append({
                    "field": field, "base": bv, "head": hv,
                    "delta_pct": round(pct, 1),
                })
            elif hv != 0:
                changes.append({"field": field, "base": bv, "head": hv})
        else:
            changes.append({"field": field, "base": bv, "head": hv})
    return changes


def _summarize_detection(item, id_fields):
    """Create a concise summary of a detection for added/removed lists."""
    summary = {}
    # Include identity fields
    for field in (id_fields or []):
        val = _resolve(item, field)
        if val is not None:
            summary[field.split(".")[-1]] = val
    # Include common fields
    for key in ("reference", "ref", "value", "type", "topology"):
        if key in item and isinstance(item[key], str):
            summary[key] = item[key]
    # Include sub-dict refs
    for key, val in item.items():
        if isinstance(val, dict) and "ref" in val and key not in summary:
            summary[key + "_ref"] = val["ref"]
    return summary


def _pct_delta(old, new):
    """Calculate percentage change. Returns None if old is zero."""
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100, 1)


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

def detect_type(data):
    """Infer analyzer type from top-level JSON keys."""
    if "signal_analysis" in data:
        return "schematic"
    if "footprints" in data and "tracks" in data:
        return "pcb"
    summary = data.get("summary", {})
    if "findings" in data and "emc_risk_score" in summary:
        return "emc"
    if "simulation_results" in data:
        return "spice"
    return None


# ---------------------------------------------------------------------------
# Schematic diff
# ---------------------------------------------------------------------------

def diff_schematic(base, head, threshold):
    """Diff two schematic analysis JSONs."""
    result = {}

    # Statistics
    stat_paths = [
        "statistics.total_components", "statistics.total_nets",
        "statistics.unique_parts", "statistics.total_wires",
        "statistics.total_no_connects",
    ]
    stats = _diff_counts(base, head, stat_paths)
    if stats:
        result["statistics"] = stats

    # Components: match by reference
    base_comps = {c["reference"]: c for c in base.get("components", [])
                  if isinstance(c, dict) and "reference" in c}
    head_comps = {c["reference"]: c for c in head.get("components", [])
                  if isinstance(c, dict) and "reference" in c}

    comp_diff = {"added": [], "removed": [], "modified": []}
    for ref, comp in head_comps.items():
        if ref not in base_comps:
            comp_diff["added"].append({
                "reference": ref, "value": comp.get("value", ""),
                "footprint": comp.get("footprint", ""),
            })
        else:
            bc = base_comps[ref]
            changes = []
            for field in ("value", "footprint", "mpn"):
                bv = bc.get(field, "")
                hv = comp.get(field, "")
                if bv != hv:
                    changes.append({"field": field, "base": bv, "head": hv})
            if changes:
                comp_diff["modified"].append({"reference": ref, "changes": changes})

    for ref in base_comps:
        if ref not in head_comps:
            bc = base_comps[ref]
            comp_diff["removed"].append({
                "reference": ref, "value": bc.get("value", ""),
                "footprint": bc.get("footprint", ""),
            })

    if comp_diff["added"] or comp_diff["removed"] or comp_diff["modified"]:
        result["components"] = comp_diff

    # Signal analysis
    base_sa = base.get("signal_analysis", {})
    head_sa = head.get("signal_analysis", {})
    all_keys = set(list(base_sa.keys()) + list(head_sa.keys()))
    sa_diff = {}

    for det_type in sorted(all_keys):
        base_items = base_sa.get(det_type, [])
        head_items = head_sa.get(det_type, [])

        if det_type in SIGNAL_REGISTRY:
            id_fields, val_fields = SIGNAL_REGISTRY[det_type]
        else:
            id_fields, val_fields = ["reference"], []

        diff = _diff_lists(base_items, head_items, id_fields, val_fields, threshold)
        if diff["added"] or diff["removed"] or diff["modified"]:
            sa_diff[det_type] = diff

    if sa_diff:
        result["signal_analysis"] = sa_diff

    # BOM changes
    base_bom = {(b.get("value", ""), b.get("footprint", "")): b
                for b in base.get("bom", []) if isinstance(b, dict)}
    head_bom = {(b.get("value", ""), b.get("footprint", "")): b
                for b in head.get("bom", []) if isinstance(b, dict)}

    bom_diff = {"added": [], "removed": [], "quantity_changes": []}
    for key, entry in head_bom.items():
        if key not in base_bom:
            bom_diff["added"].append({
                "value": entry.get("value", ""),
                "footprint": entry.get("footprint", ""),
                "quantity": entry.get("quantity", 0),
            })
        else:
            bq = base_bom[key].get("quantity", 0)
            hq = entry.get("quantity", 0)
            if bq != hq:
                bom_diff["quantity_changes"].append({
                    "value": entry.get("value", ""),
                    "footprint": entry.get("footprint", ""),
                    "base_qty": bq, "head_qty": hq, "delta": hq - bq,
                })
    for key in base_bom:
        if key not in head_bom:
            entry = base_bom[key]
            bom_diff["removed"].append({
                "value": entry.get("value", ""),
                "footprint": entry.get("footprint", ""),
                "quantity": entry.get("quantity", 0),
            })

    if bom_diff["added"] or bom_diff["removed"] or bom_diff["quantity_changes"]:
        result["bom"] = bom_diff

    # Connectivity issues
    conn_diff = {}
    for section in ("single_pin_nets", "floating_nets", "multi_driver_nets"):
        base_set = set(base.get("connectivity_issues", {}).get(section, []))
        head_set = set(head.get("connectivity_issues", {}).get(section, []))
        new = sorted(head_set - base_set)
        resolved = sorted(base_set - head_set)
        if new or resolved:
            conn_diff[section] = {}
            if new:
                conn_diff[section]["new"] = new
            if resolved:
                conn_diff[section]["resolved"] = resolved
    if conn_diff:
        result["connectivity"] = conn_diff

    # ERC warnings (list of dicts — key on type/net/message for stable identity)
    def _erc_key(w):
        if isinstance(w, dict):
            return (w.get("type", ""), w.get("net", ""), w.get("message", ""))
        return (str(w),)

    base_erc_list = base.get("design_analysis", {}).get("erc_warnings", [])
    head_erc_list = head.get("design_analysis", {}).get("erc_warnings", [])
    base_erc_map = {_erc_key(w): w for w in base_erc_list if isinstance(w, (dict, str))}
    head_erc_map = {_erc_key(w): w for w in head_erc_list if isinstance(w, (dict, str))}
    new_keys = set(head_erc_map) - set(base_erc_map)
    resolved_keys = set(base_erc_map) - set(head_erc_map)
    if new_keys or resolved_keys:
        erc = {}
        if new_keys:
            erc["new_warnings"] = [head_erc_map[k] for k in sorted(new_keys)]
        if resolved_keys:
            erc["resolved_warnings"] = [base_erc_map[k] for k in sorted(resolved_keys)]
        result["erc"] = erc

    return result


# ---------------------------------------------------------------------------
# PCB diff
# ---------------------------------------------------------------------------

def diff_pcb(base, head, threshold):
    """Diff two PCB analysis JSONs."""
    result = {}

    # Statistics
    stat_paths = [
        "statistics.footprint_count", "statistics.track_segments",
        "statistics.via_count", "statistics.zone_count",
        "statistics.net_count", "statistics.copper_layers_used",
        "statistics.board_width_mm", "statistics.board_height_mm",
        "statistics.total_track_length_mm",
    ]
    stats = _diff_counts(base, head, stat_paths)
    if stats:
        result["statistics"] = stats

    # Routing completeness
    base_rc = _resolve(base, "connectivity.routing_complete")
    head_rc = _resolve(head, "connectivity.routing_complete")
    if base_rc != head_rc:
        result["routing_complete"] = {"base": base_rc, "head": head_rc}

    unrouted = _diff_counts(base, head, ["connectivity.unrouted_count"])
    if unrouted:
        result["unrouted"] = unrouted

    # Footprints: match by reference
    base_fps = {f["reference"]: f for f in base.get("footprints", [])
                if isinstance(f, dict) and "reference" in f}
    head_fps = {f["reference"]: f for f in head.get("footprints", [])
                if isinstance(f, dict) and "reference" in f}

    fp_diff = {"added": [], "removed": [], "modified": []}
    for ref, fp in head_fps.items():
        if ref not in base_fps:
            fp_diff["added"].append({
                "reference": ref, "value": fp.get("value", ""),
                "lib_id": fp.get("lib_id", ""), "layer": fp.get("layer", ""),
            })
        else:
            bfp = base_fps[ref]
            changes = []
            for field in ("value", "lib_id", "layer"):
                bv = bfp.get(field, "")
                hv = fp.get(field, "")
                if bv != hv:
                    changes.append({"field": field, "base": bv, "head": hv})
            if changes:
                fp_diff["modified"].append({"reference": ref, "changes": changes})

    for ref in base_fps:
        if ref not in head_fps:
            bfp = base_fps[ref]
            fp_diff["removed"].append({
                "reference": ref, "value": bfp.get("value", ""),
                "layer": bfp.get("layer", ""),
            })

    if fp_diff["added"] or fp_diff["removed"] or fp_diff["modified"]:
        result["footprints"] = fp_diff

    return result


# ---------------------------------------------------------------------------
# EMC diff
# ---------------------------------------------------------------------------

def diff_emc(base, head, threshold):
    """Diff two EMC analysis JSONs."""
    result = {}

    # Risk score
    base_score = _resolve(base, "summary.emc_risk_score")
    head_score = _resolve(head, "summary.emc_risk_score")
    if base_score is not None and head_score is not None and base_score != head_score:
        result["risk_score"] = {
            "base": base_score, "head": head_score,
            "delta": head_score - base_score,
        }

    # Severity distribution
    sev_paths = ["summary.critical", "summary.high", "summary.medium",
                 "summary.low", "summary.info"]
    sev_delta = _diff_counts(base, head, sev_paths)
    if sev_delta:
        result["by_severity"] = sev_delta

    # Findings: match by (rule_id, sorted nets, sorted components)
    def _finding_key(f):
        rule = f.get("rule_id", "")
        nets = "|".join(sorted(f.get("nets", [])))
        comps = "|".join(sorted(f.get("components", [])))
        return f"{rule}::{nets}::{comps}"

    base_findings = {_finding_key(f): f for f in base.get("findings", [])
                     if isinstance(f, dict)}
    head_findings = {_finding_key(f): f for f in head.get("findings", [])
                     if isinstance(f, dict)}

    findings_diff = {"new": [], "resolved": [], "changed_severity": []}

    for key, f in head_findings.items():
        if key not in base_findings:
            findings_diff["new"].append({
                "rule_id": f.get("rule_id", ""),
                "severity": f.get("severity", ""),
                "title": f.get("title", ""),
                "category": f.get("category", ""),
                "nets": f.get("nets", []),
                "components": f.get("components", []),
            })
        else:
            bf = base_findings[key]
            if bf.get("severity") != f.get("severity"):
                findings_diff["changed_severity"].append({
                    "rule_id": f.get("rule_id", ""),
                    "base_severity": bf.get("severity", ""),
                    "head_severity": f.get("severity", ""),
                    "title": f.get("title", ""),
                })

    for key, f in base_findings.items():
        if key not in head_findings:
            findings_diff["resolved"].append({
                "rule_id": f.get("rule_id", ""),
                "severity": f.get("severity", ""),
                "title": f.get("title", ""),
                "category": f.get("category", ""),
            })

    if findings_diff["new"] or findings_diff["resolved"] or findings_diff["changed_severity"]:
        result["findings"] = findings_diff

    # Per-net score changes
    base_nets = {n["net"]: n for n in base.get("per_net_scores", [])
                 if isinstance(n, dict) and "net" in n}
    head_nets = {n["net"]: n for n in head.get("per_net_scores", [])
                 if isinstance(n, dict) and "net" in n}

    net_changes = []
    for net, entry in head_nets.items():
        if net in base_nets:
            bs = base_nets[net].get("score", 0)
            hs = entry.get("score", 0)
            if abs(hs - bs) >= threshold:
                net_changes.append({
                    "net": net, "base_score": bs, "head_score": hs,
                    "delta": hs - bs,
                })
    if net_changes:
        net_changes.sort(key=lambda n: -abs(n["delta"]))
        result["per_net_scores"] = net_changes

    return result


# ---------------------------------------------------------------------------
# SPICE diff
# ---------------------------------------------------------------------------

def diff_spice(base, head, threshold):
    """Diff two SPICE simulation JSONs."""
    result = {}

    # Summary deltas
    sum_paths = ["summary.pass", "summary.warn", "summary.fail", "summary.skip",
                 "summary.total"]
    sum_delta = _diff_counts(base, head, sum_paths)
    if sum_delta:
        result["summary"] = sum_delta

    # Results: match by (subcircuit_type, sorted components)
    def _sim_key(r):
        stype = r.get("subcircuit_type", "")
        comps = "|".join(sorted(r.get("components", [])))
        return f"{stype}::{comps}"

    base_results = {_sim_key(r): r for r in base.get("simulation_results", [])
                    if isinstance(r, dict)}
    head_results = {_sim_key(r): r for r in head.get("simulation_results", [])
                    if isinstance(r, dict)}

    status_changes = []
    new_results = []
    removed_results = []

    for key, r in head_results.items():
        if key not in base_results:
            new_results.append({
                "subcircuit_type": r.get("subcircuit_type", ""),
                "components": r.get("components", []),
                "status": r.get("status", ""),
            })
        else:
            br = base_results[key]
            bs = br.get("status", "")
            hs = r.get("status", "")
            if bs != hs:
                entry = {
                    "subcircuit_type": r.get("subcircuit_type", ""),
                    "components": r.get("components", []),
                    "base_status": bs,
                    "head_status": hs,
                }
                # Add note for regressions
                if bs == "pass" and hs in ("fail", "warn"):
                    delta = r.get("delta", {})
                    notes = [f"{k}={v}" for k, v in delta.items()
                             if isinstance(v, (int, float))]
                    if notes:
                        entry["note"] = ", ".join(notes[:3])
                status_changes.append(entry)

    for key, r in base_results.items():
        if key not in head_results:
            removed_results.append({
                "subcircuit_type": r.get("subcircuit_type", ""),
                "components": r.get("components", []),
                "status": r.get("status", ""),
            })

    if status_changes:
        result["status_changes"] = status_changes
    if new_results:
        result["new_results"] = new_results
    if removed_results:
        result["removed_results"] = removed_results

    # Monte Carlo concerns diff
    base_mc = base.get("monte_carlo_summary", {}).get("concerns", [])
    head_mc = head.get("monte_carlo_summary", {}).get("concerns", [])
    if base_mc or head_mc:
        def _mc_key(c):
            return f"{c.get('subcircuit_type', '')}::{c.get('metric', '')}"
        base_mc_map = {_mc_key(c): c for c in base_mc}
        head_mc_map = {_mc_key(c): c for c in head_mc}
        new_concerns = [c for k, c in head_mc_map.items() if k not in base_mc_map]
        resolved_concerns = [c for k, c in base_mc_map.items() if k not in head_mc_map]
        if new_concerns or resolved_concerns:
            mc = {}
            if new_concerns:
                mc["new"] = new_concerns
            if resolved_concerns:
                mc["resolved"] = resolved_concerns
            result["monte_carlo"] = mc

    return result


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

def classify_severity(analyzer_type, diff_result):
    """Classify overall change severity."""
    if not diff_result:
        return "none"

    # Breaking: SPICE pass->fail, new EMC CRITICAL, new ERC warnings
    if analyzer_type == "spice":
        for sc in diff_result.get("status_changes", []):
            if sc.get("base_status") == "pass" and sc.get("head_status") == "fail":
                return "breaking"

    if analyzer_type == "emc":
        for f in diff_result.get("findings", {}).get("new", []):
            if f.get("severity") == "CRITICAL":
                return "breaking"

    if analyzer_type == "schematic":
        if diff_result.get("erc", {}).get("new_warnings"):
            return "breaking"

    # Major: component changes, signal parameter shifts, new/removed detections
    if "signal_analysis" in diff_result or "components" in diff_result:
        return "major"
    if "findings" in diff_result:
        return "major"
    if "status_changes" in diff_result:
        return "major"
    if "footprints" in diff_result:
        fp = diff_result["footprints"]
        if fp.get("added") or fp.get("removed") or fp.get("modified"):
            return "major"

    # Minor: only statistics changes
    if "statistics" in diff_result:
        return "minor"

    return "none"


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def build_summary(analyzer_type, diff_result):
    """Build a top-level summary of all changes."""
    added = 0
    removed = 0
    modified = 0

    if analyzer_type == "schematic":
        comp = diff_result.get("components", {})
        added += len(comp.get("added", []))
        removed += len(comp.get("removed", []))
        modified += len(comp.get("modified", []))
        for det_type, det_diff in diff_result.get("signal_analysis", {}).items():
            added += len(det_diff.get("added", []))
            removed += len(det_diff.get("removed", []))
            modified += len(det_diff.get("modified", []))

    elif analyzer_type == "pcb":
        fp = diff_result.get("footprints", {})
        added += len(fp.get("added", []))
        removed += len(fp.get("removed", []))
        modified += len(fp.get("modified", []))

    elif analyzer_type == "emc":
        findings = diff_result.get("findings", {})
        added += len(findings.get("new", []))
        removed += len(findings.get("resolved", []))
        modified += len(findings.get("changed_severity", []))

    elif analyzer_type == "spice":
        added += len(diff_result.get("new_results", []))
        removed += len(diff_result.get("removed_results", []))
        modified += len(diff_result.get("status_changes", []))

    total = added + removed + modified
    severity = classify_severity(analyzer_type, diff_result)

    return {
        "total_changes": total,
        "added": added,
        "removed": removed,
        "modified": modified,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

MAX_TEXT_ITEMS = 20


def format_text(output):
    """Format diff output as human-readable text."""
    lines = []
    atype = output.get("analyzer_type", "?")
    summary = output.get("summary", {})
    severity = summary.get("severity", "none")
    total = summary.get("total_changes", 0)

    lines.append(f"Design Changes: {atype} ({severity}) — {total} changes")
    if total == 0:
        lines.append("  No changes detected.")
        return "\n".join(lines)

    s = summary
    lines.append(f"  +{s['added']} added, -{s['removed']} removed, ~{s['modified']} modified")
    lines.append("")

    diff = output.get("diff", {})
    shown = 0

    # Components (schematic)
    comp = diff.get("components", {})
    if comp:
        lines.append("Components:")
        for c in comp.get("added", [])[:5]:
            lines.append(f"  + {c.get('reference', '?')} {c.get('value', '')} {c.get('footprint', '')}")
            shown += 1
        for c in comp.get("removed", [])[:5]:
            lines.append(f"  - {c.get('reference', '?')} {c.get('value', '')} {c.get('footprint', '')}")
            shown += 1
        for c in comp.get("modified", [])[:5]:
            ref = c.get("reference", "?")
            for ch in c.get("changes", []):
                lines.append(f"  ~ {ref}: {ch['field']} {ch.get('base', '?')} → {ch.get('head', '?')}")
            shown += 1
        lines.append("")

    # Signal analysis (schematic)
    sa = diff.get("signal_analysis", {})
    if sa:
        lines.append("Signal Analysis:")
        for det_type, det_diff in sa.items():
            label = det_type.replace("_", " ").title()
            for item in det_diff.get("added", [])[:3]:
                desc = " ".join(f"{k}={v}" for k, v in item.items())
                lines.append(f"  + New {label}: {desc}")
                shown += 1
            for item in det_diff.get("removed", [])[:3]:
                desc = " ".join(f"{k}={v}" for k, v in item.items())
                lines.append(f"  - Removed {label}: {desc}")
                shown += 1
            for item in det_diff.get("modified", [])[:3]:
                identity = item.get("identity", "?")
                changes = ", ".join(
                    f"{ch['field']} {ch.get('base', '?')} → {ch.get('head', '?')}"
                    for ch in item.get("changes", [])
                )
                lines.append(f"  ~ {label} {identity}: {changes}")
                shown += 1
            if shown >= MAX_TEXT_ITEMS:
                break
        lines.append("")

    # Footprints (PCB)
    fp = diff.get("footprints", {})
    if fp:
        lines.append("Footprints:")
        for f in fp.get("added", [])[:5]:
            lines.append(f"  + {f.get('reference', '?')} {f.get('value', '')} ({f.get('layer', '')})")
        for f in fp.get("removed", [])[:5]:
            lines.append(f"  - {f.get('reference', '?')} {f.get('value', '')} ({f.get('layer', '')})")
        for f in fp.get("modified", [])[:5]:
            ref = f.get("reference", "?")
            for ch in f.get("changes", []):
                lines.append(f"  ~ {ref}: {ch['field']} {ch.get('base', '?')} → {ch.get('head', '?')}")
        lines.append("")

    # EMC findings
    findings = diff.get("findings", {})
    if findings:
        lines.append("EMC Findings:")
        for f in findings.get("new", [])[:5]:
            lines.append(f"  NEW: {f.get('rule_id', '?')} ({f.get('severity', '?')}) {f.get('title', '')}")
        for f in findings.get("resolved", [])[:5]:
            lines.append(f"  RESOLVED: {f.get('rule_id', '?')} ({f.get('severity', '?')}) {f.get('title', '')}")
        for f in findings.get("changed_severity", [])[:3]:
            lines.append(f"  CHANGED: {f.get('rule_id', '?')} {f.get('base_severity', '?')} → {f.get('head_severity', '?')}")
        lines.append("")

    # SPICE status changes
    status_changes = diff.get("status_changes", [])
    if status_changes:
        lines.append("SPICE:")
        for sc in status_changes[:5]:
            direction = "REGRESSION" if sc.get("head_status") == "fail" else "FIXED" if sc.get("head_status") == "pass" else "CHANGED"
            comps = ", ".join(sc.get("components", []))
            lines.append(f"  {direction}: {sc.get('subcircuit_type', '?')} {comps}: "
                         f"{sc.get('base_status', '?')} → {sc.get('head_status', '?')}")
        lines.append("")

    # Risk score
    risk = diff.get("risk_score", {})
    if risk:
        lines.append(f"EMC Risk Score: {risk.get('base', '?')} → {risk.get('head', '?')} "
                      f"(delta {risk.get('delta', 0):+d})")
        lines.append("")

    remaining = total - shown
    if remaining > 0:
        lines.append(f"  ... and {remaining} more changes")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compare two KiCad analysis JSON files and report changes"
    )
    parser.add_argument("base", help="Path to base (old) analysis JSON")
    parser.add_argument("head", help="Path to head (new) analysis JSON")
    parser.add_argument("--output", "-o", help="Write output JSON to file (default: stdout)")
    parser.add_argument("--text", action="store_true", help="Output human-readable text instead of JSON")
    parser.add_argument("--threshold", type=float, default=1.0,
                        help="Ignore numeric deltas below this percentage (default: 1.0%%)")
    args = parser.parse_args()

    # Load inputs
    try:
        with open(args.base) as f:
            base = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading base file {args.base}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.head) as f:
            head = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading head file {args.head}: {e}", file=sys.stderr)
        sys.exit(1)

    # Detect types
    base_type = detect_type(base)
    head_type = detect_type(head)
    if not base_type or not head_type:
        print("Error: could not detect analyzer type from JSON", file=sys.stderr)
        sys.exit(1)
    if base_type != head_type:
        print(f"Error: type mismatch — base is {base_type}, head is {head_type}", file=sys.stderr)
        sys.exit(1)

    # Run diff
    diff_funcs = {
        "schematic": diff_schematic,
        "pcb": diff_pcb,
        "emc": diff_emc,
        "spice": diff_spice,
    }
    diff_result = diff_funcs[base_type](base, head, args.threshold)
    summary = build_summary(base_type, diff_result)

    output = {
        "diff_version": "1.0",
        "analyzer_type": base_type,
        "base_file": args.base,
        "head_file": args.head,
        "has_changes": summary["total_changes"] > 0,
        "summary": summary,
        "diff": diff_result,
    }

    if args.text:
        text = format_text(output)
        if args.output:
            with open(args.output, "w") as f:
                f.write(text)
        else:
            print(text)
    else:
        output_json = json.dumps(output, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output_json)
        else:
            print(output_json)


if __name__ == "__main__":
    main()
