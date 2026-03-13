"""
Signal path detector functions extracted from analyze_signal_paths().

Each detector takes an AnalysisContext (ctx) and returns its detection results.
Some detectors also take prior results for cross-references.
"""

import math
import re

from kicad_utils import (
    _LOAD_TYPE_KEYWORDS,
    _REGULATOR_VREF,
    format_frequency as _format_frequency,
    is_ground_name as _is_ground_name,
    is_power_net_name as _is_power_net_name,
    lookup_regulator_vref as _lookup_regulator_vref,
    parse_value,
    parse_voltage_from_net_name as _parse_voltage_from_net_name,
)
from kicad_types import AnalysisContext


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_net_components(ctx: AnalysisContext, net_name, exclude_ref):
    """Get components on a net excluding the transistor itself."""
    if net_name not in ctx.nets:
        return []
    result_comps = []
    for p in ctx.nets[net_name]["pins"]:
        if p["component"] == exclude_ref:
            continue
        comp = ctx.comp_lookup.get(p["component"])
        if comp:
            result_comps.append({
                "reference": p["component"],
                "type": comp["type"],
                "value": comp["value"],
                "pin_name": p.get("pin_name", ""),
                "pin_number": p["pin_number"],
            })
    return result_comps


def _classify_load(ctx: AnalysisContext, net_name, exclude_ref):
    """Classify what's on a net as a load type.

    Checks net name keywords first (motor, heater, fan, solenoid, valve,
    pump, relay, speaker, buzzer, lamp) for cases where the net name
    reveals the load type better than the connected components.
    Falls back to component-type classification.
    """
    # Net name keyword classification — catches loads driven through
    # connectors or across sheet boundaries where component type alone
    # would just show "connector" or "other"
    if net_name:
        nu = net_name.upper()
        for load_type, keywords in _LOAD_TYPE_KEYWORDS.items():
            if any(kw in nu for kw in keywords):
                return load_type

    comps = _get_net_components(ctx, net_name, exclude_ref)
    types = {c["type"] for c in comps}
    if "inductor" in types:
        return "inductive"
    if "led" in types:
        return "led"
    if types == {"resistor"} or types == {"resistor", "capacitor"}:
        return "resistive"
    if "connector" in types:
        return "connector"
    if "ic" in types:
        return "ic"
    if "transistor" in types:
        return "transistor"  # cascaded
    return "other"


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_voltage_dividers(ctx: AnalysisContext) -> dict:
    """Detect voltage dividers and feedback networks.

    Returns dict with keys ``voltage_dividers`` and ``feedback_networks``.
    """
    voltage_dividers: list[dict] = []
    feedback_networks: list[dict] = []

    # ---- Voltage Dividers ----
    # Two resistors in series between different nets, with a mid-point net
    resistors = [c for c in ctx.components if c["type"] == "resistor" and c["reference"] in ctx.parsed_values]

    # Index resistors by their nets for O(n) pair-finding instead of O(n²)
    resistor_nets = {}  # ref -> (net1, net2)
    net_to_resistors = {}  # net_name -> [refs]
    for r in resistors:
        n1, n2 = ctx.get_two_pin_nets(r["reference"])
        if not n1 or not n2 or n1 == n2:
            continue
        resistor_nets[r["reference"]] = (n1, n2)
        net_to_resistors.setdefault(n1, []).append(r["reference"])
        net_to_resistors.setdefault(n2, []).append(r["reference"])

    # Check pairs of resistors that share a net (potential dividers)
    vd_seen = set()  # track (r1, r2) pairs to avoid duplicates
    for net_name, refs in net_to_resistors.items():
        if len(refs) < 2:
            continue
        for i, r1_ref in enumerate(refs):
            r1_n1, r1_n2 = resistor_nets[r1_ref]
            r1 = ctx.comp_lookup[r1_ref]
            for r2_ref in refs[i + 1:]:
                pair_key = (min(r1_ref, r2_ref), max(r1_ref, r2_ref))
                if pair_key in vd_seen:
                    continue
                vd_seen.add(pair_key)

                r2_n1, r2_n2 = resistor_nets[r2_ref]
                r2 = ctx.comp_lookup[r2_ref]

                # Find shared net (mid-point)
                r1_nets = {r1_n1, r1_n2}
                r2_nets = {r2_n1, r2_n2}
                shared = r1_nets & r2_nets
                if len(shared) != 1:
                    continue

                mid_net = shared.pop()
                top_net = (r1_nets - {mid_net}).pop()
                bot_net = (r2_nets - {mid_net}).pop()

                # Reject if mid-point is a power rail with many connections —
                # that's a power bus, not a divider output. Real divider mid-points
                # connect to 2 resistors + maybe an IC input (≤4 connections).
                if ctx.is_power_net(mid_net) or ctx.is_ground(mid_net):
                    mid_pin_count = len(ctx.nets.get(mid_net, {}).get("pins", []))
                    if mid_pin_count > 4:
                        continue

                # One end should be power, other should be ground (or another power)
                # Determine orientation: top is higher voltage, bottom is lower
                if ctx.is_ground(top_net) and ctx.is_power_net(bot_net):
                    top_net, bot_net = bot_net, top_net
                    r1, r2 = r2, r1
                elif not (ctx.is_power_net(top_net) and (ctx.is_ground(bot_net) or ctx.is_power_net(bot_net))):
                    # Also catch feedback dividers: output -> mid -> ground
                    if not ctx.is_ground(bot_net):
                        continue

                r1_val = ctx.parsed_values[r1["reference"]]
                r2_val = ctx.parsed_values[r2["reference"]]
                if r1_val <= 0 or r2_val <= 0:
                    continue

                # Determine which is top/bottom based on net position
                if ctx.is_ground(bot_net):
                    # r_top connects top_net to mid, r_bot connects mid to gnd
                    # Re-derive nets from current r1/r2 (may have been swapped above)
                    r1_nets_cur = set(ctx.get_two_pin_nets(r1["reference"]))
                    if top_net in r1_nets_cur:
                        r_top, r_bot = r1_val, r2_val
                        r_top_ref, r_bot_ref = r1["reference"], r2["reference"]
                    else:
                        r_top, r_bot = r2_val, r1_val
                        r_top_ref, r_bot_ref = r2["reference"], r1["reference"]

                    ratio = r_bot / (r_top + r_bot)

                    divider = {
                        "r_top": {"ref": r_top_ref, "value": ctx.comp_lookup[r_top_ref]["value"], "ohms": r_top},
                        "r_bottom": {"ref": r_bot_ref, "value": ctx.comp_lookup[r_bot_ref]["value"], "ohms": r_bot},
                        "top_net": top_net,
                        "mid_net": mid_net,
                        "bottom_net": bot_net,
                        "ratio": round(ratio, 6),
                    }

                    # Check if mid-point connects to a known feedback pin
                    if mid_net in ctx.nets:
                        mid_pins = [p for p in ctx.nets[mid_net]["pins"]
                                    if p["component"] != r_top_ref
                                    and p["component"] != r_bot_ref
                                    and not p["component"].startswith("#")]
                        if mid_pins:
                            divider["mid_point_connections"] = mid_pins
                            # If connected to an IC FB pin, this is likely a feedback network
                            for mp in mid_pins:
                                if "FB" in mp.get("pin_name", "").upper():
                                    divider["is_feedback"] = True
                                    feedback_networks.append(divider)
                                    break

                    voltage_dividers.append(divider)

    return {"voltage_dividers": voltage_dividers, "feedback_networks": feedback_networks}


def detect_rc_filters(ctx: AnalysisContext, voltage_dividers: list[dict]) -> list[dict]:
    """Detect RC filters. Takes voltage_dividers to exclude VD resistors."""
    results_rc: list[dict] = []

    resistors = [c for c in ctx.components if c["type"] == "resistor" and c["reference"] in ctx.parsed_values]

    # Index resistors by their nets
    resistor_nets = {}
    for r in resistors:
        n1, n2 = ctx.get_two_pin_nets(r["reference"])
        if not n1 or not n2 or n1 == n2:
            continue
        resistor_nets[r["reference"]] = (n1, n2)

    # ---- RC Filters ----
    # R and C must share a SIGNAL net (not power/ground) to form a real filter.
    # If they only share GND, every R and C in the circuit would match.
    # Exclude resistors that are part of voltage dividers — pairing a feedback
    # divider resistor with an output decoupling cap is a common false positive.
    vd_resistor_refs = set()
    for vd in voltage_dividers:
        vd_resistor_refs.add(vd["r_top"]["ref"])
        vd_resistor_refs.add(vd["r_bottom"]["ref"])

    capacitors = [c for c in ctx.components if c["type"] == "capacitor" and c["reference"] in ctx.parsed_values]

    # Index capacitors by net for O(n) RC pair-finding instead of O(R*C)
    cap_nets = {}  # ref -> (net1, net2)
    net_to_caps = {}  # net_name -> [refs]
    for cap in capacitors:
        cn1, cn2 = ctx.get_two_pin_nets(cap["reference"])
        if not cn1 or not cn2 or cn1 == cn2:
            continue
        cap_nets[cap["reference"]] = (cn1, cn2)
        net_to_caps.setdefault(cn1, []).append(cap["reference"])
        net_to_caps.setdefault(cn2, []).append(cap["reference"])

    for res in resistors:
        if res["reference"] in vd_resistor_refs:
            continue  # Skip voltage divider resistors
        if res["reference"] not in resistor_nets:
            continue
        r_n1, r_n2 = resistor_nets[res["reference"]]
        r_nets = {r_n1, r_n2}

        # Only check capacitors that share a net with this resistor
        candidate_caps = set()
        for rn in (r_n1, r_n2):
            if not ctx.is_power_net(rn) and not ctx.is_ground(rn):
                for cref in net_to_caps.get(rn, ()):
                    candidate_caps.add(cref)

        for cap_ref in candidate_caps:
            c_n1, c_n2 = cap_nets[cap_ref]
            c_nets = {c_n1, c_n2}

            shared = r_nets & c_nets
            if len(shared) != 1:
                continue

            shared_net = shared.pop()

            # The shared net must NOT be a power/ground rail — those create
            # false matches between every R and C on the board.
            if ctx.is_power_net(shared_net) or ctx.is_ground(shared_net):
                continue

            # Reject if shared net has too many connections — a real RC filter
            # node typically has 2-3 connections (R + C + maybe one IC pin).
            # High-fanout nets (>6 pins) are likely buses or IC rails where
            # R and C happen to share a node but don't form a filter.
            shared_pin_count = len(ctx.nets.get(shared_net, {}).get("pins", []))
            if shared_pin_count > 6:
                continue

            r_other = (r_nets - {shared_net}).pop()
            c_other = (c_nets - {shared_net}).pop()

            r_val = ctx.parsed_values[res["reference"]]
            c_val = ctx.parsed_values[cap_ref]

            # Compute cutoff frequency: fc = 1 / (2π·R·C)
            if r_val > 0 and c_val > 0:
                fc = 1.0 / (2.0 * math.pi * r_val * c_val)
                tau = r_val * c_val

                # Classify filter type
                if ctx.is_ground(c_other):
                    filter_type = "low-pass"
                elif ctx.is_ground(r_other):
                    filter_type = "high-pass"
                else:
                    filter_type = "RC-network"

                # Skip if R is very small — likely series termination or current
                # sense shunt, not an intentional filter
                if r_val < 10:
                    continue

                rc_entry = {
                    "type": filter_type,
                    "resistor": {"ref": res["reference"], "value": ctx.comp_lookup[res["reference"]]["value"], "ohms": r_val},
                    "capacitor": {"ref": cap_ref, "value": ctx.comp_lookup[cap_ref]["value"], "farads": c_val},
                    "cutoff_hz": round(fc, 2),
                    "time_constant_s": tau,
                    "input_net": r_other if filter_type == "low-pass" else shared_net,
                    "output_net": shared_net if filter_type == "low-pass" else r_other,
                    "ground_net": c_other if ctx.is_ground(c_other) else r_other,
                }

                rc_entry["cutoff_formatted"] = _format_frequency(fc)

                results_rc.append(rc_entry)

    # Merge RC filters where the same resistor pairs with multiple caps on
    # the same shared net (parallel caps = one effective filter, not N filters).
    _rc_groups: dict[tuple[str, str, str], list[dict]] = {}
    for rc in results_rc:
        key = (rc["resistor"]["ref"], rc.get("input_net", ""), rc.get("output_net", ""))
        _rc_groups.setdefault(key, []).append(rc)
    merged_rc: list[dict] = []
    for key, entries in _rc_groups.items():
        if len(entries) == 1:
            merged_rc.append(entries[0])
        else:
            total_c = sum(e["capacitor"]["farads"] for e in entries)
            r_val = entries[0]["resistor"]["ohms"]
            fc = 1.0 / (2.0 * math.pi * r_val * total_c)
            tau = r_val * total_c
            cap_refs = [e["capacitor"]["ref"] for e in entries]
            base = entries[0].copy()
            base["capacitor"] = {
                "ref": cap_refs[0],
                "value": f"{len(entries)} caps parallel",
                "farads": total_c,
                "parallel_caps": cap_refs,
            }
            base["cutoff_hz"] = round(fc, 2)
            base["time_constant_s"] = tau
            base["cutoff_formatted"] = _format_frequency(fc)
            merged_rc.append(base)
    return merged_rc


def detect_lc_filters(ctx: AnalysisContext) -> list[dict]:
    """Detect LC filters."""
    capacitors = [c for c in ctx.components if c["type"] == "capacitor" and c["reference"] in ctx.parsed_values]
    inductors = [c for c in ctx.components if c["type"] in ("inductor", "ferrite_bead")
                 and c["reference"] in ctx.parsed_values]

    # Collect LC pairs grouped by (inductor, shared_net). Multiple caps on
    # the same inductor output node are parallel decoupling, not separate
    # filters — merge them into one entry with summed capacitance.
    _lc_groups: dict[tuple[str, str], list[dict]] = {}

    for ind in inductors:
        l_n1, l_n2 = ctx.get_two_pin_nets(ind["reference"])
        if not l_n1 or not l_n2:
            continue

        for cap in capacitors:
            c_n1, c_n2 = ctx.get_two_pin_nets(cap["reference"])
            if not c_n1 or not c_n2:
                continue

            l_nets = {l_n1, l_n2}
            c_nets = {c_n1, c_n2}
            # Skip components with both pins on the same net (shorted)
            if len(l_nets) < 2 or len(c_nets) < 2:
                continue
            shared = l_nets & c_nets
            if len(shared) != 1:
                continue

            shared_net_lc = shared.pop()
            # Skip if shared net is power/ground (would match all L-C pairs)
            if ctx.is_power_net(shared_net_lc) or ctx.is_ground(shared_net_lc):
                continue

            # Skip bootstrap capacitors: cap between BST/BOOT pin and SW/LX node.
            # These are gate-drive charge pumps, not signal filters.
            cap_other_net = (c_nets - {shared_net_lc}).pop()
            is_bootstrap = False
            if cap_other_net and cap_other_net in ctx.nets:
                for p in ctx.nets[cap_other_net]["pins"]:
                    pn = p.get("pin_name", "").upper().rstrip("0123456789")
                    pn_parts = {pp.strip() for pp in pn.split("/")}
                    if pn_parts & {"BST", "BOOT", "BOOTSTRAP", "CBST"}:
                        is_bootstrap = True
                        break
            if is_bootstrap:
                continue

            l_val = ctx.parsed_values[ind["reference"]]
            c_val = ctx.parsed_values[cap["reference"]]

            if l_val > 0 and c_val > 0:
                f0 = 1.0 / (2.0 * math.pi * math.sqrt(l_val * c_val))
                z0 = math.sqrt(l_val / c_val)  # characteristic impedance

                lc_entry = {
                    "inductor": {"ref": ind["reference"], "value": ctx.comp_lookup[ind["reference"]]["value"], "henries": l_val},
                    "capacitor": {"ref": cap["reference"], "value": ctx.comp_lookup[cap["reference"]]["value"], "farads": c_val},
                    "resonant_hz": round(f0, 2),
                    "impedance_ohms": round(z0, 2),
                    "shared_net": shared_net_lc,
                }

                lc_entry["resonant_formatted"] = _format_frequency(f0)

                _lc_groups.setdefault((ind["reference"], shared_net_lc), []).append(lc_entry)

    # Merge parallel caps per inductor-net pair
    lc_filters: list[dict] = []
    for (_ind_ref, _shared_net), entries in _lc_groups.items():
        if len(entries) == 1:
            lc_filters.append(entries[0])
        else:
            total_c = sum(e["capacitor"]["farads"] for e in entries)
            l_val = entries[0]["inductor"]["henries"]
            f0 = 1.0 / (2.0 * math.pi * math.sqrt(l_val * total_c))
            z0 = math.sqrt(l_val / total_c)
            cap_refs = [e["capacitor"]["ref"] for e in entries]
            merged = {
                "inductor": entries[0]["inductor"],
                "capacitor": {
                    "ref": cap_refs[0],
                    "value": f"{len(entries)} caps parallel",
                    "farads": total_c,
                    "parallel_caps": cap_refs,
                },
                "resonant_hz": round(f0, 2),
                "impedance_ohms": round(z0, 2),
                "shared_net": _shared_net,
            }
            merged["resonant_formatted"] = _format_frequency(f0)
            lc_filters.append(merged)
    return lc_filters


def detect_crystal_circuits(ctx: AnalysisContext) -> list[dict]:
    """Detect crystal oscillator circuits."""
    crystal_circuits: list[dict] = []
    crystals = [c for c in ctx.components if c["type"] == "crystal"]
    for xtal in crystals:
        xtal_pins = xtal.get("pins", [])
        if len(xtal_pins) < 2:
            continue

        # Find capacitors connected to crystal signal pins (not power/ground)
        xtal_nets = set()
        for pin in xtal_pins:
            net_name, _ = ctx.pin_net.get((xtal["reference"], pin["number"]), (None, None))
            if net_name and not ctx.is_power_net(net_name) and not ctx.is_ground(net_name):
                xtal_nets.add(net_name)

        load_caps = []
        for net_name in xtal_nets:
            if net_name not in ctx.nets:
                continue
            for p in ctx.nets[net_name]["pins"]:
                if p["component"] != xtal["reference"] and ctx.comp_lookup.get(p["component"], {}).get("type") == "capacitor":
                    cap_ref = p["component"]
                    cap_val = ctx.parsed_values.get(cap_ref)
                    if cap_val:
                        # Check if other end of cap goes to ground
                        cap_n1, cap_n2 = ctx.get_two_pin_nets(cap_ref)
                        other_net = cap_n2 if cap_n1 == net_name else cap_n1
                        if ctx.is_ground(other_net):
                            load_caps.append({
                                "ref": cap_ref,
                                "value": ctx.comp_lookup[cap_ref]["value"],
                                "farads": cap_val,
                                "net": net_name,
                            })

        xtal_entry = {
            "reference": xtal["reference"],
            "value": xtal.get("value", ""),
            "frequency": parse_value(xtal.get("value", "")),
            "load_caps": load_caps,
        }

        # Compute effective load capacitance: CL = (C1 * C2) / (C1 + C2) + C_stray
        if len(load_caps) >= 2:
            c1 = load_caps[0]["farads"]
            c2 = load_caps[1]["farads"]
            c_stray = 3e-12  # typical stray capacitance estimate
            cl_eff = (c1 * c2) / (c1 + c2) + c_stray
            xtal_entry["effective_load_pF"] = round(cl_eff * 1e12, 2)
            xtal_entry["note"] = f"CL_eff = ({load_caps[0]['value']} * {load_caps[1]['value']}) / ({load_caps[0]['value']} + {load_caps[1]['value']}) + ~3pF stray"

        crystal_circuits.append(xtal_entry)
    return crystal_circuits


def detect_decoupling(ctx: AnalysisContext) -> list[dict]:
    """Detect decoupling capacitors per power rail."""
    decoupling_analysis: list[dict] = []

    # For each power rail, compute total decoupling capacitance and frequency coverage
    power_nets = {}
    for net_name, net_info in ctx.nets.items():
        if net_name.startswith("__unnamed_"):
            continue
        if ctx.is_ground(net_name):
            continue
        if ctx.is_power_net(net_name):
            power_nets[net_name] = net_info

    for rail_name, rail_info in power_nets.items():
        rail_caps = []
        for p in rail_info["pins"]:
            comp = ctx.comp_lookup.get(p["component"])
            if comp and comp["type"] == "capacitor":
                cap_val = ctx.parsed_values.get(p["component"])
                if cap_val:
                    # Check if other pin goes to ground
                    c_n1, c_n2 = ctx.get_two_pin_nets(p["component"])
                    other = c_n2 if c_n1 == rail_name else c_n1
                    if ctx.is_ground(other):
                        self_resonant = 1.0 / (2.0 * math.pi * math.sqrt(1e-9 * cap_val))  # ~1nH ESL estimate
                        rail_caps.append({
                            "ref": p["component"],
                            "value": comp["value"],
                            "farads": cap_val,
                            "self_resonant_hz": round(self_resonant, 0),
                        })

        if rail_caps:
            total_cap = sum(c["farads"] for c in rail_caps)
            decoupling_analysis.append({
                "rail": rail_name,
                "capacitors": rail_caps,
                "total_capacitance_uF": round(total_cap * 1e6, 3),
                "cap_count": len(rail_caps),
            })
    return decoupling_analysis


def detect_current_sense(ctx: AnalysisContext) -> list[dict]:
    """Detect current sense circuits."""
    current_sense: list[dict] = []
    shunt_candidates = [
        c for c in ctx.components
        if c["type"] == "resistor" and c["reference"] in ctx.parsed_values
        and 0 < ctx.parsed_values[c["reference"]] <= 0.5
    ]

    for shunt in shunt_candidates:
        # Support both 2-pin and 4-pin Kelvin shunts (R_Shunt: pins 1,4=current; 2,3=sense)
        sense_n1, sense_n2 = None, None
        # Check for 4-pin Kelvin first (pins 1,4=current path; 2,3=sense)
        n1, _ = ctx.pin_net.get((shunt["reference"], "1"), (None, None))
        n4, _ = ctx.pin_net.get((shunt["reference"], "4"), (None, None))
        n3, _ = ctx.pin_net.get((shunt["reference"], "3"), (None, None))
        if n1 and n4 and n3:
            # 4-pin Kelvin shunt
            n2, _ = ctx.pin_net.get((shunt["reference"], "2"), (None, None))
            s_n1, s_n2 = n1, n4
            sense_n1, sense_n2 = n2, n3
        else:
            s_n1, s_n2 = ctx.get_two_pin_nets(shunt["reference"])
            if not s_n1 or not s_n2:
                continue
        if s_n1 == s_n2:
            continue
        # Skip if both nets are power/ground (bulk decoupling, not sensing)
        s1_pwr_or_gnd = ctx.is_ground(s_n1) or ctx.is_power_net(s_n1)
        s2_pwr_or_gnd = ctx.is_ground(s_n2) or ctx.is_power_net(s_n2)
        if s1_pwr_or_gnd and s2_pwr_or_gnd:
            continue

        shunt_ohms = ctx.parsed_values[shunt["reference"]]

        # Find ICs connected to BOTH sides of the shunt.
        # Ground-net exclusion: GND connects to every IC on the board, so it
        # can't be used for "IC on both sides" matching.  When one side of the
        # shunt is GND, skip GND-side component collection entirely and instead
        # match only ICs on the non-GND side that are known sense parts or have
        # sense-related pin names on the shunt nets.
        _SENSE_PIN_PREFIXES = frozenset({
            "CS", "CSP", "CSN", "ISNS", "ISENSE", "IMON", "IOUT",
            "SEN", "SENSE", "VSENSE", "VSEN", "VS", "INP", "INN",
            "IS", "IAVG", "ISET",
        })
        _SENSE_IC_KEYWORDS = frozenset({
            "ina", "acs7", "ad8210", "ad8217", "ad8218", "max9938",
            "max4080", "max4081", "max471", "ltc6101", "ltc6102",
            "ltc6103", "ltc4151", "ina226", "ina233", "ina180",
            "ina181", "ina190", "ina199", "ina200", "ina210",
            "ina240", "ina250", "ina260", "ina300", "ina381",
            "pam2401", "zxct", "acs71", "acs72", "asc",
        })

        # Treat power nets the same as GND — they connect to many ICs
        # through power pins and would cause the same false positive flood.
        side1_is_pwr = ctx.is_ground(s_n1) or ctx.is_power_net(s_n1)
        side2_is_pwr = ctx.is_ground(s_n2) or ctx.is_power_net(s_n2)
        has_pwr_side = side1_is_pwr or side2_is_pwr

        comps_on_n1 = set()
        comps_on_n2 = set()
        check_nets_1 = [s_n1] + ([sense_n1] if sense_n1 else [])
        check_nets_2 = [s_n2] + ([sense_n2] if sense_n2 else [])

        # Collect components on each side (skip power/GND side entirely)
        if not side1_is_pwr:
            for nn in check_nets_1:
                if nn in ctx.nets:
                    for p in ctx.nets[nn]["pins"]:
                        if p["component"] != shunt["reference"]:
                            comps_on_n1.add(p["component"])
        if not side2_is_pwr:
            for nn in check_nets_2:
                if nn in ctx.nets:
                    for p in ctx.nets[nn]["pins"]:
                        if p["component"] != shunt["reference"]:
                            comps_on_n2.add(p["component"])

        if has_pwr_side:
            # One side is a power/GND rail: use only the non-power side's
            # components.  Filter to ICs that are plausible current sense
            # monitors: either by part name or by having sense-related pin
            # names on the shunt nets.
            non_pwr_comps = comps_on_n1 if not side1_is_pwr else comps_on_n2
            non_pwr_nets = check_nets_1 if not side1_is_pwr else check_nets_2
            sense_ics_set = set()
            for cref in non_pwr_comps:
                ic_comp = ctx.comp_lookup.get(cref)
                if not ic_comp or ic_comp["type"] != "ic":
                    continue
                # Check if part is a known sense IC
                val_lower = (ic_comp.get("value", "") + " " + ic_comp.get("lib_id", "")).lower()
                if any(kw in val_lower for kw in _SENSE_IC_KEYWORDS):
                    sense_ics_set.add(cref)
                    continue
                # Check if the IC's pin on this net has a sense-related name
                for nn in non_pwr_nets:
                    if nn not in ctx.nets:
                        continue
                    for p in ctx.nets[nn]["pins"]:
                        if p["component"] == cref:
                            pn = p.get("pin_name", "").upper().rstrip("0123456789+-")
                            if pn in _SENSE_PIN_PREFIXES:
                                sense_ics_set.add(cref)
            sense_ics = sense_ics_set
        else:
            # Neither side is GND: use original "IC on both sides" algorithm
            sense_ics = comps_on_n1 & comps_on_n2
            # 1-hop: if no IC on both sides directly, look through filter resistors
            # (e.g., shunt -> R_filter -> sense IC is a common BMS pattern)
            if not any(ctx.comp_lookup.get(c, {}).get("type") == "ic" for c in sense_ics):
                for nn in check_nets_1:
                    if nn not in ctx.nets:
                        continue
                    for p in ctx.nets[nn]["pins"]:
                        r_comp = ctx.comp_lookup.get(p["component"])
                        if r_comp and r_comp["type"] == "resistor" and p["component"] != shunt["reference"]:
                            r_other = ctx.get_two_pin_nets(p["component"])
                            if r_other[0] and r_other[1]:
                                hop_net = r_other[1] if r_other[0] == nn else r_other[0]
                                if hop_net in ctx.nets:
                                    for hp in ctx.nets[hop_net]["pins"]:
                                        comps_on_n1.add(hp["component"])
                for nn in check_nets_2:
                    if nn not in ctx.nets:
                        continue
                    for p in ctx.nets[nn]["pins"]:
                        r_comp = ctx.comp_lookup.get(p["component"])
                        if r_comp and r_comp["type"] == "resistor" and p["component"] != shunt["reference"]:
                            r_other = ctx.get_two_pin_nets(p["component"])
                            if r_other[0] and r_other[1]:
                                hop_net = r_other[1] if r_other[0] == nn else r_other[0]
                                if hop_net in ctx.nets:
                                    for hp in ctx.nets[hop_net]["pins"]:
                                        comps_on_n2.add(hp["component"])
                sense_ics = comps_on_n1 & comps_on_n2
        for ic_ref in sense_ics:
            ic_comp = ctx.comp_lookup.get(ic_ref)
            if not ic_comp:
                continue
            # Only consider ICs (sense amplifiers, MCUs with ADC)
            if ic_comp["type"] not in ("ic",):
                continue

            current_sense.append({
                "shunt": {
                    "ref": shunt["reference"],
                    "value": shunt["value"],
                    "ohms": shunt_ohms,
                },
                "sense_ic": {
                    "ref": ic_ref,
                    "value": ic_comp.get("value", ""),
                    "type": ic_comp.get("type", ""),
                },
                "high_net": s_n1,
                "low_net": s_n2,
                "max_current_50mV_A": round(0.05 / shunt_ohms, 3) if shunt_ohms > 0 else None,
                "max_current_100mV_A": round(0.1 / shunt_ohms, 3) if shunt_ohms > 0 else None,
            })
    return current_sense


def detect_power_regulators(ctx: AnalysisContext, voltage_dividers: list[dict]) -> list[dict]:
    """Detect power regulator topology. Takes voltage_dividers for feedback matching."""
    power_regulators: list[dict] = []

    for ic in [c for c in ctx.components if c["type"] == "ic"]:
        ref = ic["reference"]
        ic_pins = {}  # pin_name -> (net_name, pin_number)
        for pkey, (net_name, _) in ctx.pin_net.items():
            if pkey[0] == ref:
                # Find pin name from net info
                pin_num = pkey[1]
                pin_name = ""
                if net_name in ctx.nets:
                    for p in ctx.nets[net_name]["pins"]:
                        if p["component"] == ref and p["pin_number"] == pin_num:
                            pin_name = p.get("pin_name", "").upper()
                            break
                ic_pins[pin_name] = (net_name, pin_num)

        # Look for regulator pin patterns
        fb_pin = None
        sw_pin = None
        en_pin = None
        vin_pin = None
        vout_pin = None
        boot_pin = None

        for pname, (net, pnum) in ic_pins.items():
            # Use startswith for pins that may have numeric suffixes (FB1, SW2, etc.)
            pn_base = pname.rstrip("0123456789")  # Strip trailing digits
            # Split composite pin names like "FB/VOUT" into parts
            pn_parts = {p.strip() for p in pname.split("/")} | {pn_base}
            if pn_parts & {"FB", "VFB", "ADJ", "VADJ"}:
                if not fb_pin:
                    fb_pin = (pname, net)
                # Composite names like "FB/VOUT" also set vout_pin
                if not vout_pin and pn_parts & {"VOUT", "VO", "OUT", "OUTPUT"}:
                    vout_pin = (pname, net)
            elif pn_parts & {"SW", "PH", "LX"}:
                if not sw_pin:
                    sw_pin = (pname, net)
            elif pname in ("EN", "ENABLE", "ON", "~{SHDN}", "SHDN", "~{EN}") or \
                 (pn_base == "EN" and len(pname) <= 3):
                en_pin = (pname, net)
            elif pn_parts & {"VIN", "VI", "IN", "PVIN", "AVIN", "INPUT"}:
                vin_pin = (pname, net)
            elif pn_parts & {"VOUT", "VO", "OUT", "OUTPUT"}:
                vout_pin = (pname, net)
            elif pn_parts & {"BOOT", "BST", "BOOTSTRAP", "CBST"}:
                boot_pin = (pname, net)

        if not fb_pin and not sw_pin and not vout_pin:
            continue  # Not a regulator

        # Early lib_id check
        lib_id_raw = ic.get("lib_id", "")
        lib_part_name = lib_id_raw.split(":")[-1] if ":" in lib_id_raw else ""
        desc_lower = ic.get("description", "").lower()
        lib_val_lower = (lib_id_raw + " " + ic.get("value", "") + " " + lib_part_name).lower()
        reg_lib_keywords = ("regulator", "regul", "ldo", "vreg", "buck", "boost",
                           "converter", "dc-dc", "dc_dc", "linear_regulator",
                           "switching_regulator",
                           "ams1117", "lm317", "lm78", "lm79", "ld1117", "ld33",
                           "ap6", "tps5", "tps6", "tlv7", "rt5", "mp1", "mp2",
                           "sy8", "max150", "max170", "ncp1", "xc6", "mcp170",
                           "mic29", "mic55", "ap2112", "ap2210", "ap73",
                           "ncv4", "lm26", "lm11", "78xx",
                           "79xx", "lt308", "lt36", "ltc36", "lt86", "ltc34")
        has_reg_keyword = (any(k in lib_val_lower for k in reg_lib_keywords) or
                          any(k in desc_lower for k in ("regulator", "ldo", "vreg",
                                                        "voltage regulator")))

        if not fb_pin and not boot_pin:
            if not sw_pin and not has_reg_keyword:
                # Only VOUT pin, no regulator keywords → check if VIN+VOUT
                # both connect to distinct power nets (custom-lib LDOs like TC1185)
                if vin_pin and vout_pin:
                    in_net = vin_pin[1]
                    out_net = vout_pin[1]
                    if not (ctx.is_power_net(in_net) and ctx.is_power_net(out_net)
                            and in_net != out_net):
                        continue
                else:
                    continue
            if sw_pin and not has_reg_keyword:
                # SW pin but check if inductor is connected
                sw_has_inductor = False
                sw_net_name = sw_pin[1]
                if sw_net_name in ctx.nets:
                    for p in ctx.nets[sw_net_name]["pins"]:
                        comp_c = ctx.comp_lookup.get(p["component"])
                        if comp_c and comp_c["type"] == "inductor":
                            sw_has_inductor = True
                            break
                if not sw_has_inductor:
                    continue

        reg_info = {
            "ref": ref,
            "value": ic["value"],
            "lib_id": ic.get("lib_id", ""),
        }

        # Determine topology
        if sw_pin:
            # Check if SW pin connects to an inductor
            sw_net = sw_pin[1]
            has_inductor = False
            inductor_ref = None
            if sw_net in ctx.nets:
                for p in ctx.nets[sw_net]["pins"]:
                    comp = ctx.comp_lookup.get(p["component"])
                    if comp and comp["type"] == "inductor":
                        has_inductor = True
                        inductor_ref = p["component"]
                        break
            if has_inductor:
                reg_info["topology"] = "switching"
                reg_info["inductor"] = inductor_ref
                if boot_pin:
                    reg_info["has_bootstrap"] = True
            else:
                reg_info["topology"] = "switching"  # SW pin but no inductor found
        elif vout_pin and not sw_pin:
            # Check if description/lib_id suggests a switching regulator whose
            # SW pin wasn't found (e.g., pin in different unit or unusual name)
            _switching_kw = ("buck", "boost", "switching", "step-down", "step-up",
                             "step down", "step up", "dc-dc", "dc_dc", "smps",
                             "converter", "synchronous")
            if any(k in desc_lower for k in _switching_kw) or \
               any(k in lib_val_lower for k in _switching_kw):
                reg_info["topology"] = "switching"
            else:
                reg_info["topology"] = "LDO"
        elif fb_pin and not sw_pin:
            reg_info["topology"] = "unknown"

        # Detect inverting topology from part name/description or output net name
        inverting_kw = ("invert", "inv_", "_inv", "negative output", "neg_out")
        is_inverting = any(k in lib_val_lower for k in inverting_kw) or \
                       any(k in desc_lower for k in inverting_kw)

        # Extract input/output rails
        if vin_pin:
            reg_info["input_rail"] = vin_pin[1]
        if vout_pin:
            reg_info["output_rail"] = vout_pin[1]
            # Also check if output rail name suggests negative voltage
            out_net_u = vout_pin[1].upper()
            if re.search(r'[-](\d)', out_net_u) or "NEG" in out_net_u or out_net_u.startswith("-"):
                is_inverting = True
        if is_inverting:
            reg_info["inverting"] = True

        # Check feedback divider for output voltage estimation
        if fb_pin:
            fb_net = fb_pin[1]
            reg_info["fb_net"] = fb_net
            # Try part-specific Vref lookup first, fall back to heuristic sweep
            known_vref, vref_source = _lookup_regulator_vref(
                ic.get("value", ""), ic.get("lib_id", ""))
            # Find matching voltage divider
            for vd in voltage_dividers:
                if vd["mid_net"] == fb_net:
                    ratio = vd["ratio"]
                    if known_vref is not None:
                        # Use the known Vref from the lookup table
                        v_out = known_vref / ratio if ratio > 0 else 0
                        if 0.5 < v_out < 60:
                            reg_info["estimated_vout"] = round(v_out, 3)
                            reg_info["assumed_vref"] = known_vref
                            reg_info["vref_source"] = "lookup"
                            reg_info["feedback_divider"] = {
                                "r_top": vd["r_top"]["ref"],
                                "r_bottom": vd["r_bottom"]["ref"],
                                "ratio": ratio,
                            }
                    else:
                        # Heuristic: try common Vref values
                        for vref in [0.6, 0.8, 1.0, 1.22, 1.25]:
                            v_out = vref / ratio if ratio > 0 else 0
                            if 0.5 < v_out < 60:
                                reg_info["estimated_vout"] = round(v_out, 3)
                                reg_info["assumed_vref"] = vref
                                reg_info["vref_source"] = "heuristic"
                                reg_info["feedback_divider"] = {
                                    "r_top": vd["r_top"]["ref"],
                                    "r_bottom": vd["r_bottom"]["ref"],
                                    "ratio": ratio,
                                }
                                break
                    break

        # Negate Vout for inverting regulators
        if reg_info.get("inverting") and "estimated_vout" in reg_info:
            reg_info["estimated_vout"] = -abs(reg_info["estimated_vout"])

        # Only add if we found meaningful regulator features
        is_regulator = False
        if fb_pin or sw_pin or boot_pin:
            is_regulator = True
        elif vin_pin or vout_pin:
            in_net = vin_pin[1] if vin_pin else ""
            out_net = vout_pin[1] if vout_pin else ""
            if ctx.is_power_net(in_net) or ctx.is_power_net(out_net):
                is_regulator = True
            if has_reg_keyword:
                is_regulator = True

        if is_regulator and any(k in reg_info for k in ("topology", "input_rail", "output_rail", "estimated_vout")):
            power_regulators.append(reg_info)

    return power_regulators


def detect_protection_devices(ctx: AnalysisContext) -> list[dict]:
    """Detect protection devices (TVS, ESD, Schottky, fuses, etc.)."""
    protection_devices: list[dict] = []
    protection_types = ("diode", "varistor", "surge_arrester")
    tvs_keywords = ("tvs", "esd", "pesd", "prtr", "usblc", "sp0", "tpd", "ip4", "rclamp",
                     "smaj", "smbj", "p6ke", "1.5ke", "lesd", "nup")
    schottky_keywords = ("schottky", "d_schottky")

    for comp in ctx.components:
        if comp["type"] not in protection_types:
            continue
        val = comp.get("value", "").lower()
        lib = comp.get("lib_id", "").lower()
        desc = comp.get("description", "").lower()

        is_tvs = comp["type"] == "diode" and any(k in val or k in lib for k in tvs_keywords)
        is_schottky = comp["type"] == "diode" and any(k in lib or k in desc for k in schottky_keywords)
        is_non_diode_protection = comp["type"] in ("varistor", "surge_arrester")

        if comp["type"] == "diode" and not is_tvs and not is_schottky:
            continue

        # Multi-pin protection diodes (PRTR5V0U2X, etc.) — handle like ESD ICs
        comp_pins = comp.get("pins", [])
        if len(comp_pins) > 2 and is_tvs:
            if any(p["ref"] == comp["reference"] for p in protection_devices):
                continue
            protected = []
            for pin in comp_pins:
                net_name, _ = ctx.pin_net.get((comp["reference"], pin["number"]), (None, None))
                if net_name and not ctx.is_power_net(net_name) and not ctx.is_ground(net_name):
                    protected.append(net_name)
            for net_name in set(protected):
                protection_devices.append({
                    "ref": comp["reference"],
                    "value": comp.get("value", ""),
                    "type": "esd_ic",
                    "protected_net": net_name,
                    "clamp_net": None,
                })
            continue

        d_n1, d_n2 = ctx.get_two_pin_nets(comp["reference"])
        if not d_n1 or not d_n2:
            continue

        protected_net = None
        prot_type = comp["type"]

        if is_schottky and not is_tvs:
            if ctx.is_power_net(d_n1) and (ctx.is_ground(d_n2) or ctx.is_power_net(d_n2)):
                protected_net = d_n1
                prot_type = "reverse_polarity"
            elif ctx.is_power_net(d_n2) and (ctx.is_ground(d_n1) or ctx.is_power_net(d_n1)):
                protected_net = d_n2
                prot_type = "reverse_polarity"
        else:
            if ctx.is_ground(d_n1) and not ctx.is_ground(d_n2):
                protected_net = d_n2
            elif ctx.is_ground(d_n2) and not ctx.is_ground(d_n1):
                protected_net = d_n1
            elif ctx.is_power_net(d_n1) and not ctx.is_power_net(d_n2):
                protected_net = d_n2
            elif ctx.is_power_net(d_n2) and not ctx.is_power_net(d_n1):
                protected_net = d_n1

        if protected_net:
            protection_devices.append({
                "ref": comp["reference"],
                "value": comp.get("value", ""),
                "type": prot_type,
                "protected_net": protected_net,
                "clamp_net": d_n1 if protected_net == d_n2 else d_n2,
            })

    # Also detect varistors and surge arresters (already typed correctly)
    for comp in ctx.components:
        if comp["type"] in ("varistor", "surge_arrester"):
            d_n1, d_n2 = ctx.get_two_pin_nets(comp["reference"])
            if not d_n1 or not d_n2:
                continue
            # Avoid duplicates
            if any(p["ref"] == comp["reference"] for p in protection_devices):
                continue
            protected_net = d_n1 if not ctx.is_ground(d_n1) else d_n2
            protection_devices.append({
                "ref": comp["reference"],
                "value": comp.get("value", ""),
                "type": comp["type"],
                "protected_net": protected_net,
                "clamp_net": d_n1 if protected_net == d_n2 else d_n2,
            })

    # PTC fuses / polyfuses used as overcurrent protection
    for comp in ctx.components:
        if comp["type"] != "fuse":
            continue
        if any(p["ref"] == comp["reference"] for p in protection_devices):
            continue
        d_n1, d_n2 = ctx.get_two_pin_nets(comp["reference"])
        if not d_n1 or not d_n2:
            continue
        protected_net = None
        if ctx.is_power_net(d_n1) and not ctx.is_power_net(d_n2) and not ctx.is_ground(d_n2):
            protected_net = d_n2
        elif ctx.is_power_net(d_n2) and not ctx.is_power_net(d_n1) and not ctx.is_ground(d_n1):
            protected_net = d_n1
        elif ctx.is_power_net(d_n1) and ctx.is_power_net(d_n2):
            protected_net = d_n2
        if protected_net:
            protection_devices.append({
                "ref": comp["reference"],
                "value": comp.get("value", ""),
                "type": "fuse",
                "protected_net": protected_net,
                "clamp_net": d_n1 if protected_net == d_n2 else d_n2,
            })

    # ---- IC-based ESD Protection ----
    esd_ic_keywords = ("usblc", "tpd", "prtr", "ip42", "sp05", "esda",
                       "pesd", "nup4", "sn65220", "dtc11", "sp72")
    for comp in ctx.components:
        if comp["type"] != "ic":
            continue
        val = comp.get("value", "").lower()
        lib = comp.get("lib_id", "").lower()
        if not any(k in val or k in lib for k in esd_ic_keywords):
            continue
        if any(p["ref"] == comp["reference"] for p in protection_devices):
            continue
        protected = []
        for pin in comp.get("pins", []):
            net_name, _ = ctx.pin_net.get((comp["reference"], pin["number"]), (None, None))
            if net_name and not ctx.is_power_net(net_name) and not ctx.is_ground(net_name):
                protected.append(net_name)
        for net_name in set(protected):
            protection_devices.append({
                "ref": comp["reference"],
                "value": comp.get("value", ""),
                "type": "esd_ic",
                "protected_net": net_name,
                "clamp_net": None,
            })

    return protection_devices


def detect_opamp_circuits(ctx: AnalysisContext) -> list[dict]:
    """Detect op-amp gain stage configurations."""
    opamp_circuits: list[dict] = []
    opamp_lib_keywords = ("amplifier_operational", "op_amp", "opamp")
    opamp_value_keywords = ("opa", "lm358", "lm324", "mcp6", "ad8", "tl07", "tl08",
                            "ne5532", "lf35", "lt623", "ths", "ada4",
                            "ina10", "ina11", "ina12", "ina13",
                            "ncs3", "lmc7", "lmv3", "max40", "max44",
                            "tsc10", "mcp60", "mcp61", "mcp65")

    seen_opamp_units = set()  # (ref, unit) to avoid multi-unit duplicates
    for ic in [c for c in ctx.components if c["type"] == "ic"]:
        lib = ic.get("lib_id", "").lower()
        val = ic.get("value", "").lower()
        desc = ic.get("description", "").lower()
        lib_part = lib.split(":")[-1] if ":" in lib else ""
        match_sources = [val, lib_part]
        if not (any(k in lib for k in opamp_lib_keywords) or
                any(s.startswith(k) for k in opamp_value_keywords for s in match_sources) or
                any(k in desc for k in ("opamp", "op-amp", "op amp", "operational amplifier"))):
            continue

        ref = ic["reference"]
        unit = ic.get("unit", 1)
        if (ref, unit) in seen_opamp_units:
            continue
        seen_opamp_units.add((ref, unit))

        # For multi-unit op-amps, restrict to this unit's pins.
        unit_pin_nums = None
        lib_id = ic.get("lib_id", "")
        sym_def = ctx.lib_symbols.get(lib_id)
        if sym_def and sym_def.get("unit_pins") and unit in sym_def["unit_pins"]:
            unit_pin_nums = {p["number"] for p in sym_def["unit_pins"][unit]}
            if 0 in sym_def["unit_pins"]:
                unit_pin_nums |= {p["number"] for p in sym_def["unit_pins"][0]}

        # Find op-amp pins: +IN, -IN, OUT
        pos_in = None
        neg_in = None
        out_pin = None
        for (pref, pnum), (net, _) in ctx.pin_net.items():
            if pref != ref or not net:
                continue
            if unit_pin_nums is not None and pnum not in unit_pin_nums:
                continue
            pin_name = ""
            if net in ctx.nets:
                for p in ctx.nets[net]["pins"]:
                    if p["component"] == ref and p["pin_number"] == pnum:
                        pin_name = p.get("pin_name", "").upper()
                        break
            if not pin_name:
                continue
            pn = pin_name.replace(" ", "")
            if pn in ("+", "+IN", "IN+", "INP", "V+IN", "NONINVERTING") or \
               (pn.startswith("+") and "IN" in pn):
                pos_in = (pin_name, net, pnum)
            elif pn in ("-", "-IN", "IN-", "INM", "V-IN", "INVERTING") or \
                 (pn.startswith("-") and "IN" in pn):
                neg_in = (pin_name, net, pnum)
            elif pn in ("OUT", "OUTPUT", "VOUT", "VO"):
                out_pin = (pin_name, net, pnum)
            elif pn in ("V+", "V-", "VCC", "VDD", "VEE", "VSS", "VS+", "VS-"):
                continue
            else:
                pin_type = ""
                if net in ctx.nets:
                    for p in ctx.nets[net]["pins"]:
                        if p["component"] == ref and p["pin_number"] == pnum:
                            pin_type = p.get("pin_type", "")
                            break
                if pin_type == "output" and not out_pin:
                    out_pin = (pin_name, net, pnum)
                elif pin_type == "input":
                    if not pos_in:
                        pos_in = (pin_name, net, pnum)
                    elif not neg_in:
                        neg_in = (pin_name, net, pnum)

        if not out_pin or not neg_in:
            continue

        out_net = out_pin[1]
        neg_net = neg_in[1]
        pos_net = pos_in[1] if pos_in else None

        # Find feedback resistor
        rf_ref = None
        rf_val = None
        if out_net in ctx.nets and neg_net != out_net:
            out_comps = {p["component"] for p in ctx.nets[out_net]["pins"] if p["component"] != ref}
            neg_comps = {p["component"] for p in ctx.nets[neg_net]["pins"] if p["component"] != ref}
            fb_resistors = out_comps & neg_comps
            for fb_ref in fb_resistors:
                comp = ctx.comp_lookup.get(fb_ref)
                if comp and comp["type"] == "resistor" and fb_ref in ctx.parsed_values:
                    rf_ref = fb_ref
                    rf_val = ctx.parsed_values[fb_ref]
                    break

            # 2-hop feedback
            if not rf_ref:
                for out_comp_ref in out_comps:
                    oc = ctx.comp_lookup.get(out_comp_ref)
                    if not oc or oc["type"] not in ("resistor", "capacitor"):
                        continue
                    o_n1, o_n2 = ctx.get_two_pin_nets(out_comp_ref)
                    if not o_n1 or not o_n2:
                        continue
                    mid = o_n2 if o_n1 == out_net else o_n1
                    if mid == out_net or ctx.is_ground(mid) or ctx.is_power_net(mid):
                        continue
                    if mid in ctx.nets:
                        mid_comps = {p["component"] for p in ctx.nets[mid]["pins"]
                                    if p["component"] != out_comp_ref}
                        fb_via_mid = mid_comps & neg_comps
                        for fb2 in fb_via_mid:
                            c2 = ctx.comp_lookup.get(fb2)
                            if c2 and c2["type"] in ("resistor", "capacitor"):
                                if oc["type"] == "resistor" and out_comp_ref in ctx.parsed_values:
                                    rf_ref = out_comp_ref
                                    rf_val = ctx.parsed_values[out_comp_ref]
                                elif c2["type"] == "resistor" and fb2 in ctx.parsed_values:
                                    rf_ref = fb2
                                    rf_val = ctx.parsed_values[fb2]
                                break
                    if rf_ref:
                        break

        # Find input resistor
        ri_ref = None
        ri_val = None
        if neg_net in ctx.nets:
            for p in ctx.nets[neg_net]["pins"]:
                if p["component"] == ref or p["component"] == rf_ref:
                    continue
                comp = ctx.comp_lookup.get(p["component"])
                if comp and comp["type"] == "resistor" and p["component"] in ctx.parsed_values:
                    r_n1, r_n2 = ctx.get_two_pin_nets(p["component"])
                    other = r_n2 if r_n1 == neg_net else r_n1
                    if other != out_net:
                        ri_ref = p["component"]
                        ri_val = ctx.parsed_values[p["component"]]
                        break

        # Determine configuration
        config = "unknown"
        gain = None
        if out_net == neg_net:
            config = "buffer"
            gain = 1.0
        elif rf_ref and ri_ref and ri_val and rf_val:
            if pos_net and pos_net != neg_net:
                pos_has_signal = pos_net and not ctx.is_power_net(pos_net) and not ctx.is_ground(pos_net)
                neg_has_signal = ri_ref is not None
                if pos_has_signal and not neg_has_signal:
                    config = "non_inverting"
                    gain = 1.0 + rf_val / ri_val
                else:
                    config = "inverting"
                    gain = -rf_val / ri_val
            else:
                config = "inverting"
                gain = -rf_val / ri_val
        elif rf_ref and not ri_ref:
            config = "transimpedance_or_buffer"
        elif not rf_ref:
            config = "comparator_or_open_loop"

        entry = {
            "reference": ref,
            "unit": unit,
            "value": ic["value"],
            "lib_id": ic.get("lib_id", ""),
            "configuration": config,
            "output_net": out_net,
            "inverting_input_net": neg_net,
            "non_inverting_input_net": pos_net,
        }
        if gain is not None:
            entry["gain"] = round(gain, 3)
            entry["gain_dB"] = round(20 * math.log10(abs(gain)), 1) if gain != 0 else None
        if rf_ref:
            entry["feedback_resistor"] = {"ref": rf_ref, "ohms": rf_val}
        if ri_ref:
            entry["input_resistor"] = {"ref": ri_ref, "ohms": ri_val}
        # Dedup
        dedup_key = (ref, out_net, neg_net)
        if dedup_key not in seen_opamp_units:
            seen_opamp_units.add(dedup_key)
            opamp_circuits.append(entry)

    return opamp_circuits


def detect_bridge_circuits(ctx: AnalysisContext) -> tuple[list[dict], set, dict]:
    """Detect gate driver / bridge topology.

    Returns (bridge_circuits, matched_fets, fet_pins).
    """
    bridge_circuits: list[dict] = []
    transistors = [c for c in ctx.components if c["type"] == "transistor"]

    # Build transistor pin map: ref -> {GATE: net, DRAIN: net, SOURCE: net}
    fet_pins = {}
    for t in transistors:
        ref = t["reference"]
        pins = {}
        for (pref, pnum), (net, _) in ctx.pin_net.items():
            if pref != ref:
                continue
            # Find pin name
            if net in ctx.nets:
                for p in ctx.nets[net]["pins"]:
                    if p["component"] == ref and p["pin_number"] == pnum:
                        pn = p.get("pin_name", "").upper()
                        pn_base = pn.rstrip("0123456789")  # G1→G, D2→D
                        if "GATE" in pn or pn_base == "G":
                            pins["gate"] = net
                        elif "DRAIN" in pn or pn_base == "D":
                            pins.setdefault("drain", net)
                        elif "SOURCE" in pn or pn_base == "S":
                            pins.setdefault("source", net)
                        break
        if "gate" in pins and "drain" in pins and "source" in pins:
            fet_pins[ref] = {**pins, "value": t["value"], "lib_id": t.get("lib_id", "")}

    # Find half-bridge pairs
    matched = set()
    half_bridges = []
    for hi_ref, hi in fet_pins.items():
        if hi_ref in matched:
            continue
        for lo_ref, lo in fet_pins.items():
            if lo_ref == hi_ref or lo_ref in matched:
                continue
            if hi["source"] == lo["drain"]:
                mid_net = hi["source"]
                if ctx.is_power_net(hi["drain"]) or ctx.is_ground(lo["source"]):
                    half_bridges.append({
                        "high_side": hi_ref,
                        "low_side": lo_ref,
                        "output_net": mid_net,
                        "power_net": hi["drain"],
                        "ground_net": lo["source"],
                        "high_gate": hi["gate"],
                        "low_gate": lo["gate"],
                    })
                    matched.add(hi_ref)
                    matched.add(lo_ref)
                    break

    if half_bridges:
        n = len(half_bridges)
        if n == 1:
            topology = "half_bridge"
        elif n == 2:
            topology = "h_bridge"
        elif n == 3:
            topology = "three_phase"
        else:
            topology = f"{n}_phase"

        gate_nets = set()
        for hb in half_bridges:
            gate_nets.add(hb["high_gate"])
            gate_nets.add(hb["low_gate"])
        driver_ics = set()
        for gn in gate_nets:
            if gn in ctx.nets:
                for p in ctx.nets[gn]["pins"]:
                    comp = ctx.comp_lookup.get(p["component"])
                    if comp and comp["type"] == "ic":
                        driver_ics.add(p["component"])

        bridge_circuits.append({
            "topology": topology,
            "half_bridges": half_bridges,
            "driver_ics": list(driver_ics),
            "driver_values": {ref: ctx.comp_lookup[ref]["value"] for ref in driver_ics if ref in ctx.comp_lookup},
            "fet_values": {hb["high_side"]: fet_pins[hb["high_side"]]["value"] for hb in half_bridges},
        })

    return bridge_circuits, matched, fet_pins


def detect_transistor_circuits(ctx: AnalysisContext, matched_fets: set, fet_pins: dict) -> list[dict]:
    """Detect transistor circuit configurations (MOSFETs and BJTs)."""
    transistor_circuits: list[dict] = []
    transistors = [c for c in ctx.components if c["type"] == "transistor"]

    # Build BJT pin map too (base/collector/emitter)
    bjt_pins = {}
    for t in transistors:
        ref = t["reference"]
        if ref in fet_pins:
            continue  # Already mapped as FET
        pins = {}
        for (pref, pnum), (net, _) in ctx.pin_net.items():
            if pref != ref:
                continue
            if net in ctx.nets:
                for p in ctx.nets[net]["pins"]:
                    if p["component"] == ref and p["pin_number"] == pnum:
                        pn = p.get("pin_name", "").upper()
                        if pn in ("B", "BASE"):
                            pins["base"] = net
                        elif pn in ("C", "COLLECTOR"):
                            pins["collector"] = net
                        elif pn in ("E", "EMITTER"):
                            pins["emitter"] = net
                        break
        if len(pins) >= 2:
            bjt_pins[ref] = {**pins, "value": t["value"], "lib_id": t.get("lib_id", "")}

    # Analyze each FET
    for ref, pins in fet_pins.items():
        if ref in matched_fets:
            continue  # Skip bridge FETs, handled above
        comp = ctx.comp_lookup.get(ref, {})
        gate_net = pins.get("gate")
        drain_net = pins.get("drain")
        source_net = pins.get("source")

        # Detect P-channel vs N-channel from lib_id, ki_keywords, and value
        lib_lower = comp.get("lib_id", "").lower()
        val_lower = comp.get("value", "").lower()
        kw_lower = comp.get("keywords", "").lower()
        is_pchannel = any(k in lib_lower for k in
                         ("pmos", "p-channel", "p_channel", "pchannel", "q_pmos"))
        if not is_pchannel:
            is_pchannel = "p-channel" in kw_lower or "pchannel" in kw_lower
        if not is_pchannel:
            is_pchannel = any(k in val_lower for k in
                             ("pmos", "p-channel", "p_channel", "pchannel", "dmp"))

        # Gate drive analysis
        gate_comps = _get_net_components(ctx, gate_net, ref) if gate_net else []
        gate_resistors = [c for c in gate_comps if c["type"] == "resistor"]
        gate_ics = [c for c in gate_comps if c["type"] == "ic"]

        if not gate_resistors and gate_net and gate_net in ctx.nets:
            gate_pin_count = len(ctx.nets[gate_net].get("pins", []))
            if gate_pin_count <= 3:
                for gc in gate_comps:
                    if gc["type"] == "resistor":
                        gate_resistors.append(gc)

        gate_pulldown = None
        for gr in gate_resistors:
            r_n1, r_n2 = ctx.get_two_pin_nets(gr["reference"])
            other_net = r_n2 if r_n1 == gate_net else r_n1
            if ctx.is_ground(other_net) or (is_pchannel and ctx.is_power_net(other_net)):
                gate_pulldown = {
                    "reference": gr["reference"],
                    "value": gr["value"],
                }
                break

        # Drain load analysis
        drain_comps = _get_net_components(ctx, drain_net, ref) if drain_net else []

        if is_pchannel and ctx.is_power_net(source_net):
            load_type = _classify_load(ctx, drain_net, ref) if drain_net else "unknown"
            if load_type == "other" and drain_net:
                load_type = "high_side_switch"
        else:
            load_type = _classify_load(ctx, drain_net, ref) if drain_net else "unknown"

        # Flyback diode check
        has_flyback = False
        flyback_ref = None
        for dc in drain_comps:
            if dc["type"] == "diode":
                d_n1, d_n2 = ctx.get_two_pin_nets(dc["reference"])
                if (d_n1 == source_net and d_n2 == drain_net) or \
                   (d_n1 == drain_net and d_n2 == source_net):
                    has_flyback = True
                    flyback_ref = dc["reference"]
                    break

        # Snubber check
        has_snubber = False
        for dc in drain_comps:
            if dc["type"] == "resistor":
                r_n1, r_n2 = ctx.get_two_pin_nets(dc["reference"])
                other = r_n2 if r_n1 == drain_net else r_n1
                if other and other != source_net and not ctx.is_power_net(other):
                    for sc in _get_net_components(ctx, other, dc["reference"]):
                        if sc["type"] == "capacitor":
                            c_n1, c_n2 = ctx.get_two_pin_nets(sc["reference"])
                            c_other = c_n2 if c_n1 == other else c_n1
                            if c_other == source_net:
                                has_snubber = True
                                break

        # Source sense resistor
        source_sense = None
        if source_net and not ctx.is_ground(source_net):
            source_comps = _get_net_components(ctx, source_net, ref)
            for sc in source_comps:
                if sc["type"] == "resistor":
                    r_n1, r_n2 = ctx.get_two_pin_nets(sc["reference"])
                    other = r_n2 if r_n1 == source_net else r_n1
                    if ctx.is_ground(other):
                        pv = parse_value(sc["value"])
                        if pv is not None and pv <= 1.0:
                            source_sense = {
                                "reference": sc["reference"],
                                "value": sc["value"],
                                "ohms": pv,
                            }
                            break

        circuit = {
            "reference": ref,
            "value": comp.get("value", ""),
            "lib_id": comp.get("lib_id", ""),
            "type": "mosfet",
            "is_pchannel": is_pchannel,
            "gate_net": gate_net,
            "drain_net": drain_net,
            "source_net": source_net,
            "drain_is_power": ctx.is_power_net(drain_net) or (is_pchannel and ctx.is_power_net(source_net)),
            "source_is_ground": ctx.is_ground(source_net),
            "source_is_power": ctx.is_power_net(source_net),
            "load_type": load_type,
            "gate_resistors": [{"reference": r["reference"], "value": r["value"]} for r in gate_resistors],
            "gate_driver_ics": [{"reference": ic["reference"], "value": ic["value"]} for ic in gate_ics],
            "gate_pulldown": gate_pulldown,
            "has_flyback_diode": has_flyback,
            "flyback_diode": flyback_ref,
            "has_snubber": has_snubber,
            "source_sense_resistor": source_sense,
        }
        transistor_circuits.append(circuit)

    # Analyze each BJT
    for ref, pins in bjt_pins.items():
        comp = ctx.comp_lookup.get(ref, {})
        base_net = pins.get("base")
        collector_net = pins.get("collector")
        emitter_net = pins.get("emitter")

        # Base drive analysis
        base_comps = _get_net_components(ctx, base_net, ref) if base_net else []
        base_resistors = [c for c in base_comps if c["type"] == "resistor"]
        base_ics = [c for c in base_comps if c["type"] == "ic"]
        base_pulldown = None
        for br in base_resistors:
            r_n1, r_n2 = ctx.get_two_pin_nets(br["reference"])
            other_net = r_n2 if r_n1 == base_net else r_n1
            if ctx.is_ground(other_net) or other_net == emitter_net:
                base_pulldown = {
                    "reference": br["reference"],
                    "value": br["value"],
                }
                break

        # Collector load
        load_type = _classify_load(ctx, collector_net, ref) if collector_net else "unknown"

        # Emitter resistor (degeneration)
        emitter_resistor = None
        if emitter_net and not ctx.is_ground(emitter_net):
            emitter_comps = _get_net_components(ctx, emitter_net, ref)
            for ec in emitter_comps:
                if ec["type"] == "resistor":
                    r_n1, r_n2 = ctx.get_two_pin_nets(ec["reference"])
                    other = r_n2 if r_n1 == emitter_net else r_n1
                    if ctx.is_ground(other):
                        emitter_resistor = {
                            "reference": ec["reference"],
                            "value": ec["value"],
                        }
                        break

        circuit = {
            "reference": ref,
            "value": comp.get("value", ""),
            "lib_id": comp.get("lib_id", ""),
            "type": "bjt",
            "base_net": base_net,
            "collector_net": collector_net,
            "emitter_net": emitter_net,
            "collector_is_power": ctx.is_power_net(collector_net),
            "emitter_is_ground": ctx.is_ground(emitter_net),
            "load_type": load_type,
            "base_resistors": [{"reference": r["reference"], "value": r["value"]} for r in base_resistors],
            "base_driver_ics": [{"reference": ic["reference"], "value": ic["value"]} for ic in base_ics],
            "base_pulldown": base_pulldown,
            "emitter_resistor": emitter_resistor,
        }
        transistor_circuits.append(circuit)

    return transistor_circuits


def postfilter_vd_and_dedup(voltage_dividers: list[dict], feedback_networks: list[dict],
                            transistor_circuits: list[dict]) -> tuple[list[dict], list[dict]]:
    """Post-filter: remove VDs on transistor gate/base nets and deduplicate."""
    # ---- Post-filter: remove voltage dividers on transistor gate/base nets ----
    _gate_base_nets = set()
    for tc in transistor_circuits:
        if tc["type"] == "mosfet" and tc.get("gate_net"):
            _gate_base_nets.add(tc["gate_net"])
        elif tc["type"] == "bjt" and tc.get("base_net"):
            _gate_base_nets.add(tc["base_net"])
    if _gate_base_nets:
        voltage_dividers = [
            vd for vd in voltage_dividers
            if vd["mid_net"] not in _gate_base_nets
        ]
        feedback_networks = [
            fn for fn in feedback_networks
            if fn["mid_net"] not in _gate_base_nets
        ]

    # ---- Post-filter: deduplicate voltage dividers by network topology ----
    _vd_groups: dict[tuple[str, str, str], list[dict]] = {}
    for vd in voltage_dividers:
        key = (vd["top_net"], vd["mid_net"], vd["bottom_net"])
        _vd_groups.setdefault(key, []).append(vd)
    deduped_vds: list[dict] = []
    for key, entries in _vd_groups.items():
        rep = entries[0]
        if len(entries) > 1:
            rep["parallel_count"] = len(entries)
        deduped_vds.append(rep)

    # Also deduplicate feedback_networks the same way
    _fn_groups: dict[tuple[str, str, str], list[dict]] = {}
    for fn in feedback_networks:
        key = (fn["top_net"], fn["mid_net"], fn["bottom_net"])
        _fn_groups.setdefault(key, []).append(fn)
    deduped_fns: list[dict] = []
    for key, entries in _fn_groups.items():
        rep = entries[0]
        if len(entries) > 1:
            rep["parallel_count"] = len(entries)
        deduped_fns.append(rep)

    return deduped_vds, deduped_fns


def detect_led_drivers(ctx: AnalysisContext, transistor_circuits: list[dict]) -> None:
    """Enrich transistor circuits with LED driver info. Modifies transistor_circuits in-place."""
    for tc in transistor_circuits:
        is_mosfet = tc.get("type") == "mosfet"
        is_bjt = tc.get("type") == "bjt"
        if not is_mosfet and not is_bjt:
            continue
        load_net = tc.get("drain_net") if is_mosfet else tc.get("collector_net")
        if not load_net:
            continue
        # Look at components on the load net for a resistor
        load_comps = _get_net_components(ctx, load_net, tc["reference"])
        for dc in load_comps:
            if dc["type"] != "resistor":
                continue
            # Follow the resistor to its other net
            r_n1, r_n2 = ctx.get_two_pin_nets(dc["reference"])
            other_net = r_n2 if r_n1 == load_net else r_n1
            if not other_net or other_net == load_net:
                continue
            # Check if an LED is on that net
            other_comps = _get_net_components(ctx, other_net, dc["reference"])
            for oc in other_comps:
                if oc["type"] == "led":
                    led_comp = ctx.comp_lookup.get(oc["reference"], {})
                    # Find what power rail the LED's other pin connects to
                    led_n1, led_n2 = ctx.get_two_pin_nets(oc["reference"])
                    led_other = led_n2 if led_n1 == other_net else led_n1
                    led_power = led_other if led_other and ctx.is_power_net(led_other) else None
                    tc["led_driver"] = {
                        "led_ref": oc["reference"],
                        "led_value": led_comp.get("value", ""),
                        "current_resistor": dc["reference"],
                        "current_resistor_value": dc.get("value", ""),
                        "power_rail": led_power,
                    }
                    ohms = ctx.parsed_values.get(dc["reference"])
                    if ohms and led_power:
                        tc["led_driver"]["resistor_ohms"] = ohms
                    break
            if "led_driver" in tc:
                break


def detect_buzzer_speakers(ctx: AnalysisContext, transistor_circuits: list[dict]) -> list[dict]:
    """Detect buzzer/speaker driver circuits."""
    buzzer_speaker_circuits: list[dict] = []
    # Build index: net → transistor circuits that drive it
    tc_by_output_net: dict[str, list[dict]] = {}
    for tc in transistor_circuits:
        for key in ("drain_net", "collector_net"):
            n = tc.get(key)
            if n:
                tc_by_output_net.setdefault(n, []).append(tc)
    buzzer_speaker_types = ("buzzer", "speaker")
    for comp in ctx.components:
        if comp["type"] not in buzzer_speaker_types:
            continue
        ref = comp["reference"]
        # Find signal nets via direct pin lookup (buzzers/speakers are 2-pin)
        n1, n2 = ctx.get_two_pin_nets(ref)
        signal_net = None
        for net in (n1, n2):
            if net and not ctx.is_ground(net) and not ctx.is_power_net(net):
                signal_net = net
                break
        if not signal_net:
            continue
        net_comps = _get_net_components(ctx, signal_net, ref)
        driver_ic_ref = None
        series_resistor = None
        has_transistor_driver = False
        for nc in net_comps:
            if nc["type"] == "ic":
                driver_ic_ref = nc["reference"]
            elif nc["type"] == "resistor":
                series_resistor = nc
                # Follow resistor to see if IC is on the other side
                r_n1, r_n2 = ctx.get_two_pin_nets(nc["reference"])
                r_other = r_n2 if r_n1 == signal_net else r_n1
                if r_other:
                    for rc in _get_net_components(ctx, r_other, nc["reference"]):
                        if rc["type"] == "ic":
                            driver_ic_ref = rc["reference"]
            elif nc["type"] == "transistor":
                has_transistor_driver = True
        # Check indexed transistor circuits for this net
        for tc in tc_by_output_net.get(signal_net, []):
            has_transistor_driver = True
            if not driver_ic_ref and tc.get("gate_driver_ics"):
                driver_ic_ref = tc["gate_driver_ics"][0].get("reference", "")
        entry = {
            "reference": ref,
            "value": comp.get("value", ""),
            "type": comp["type"],
            "signal_net": signal_net,
            "has_transistor_driver": has_transistor_driver,
        }
        if driver_ic_ref:
            entry["driver_ic"] = driver_ic_ref
        if series_resistor:
            entry["series_resistor"] = {
                "reference": series_resistor["reference"],
                "value": series_resistor.get("value", ""),
            }
        if not has_transistor_driver and driver_ic_ref:
            entry["direct_gpio_drive"] = True
        buzzer_speaker_circuits.append(entry)
    return buzzer_speaker_circuits


def detect_key_matrices(ctx: AnalysisContext) -> list[dict]:
    """Detect keyboard-style switch matrices."""
    key_matrices: list[dict] = []
    row_nets = {}
    col_nets = {}
    for net_name in ctx.nets:
        nn = net_name.upper().replace("_", "").replace("-", "")
        m_row = re.match(r'^ROW(\d+)$', nn)
        m_col = re.match(r'^COL(\d+)$', nn)
        if not m_row:
            m_row = re.match(r'^ROW(\d+)$', net_name.upper())
        if not m_col:
            m_col = re.match(r'^COL(?:UMN)?(\d+)$', net_name.upper())
        if m_row:
            row_nets[int(m_row.group(1))] = net_name
        elif m_col:
            col_nets[int(m_col.group(1))] = net_name

    if row_nets and col_nets:
        switch_count = 0
        diode_count = 0
        for net_name in list(row_nets.values()) + list(col_nets.values()):
            if net_name in ctx.nets:
                for p in ctx.nets[net_name]["pins"]:
                    comp = ctx.comp_lookup.get(p["component"])
                    if comp:
                        if comp["type"] == "switch":
                            switch_count += 1
                        elif comp["type"] == "diode":
                            diode_count += 1
        estimated_keys = max(switch_count, diode_count)
        if estimated_keys > 4:
            key_matrices.append({
                "rows": len(row_nets),
                "columns": len(col_nets),
                "row_nets": list(row_nets.values()),
                "col_nets": list(col_nets.values()),
                "estimated_keys": estimated_keys,
                "switches_on_matrix": switch_count,
                "diodes_on_matrix": diode_count,
            })
    return key_matrices


def detect_isolation_barriers(ctx: AnalysisContext) -> list[dict]:
    """Detect galvanic isolation domains."""
    isolation_barriers: list[dict] = []

    # Find ground domains (include PE/Earth for isolation detection)
    ground_nets = [n for n in ctx.nets if ctx.is_ground(n)
                   or n.upper() in ("PE", "EARTH", "CHASSIS", "SHIELD")]
    if len(ground_nets) >= 2:
        ground_domains = {}
        for gn in ground_nets:
            gnu = gn.upper()
            if gnu in ("PE", "EARTH", "CHASSIS", "SHIELD"):
                domain = gnu.lower()
            else:
                domain = gnu.replace("GND", "").replace("_", "").replace("-", "").strip()
                if not domain:
                    domain = "main"
            ground_domains.setdefault(domain, []).append(gn)

        if len(ground_domains) >= 2:
            iso_keywords = (
                "adum", "iso7", "iso15", "adm268", "adm248",
                "optocoupl", "opto_isolat", "pc817", "tlp",
                "isolated", "isol_dc", "traco", "recom", "murata",
                "dcdc_iso", "r1sx", "am1s", "tmu", "iec",
            )

            isolation_components = []
            for c in ctx.components:
                val = (c.get("value", "") + " " + c.get("lib_id", "")).lower()
                if any(k in val for k in iso_keywords) or c["type"] == "optocoupler":
                    isolation_components.append({
                        "reference": c["reference"],
                        "value": c["value"],
                        "type": c["type"],
                        "lib_id": c.get("lib_id", ""),
                    })

            ground_domain_map = {}
            for gn in ground_nets:
                domain = gn.upper().replace("GND", "").replace("_", "").replace("-", "").strip()
                if not domain:
                    domain = "main"
                ground_domain_map[gn] = domain

            isolated_power_rails = [
                n for n in ctx.nets
                if ctx.is_power_net(n) and any(
                    k in n.upper() for k in ("ISO", "ISOL", "_B", "_SEC")
                )
            ]

            has_iso_evidence = (
                isolation_components
                or isolated_power_rails
                or any("ISO" in d.upper() for d in ground_domains if d != "main")
            )
            if has_iso_evidence:
                isolation_barriers.append({
                    "ground_domains": {d: gnets for d, gnets in ground_domains.items()},
                    "isolation_components": isolation_components,
                    "isolated_power_rails": isolated_power_rails,
                })
    return isolation_barriers


def detect_ethernet_interfaces(ctx: AnalysisContext) -> list[dict]:
    """Detect Ethernet PHY + magnetics + connector chains."""
    ethernet_interfaces: list[dict] = []

    eth_phy_keywords = (
        "lan87", "lan91", "lan83", "dp838", "ksz8", "ksz9",
        "rtl81", "rtl83", "rtl88", "w5500", "w5100", "w5200",
        "enc28j60", "enc424", "dm9000", "ip101", "phy",
        "ethernet", "10base", "100base", "1000base",
    )
    magnetics_keywords = (
        "magnetics", "pulse", "transformer", "lan_tr", "rj45_mag",
        "hx1188", "hr601680", "g2406", "h5007",
    )

    eth_phys = []
    eth_magnetics = []
    eth_connectors = []
    seen_eth_refs = set()

    for c in ctx.components:
        if c["reference"] in seen_eth_refs:
            continue
        val_lib = (c.get("value", "") + " " + c.get("lib_id", "")).lower()
        if c["type"] == "ic" and any(k in val_lib for k in eth_phy_keywords):
            eth_phys.append(c)
            seen_eth_refs.add(c["reference"])
        elif c["type"] == "transformer" and any(k in val_lib for k in magnetics_keywords):
            eth_magnetics.append(c)
            seen_eth_refs.add(c["reference"])
        elif c["type"] == "connector":
            if any(k in val_lib for k in ("rj45", "8p8c", "ethernet", "magjack")):
                eth_connectors.append(c)
                seen_eth_refs.add(c["reference"])

    if eth_phys:
        for phy in eth_phys:
            ethernet_interfaces.append({
                "phy_reference": phy["reference"],
                "phy_value": phy["value"],
                "phy_lib_id": phy.get("lib_id", ""),
                "magnetics": [
                    {"reference": m["reference"], "value": m["value"]}
                    for m in eth_magnetics
                ],
                "connectors": [
                    {"reference": c["reference"], "value": c["value"]}
                    for c in eth_connectors
                ],
            })
    return ethernet_interfaces


def detect_memory_interfaces(ctx: AnalysisContext) -> list[dict]:
    """Detect memory ICs paired with MCUs/FPGAs."""
    memory_interfaces: list[dict] = []

    memory_keywords = (
        "sram", "dram", "ddr", "sdram", "psram", "flash", "eeprom",
        "w25q", "at25", "mx25", "is62", "is66", "cy62", "as4c",
        "mt41", "mt48", "k4b", "hy57", "is42", "25lc", "24lc",
        "at24", "fram", "fm25", "mb85", "s27k", "hyperram",
        "aps6404", "aps1604", "ly68",
    )
    processor_types = ("ic",)
    processor_keywords = (
        "stm32", "esp32", "rp2040", "atmega", "atsamd", "pic", "nrf5",
        "ice40", "ecp5", "artix", "spartan", "cyclone", "max10",
        "fpga", "mcu", "cortex", "risc",
    )

    memory_ics = []
    processor_ics = []
    seen_mem_refs = set()
    seen_proc_refs = set()
    for c in ctx.components:
        val_lib = (c.get("value", "") + " " + c.get("lib_id", "")).lower()
        if c["type"] == "ic":
            if any(k in val_lib for k in memory_keywords):
                if c["reference"] not in seen_mem_refs:
                    memory_ics.append(c)
                    seen_mem_refs.add(c["reference"])
            elif any(k in val_lib for k in processor_keywords):
                if c["reference"] not in seen_proc_refs:
                    processor_ics.append(c)
                    seen_proc_refs.add(c["reference"])

    for mem in memory_ics:
        mem_nets = set()
        for (pref, pnum), (net, _) in ctx.pin_net.items():
            if pref == mem["reference"]:
                mem_nets.add(net)

        connected_processors = []
        for proc in processor_ics:
            proc_nets = set()
            for (pref, pnum), (net, _) in ctx.pin_net.items():
                if pref == proc["reference"]:
                    proc_nets.add(net)
            shared = mem_nets & proc_nets
            signal_shared = [n for n in shared if not ctx.is_power_net(n) and not ctx.is_ground(n)]
            if signal_shared:
                connected_processors.append({
                    "reference": proc["reference"],
                    "value": proc["value"],
                    "shared_signal_nets": len(signal_shared),
                })

        if connected_processors:
            memory_interfaces.append({
                "memory_reference": mem["reference"],
                "memory_value": mem["value"],
                "memory_lib_id": mem.get("lib_id", ""),
                "connected_processors": connected_processors,
                "total_pins": len(mem_nets),
            })
    return memory_interfaces


def detect_rf_chains(ctx: AnalysisContext) -> list[dict]:
    """Detect RF signal chain components."""
    rf_chains: list[dict] = []

    rf_switch_keywords = (
        "sky134", "sky133", "sky131", "pe42", "as179", "as193",
        "hmc19", "hmc54", "hmc34", "bgrf", "rfsw", "spdt", "sp3t", "sp4t",
    )
    rf_mixer_keywords = (
        "rffc50", "ltc5549", "lt5560", "hmc21", "sa612", "ade-", "tuf-",
        "mixer",
    )
    rf_amp_keywords = (
        "mga-", "bga-", "maal", "pga-", "gali-", "maa-", "bfp7", "bfr5",
        "hmc58", "hmc31", "lna", "mmic",
    )
    rf_transceiver_keywords = (
        "max283", "at86rf", "cc1101", "cc2500", "sx127", "sx126",
        "rfm9", "rfm6", "nrf24", "si446",
    )
    rf_filter_keywords = (
        "saw", "baw", "fbar", "highpass", "lowpass", "bandpass",
        "fil-", "sf2", "ta0", "b39",
    )

    rf_switches = []
    rf_mixers = []
    rf_amplifiers = []
    rf_transceivers = []
    rf_filters = []
    rf_baluns = []
    seen_rf_refs = set()

    for c in ctx.components:
        if c["reference"] in seen_rf_refs:
            continue
        val_lib = (c.get("value", "") + " " + c.get("lib_id", "")).lower()

        if c["type"] == "ic":
            if any(k in val_lib for k in rf_switch_keywords):
                rf_switches.append(c)
                seen_rf_refs.add(c["reference"])
            elif any(k in val_lib for k in rf_mixer_keywords):
                rf_mixers.append(c)
                seen_rf_refs.add(c["reference"])
            elif any(k in val_lib for k in rf_amp_keywords):
                rf_amplifiers.append(c)
                seen_rf_refs.add(c["reference"])
            elif any(k in val_lib for k in rf_transceiver_keywords):
                rf_transceivers.append(c)
                seen_rf_refs.add(c["reference"])
            elif any(k in val_lib for k in rf_filter_keywords):
                rf_filters.append(c)
                seen_rf_refs.add(c["reference"])
        elif c["type"] == "transformer":
            if any(k in val_lib for k in ("balun", "bal-", "b0310", "bl14")):
                rf_baluns.append(c)
                seen_rf_refs.add(c["reference"])

    rf_component_count = (
        len(rf_switches) + len(rf_mixers) + len(rf_amplifiers)
        + len(rf_transceivers) + len(rf_filters) + len(rf_baluns)
    )

    if rf_component_count >= 2:
        all_rf_refs = seen_rf_refs.copy()
        rf_nets_map = {}
        for ref in all_rf_refs:
            ref_nets = set()
            for (pref, pnum), (net, _) in ctx.pin_net.items():
                if pref == ref and net and not ctx.is_power_net(net) and not ctx.is_ground(net):
                    ref_nets.add(net)
            rf_nets_map[ref] = ref_nets

        connections = []
        rf_ref_list = sorted(all_rf_refs)
        for i, ref_a in enumerate(rf_ref_list):
            for ref_b in rf_ref_list[i+1:]:
                shared = rf_nets_map.get(ref_a, set()) & rf_nets_map.get(ref_b, set())
                signal_shared = [n for n in shared if not n.startswith("__unnamed_")]
                if shared:
                    connections.append({
                        "from": ref_a,
                        "to": ref_b,
                        "shared_nets": len(shared),
                        "named_nets": signal_shared,
                    })

        def _rf_role(ref):
            comp = ctx.comp_lookup.get(ref)
            if not comp:
                return "unknown"
            val_lib = (comp.get("value", "") + " " + comp.get("lib_id", "")).lower()
            if any(k in val_lib for k in rf_switch_keywords):
                return "switch"
            if any(k in val_lib for k in rf_mixer_keywords):
                return "mixer"
            if any(k in val_lib for k in rf_amp_keywords):
                return "amplifier"
            if any(k in val_lib for k in rf_transceiver_keywords):
                return "transceiver"
            if any(k in val_lib for k in rf_filter_keywords):
                return "filter"
            if comp["type"] == "transformer":
                return "balun"
            return "unknown"

        rf_chains.append({
            "switches": [
                {"reference": c["reference"], "value": c["value"],
                 "lib_id": c.get("lib_id", "")}
                for c in rf_switches
            ],
            "mixers": [
                {"reference": c["reference"], "value": c["value"],
                 "lib_id": c.get("lib_id", "")}
                for c in rf_mixers
            ],
            "amplifiers": [
                {"reference": c["reference"], "value": c["value"],
                 "lib_id": c.get("lib_id", "")}
                for c in rf_amplifiers
            ],
            "transceivers": [
                {"reference": c["reference"], "value": c["value"],
                 "lib_id": c.get("lib_id", "")}
                for c in rf_transceivers
            ],
            "filters": [
                {"reference": c["reference"], "value": c["value"],
                 "lib_id": c.get("lib_id", "")}
                for c in rf_filters
            ],
            "baluns": [
                {"reference": c["reference"], "value": c["value"],
                 "lib_id": c.get("lib_id", "")}
                for c in rf_baluns
            ],
            "total_rf_components": rf_component_count,
            "connections": connections,
            "component_roles": {
                ref: _rf_role(ref) for ref in all_rf_refs
            },
        })
    return rf_chains


def detect_bms_systems(ctx: AnalysisContext) -> list[dict]:
    """Detect Battery Management System ICs with cell monitoring."""
    bms_systems: list[dict] = []

    bms_ic_keywords = (
        "bq769", "bq76920", "bq76930", "bq76940", "bq76952", "bq7694",
        "ltc681", "ltc682", "ltc683", "ltc680",
        "isl9420", "isl9421", "max1726", "max1730",
        "afe", "ip5189", "ip5306", "tp4056", "mp2639",
    )

    bms_ics = []
    seen_bms_refs = set()
    for c in ctx.components:
        if c["reference"] in seen_bms_refs:
            continue
        val_lib = (c.get("value", "") + " " + c.get("lib_id", "")).lower()
        if c["type"] == "ic" and any(k in val_lib for k in bms_ic_keywords):
            bms_ics.append(c)
            seen_bms_refs.add(c["reference"])

    for bms_ic in bms_ics:
        ref = bms_ic["reference"]

        cell_pins = []
        bms_nets = set()
        for (pref, pnum), (net, _) in ctx.pin_net.items():
            if pref == ref:
                bms_nets.add(net)
                if net:
                    nn = net.upper()
                    if re.match(r'^VC\d+$', nn) or re.match(r'^CELL\d+', nn):
                        cell_pins.append({"pin": pnum, "net": net})

        cell_numbers = set()
        for cp in cell_pins:
            m = re.match(r'^VC(\d+)$', cp["net"].upper())
            if m:
                cell_numbers.add(int(m.group(1)))
            m = re.match(r'^CELL(\d+)', cp["net"].upper())
            if m:
                cell_numbers.add(int(m.group(1)))

        balance_resistors = []
        cell_net_names = {cp["net"] for cp in cell_pins}
        for net_name in cell_net_names:
            if net_name not in ctx.nets:
                continue
            for p in ctx.nets[net_name]["pins"]:
                comp = ctx.comp_lookup.get(p["component"])
                if comp and comp["type"] == "resistor" and p["component"] != ref:
                    val = parse_value(comp.get("value", ""))
                    balance_resistors.append({
                        "reference": p["component"],
                        "value": comp["value"],
                        "cell_net": net_name,
                    })

        chg_dsg_fets = []
        seen_fet_refs = set()
        power_path_keywords = ("BAT+", "BAT-", "PACK+", "PACK-", "CHG+", "DSG+",
                               "BATT+", "BATT-", "VBAT+", "VBAT-")
        for net_name in ctx.nets:
            if net_name.upper() not in power_path_keywords:
                continue
            for p in ctx.nets[net_name]["pins"]:
                comp = ctx.comp_lookup.get(p["component"])
                if (comp and comp["type"] == "transistor"
                        and p["component"] not in seen_fet_refs):
                    chg_dsg_fets.append({
                        "reference": p["component"],
                        "value": comp["value"],
                        "power_net": net_name,
                    })
                    seen_fet_refs.add(p["component"])

        ntc_sensors = []
        for net_name in bms_nets:
            if not net_name or net_name not in ctx.nets:
                continue
            for p in ctx.nets[net_name]["pins"]:
                comp = ctx.comp_lookup.get(p["component"])
                if comp and comp["type"] == "thermistor":
                    ntc_sensors.append({
                        "reference": p["component"],
                        "value": comp["value"],
                        "net": net_name,
                    })

        seen_ntc = set()
        unique_ntcs = []
        for ntc in ntc_sensors:
            if ntc["reference"] not in seen_ntc:
                unique_ntcs.append(ntc)
                seen_ntc.add(ntc["reference"])

        cell_count = max(cell_numbers) if cell_numbers else 0

        bms_systems.append({
            "bms_reference": ref,
            "bms_value": bms_ic["value"],
            "bms_lib_id": bms_ic.get("lib_id", ""),
            "cell_voltage_pins": len(cell_pins),
            "cell_count": cell_count,
            "cell_nets": sorted(cell_net_names),
            "balance_resistors": len(balance_resistors),
            "charge_discharge_fets": chg_dsg_fets,
            "ntc_sensors": unique_ntcs,
        })
    return bms_systems


def detect_design_observations(ctx: AnalysisContext, results: dict) -> list[dict]:
    """Generate structured design observations for higher-level analysis."""
    design_observations: list[dict] = []

    # Build helper sets
    decoupled_rails = {d["rail"] for d in results.get("decoupling_analysis", [])}
    connector_nets = set()
    for net_name, net_info in ctx.nets.items():
        for p in net_info["pins"]:
            comp = ctx.comp_lookup.get(p["component"])
            if comp and comp["type"] in ("connector", "test_point"):
                connector_nets.add(net_name)
    protected_nets = {p["protected_net"] for p in results.get("protection_devices", [])}

    # 1. IC power pin decoupling status
    for ic in [c for c in ctx.components if c["type"] == "ic"]:
        ref = ic["reference"]
        ic_power_nets = set()
        for (pref, pnum), (net, _) in ctx.pin_net.items():
            if pref != ref:
                continue
            if net and ctx.is_power_net(net) and not ctx.is_ground(net):
                ic_power_nets.add(net)
        undecoupled = [r for r in ic_power_nets if r not in decoupled_rails]
        if undecoupled:
            design_observations.append({
                "category": "decoupling",
                "component": ref,
                "value": ic["value"],
                "rails_without_caps": undecoupled,
                "rails_with_caps": [r for r in ic_power_nets if r in decoupled_rails],
            })

    # 2. Regulator capacitor status
    for reg in results.get("power_regulators", []):
        in_rail = reg.get("input_rail")
        out_rail = reg.get("output_rail")
        missing = {}
        if in_rail and in_rail not in decoupled_rails:
            missing["input"] = in_rail
        if out_rail and out_rail not in decoupled_rails:
            missing["output"] = out_rail
        if missing:
            design_observations.append({
                "category": "regulator_caps",
                "component": reg["ref"],
                "value": reg["value"],
                "topology": reg.get("topology"),
                "missing_caps": missing,
            })

    # 3. Single-pin signal nets
    single_pin_nets = []
    for net_name, net_info in ctx.nets.items():
        if net_name.startswith("__unnamed_"):
            continue
        if ctx.is_power_net(net_name) or ctx.is_ground(net_name):
            continue
        if net_name in connector_nets:
            continue
        real_pins = [p for p in net_info["pins"] if not p["component"].startswith("#")]
        if len(real_pins) == 1:
            p = real_pins[0]
            comp = ctx.comp_lookup.get(p["component"])
            if comp and comp["type"] == "ic":
                pin_name = p.get("pin_name", p["pin_number"])
                pn_upper = pin_name.upper()
                if re.match(r'^P[A-K]\d', pn_upper) or re.match(r'^GPIO', pn_upper):
                    continue
                single_pin_nets.append({
                    "component": p["component"],
                    "pin": pin_name,
                    "net": net_name,
                })
    if single_pin_nets:
        design_observations.append({
            "category": "single_pin_nets",
            "count": len(single_pin_nets),
            "nets": single_pin_nets,
        })

    # 4. I2C bus pull-up status
    for net_name, net_info in ctx.nets.items():
        nn = net_name.upper()
        is_sda = bool(re.search(r'\bSDA\b', nn) or re.search(r'I2C.*SDA|SDA.*I2C', nn))
        is_scl = bool(re.search(r'\bSCL\b', nn) or re.search(r'I2C.*SCL|SCL.*I2C', nn))
        if "SCLK" in nn or "SCK" in nn:
            is_scl = False
        if not (is_sda or is_scl):
            continue
        line = "SDA" if is_sda else "SCL"
        has_pullup = False
        pullup_ref = None
        pullup_to = None
        for p in net_info["pins"]:
            comp = ctx.comp_lookup.get(p["component"])
            if comp and comp["type"] == "resistor":
                r_n1, r_n2 = ctx.get_two_pin_nets(p["component"])
                other = r_n2 if r_n1 == net_name else r_n1
                if other and ctx.is_power_net(other):
                    has_pullup = True
                    pullup_ref = p["component"]
                    pullup_to = other
                    break
        ic_refs = [p["component"] for p in net_info["pins"]
                   if ctx.comp_lookup.get(p["component"], {}).get("type") == "ic"]
        if ic_refs:
            design_observations.append({
                "category": "i2c_bus",
                "net": net_name,
                "line": line,
                "devices": ic_refs,
                "has_pullup": has_pullup,
                "pullup_resistor": pullup_ref,
                "pullup_rail": pullup_to,
            })

    # 5. Reset pin configuration
    for ic in [c for c in ctx.components if c["type"] == "ic"]:
        ref = ic["reference"]
        for (pref, pnum), (net, _) in ctx.pin_net.items():
            if pref != ref or not net or net.startswith("__unnamed_"):
                continue
            pin_name = ""
            if net in ctx.nets:
                for p in ctx.nets[net]["pins"]:
                    if p["component"] == ref and p["pin_number"] == pnum:
                        pin_name = p.get("pin_name", "").upper()
                        break
            if pin_name not in ("NRST", "~{RESET}", "RESET", "~{RST}", "RST", "~{NRST}", "MCLR", "~{MCLR}"):
                continue
            has_resistor = False
            has_capacitor = False
            connected_to = []
            if net in ctx.nets:
                for p in ctx.nets[net]["pins"]:
                    comp = ctx.comp_lookup.get(p["component"])
                    if not comp or p["component"] == ref:
                        continue
                    if comp["type"] == "resistor":
                        has_resistor = True
                    elif comp["type"] == "capacitor":
                        has_capacitor = True
                    connected_to.append({"ref": p["component"], "type": comp["type"]})
            design_observations.append({
                "category": "reset_pin",
                "component": ref,
                "value": ic["value"],
                "pin": pin_name,
                "net": net,
                "has_pullup": has_resistor,
                "has_filter_cap": has_capacitor,
                "connected_components": connected_to,
            })

    # 6. Regulator feedback voltage estimation
    for reg in results.get("power_regulators", []):
        if "estimated_vout" in reg:
            obs = {
                "category": "regulator_voltage",
                "component": reg["ref"],
                "value": reg["value"],
                "topology": reg.get("topology"),
                "estimated_vout": reg["estimated_vout"],
                "assumed_vref": reg.get("assumed_vref"),
                "vref_source": reg.get("vref_source", "heuristic"),
                "feedback_divider": reg.get("feedback_divider"),
                "input_rail": reg.get("input_rail"),
                "output_rail": reg.get("output_rail"),
            }
            out_rail = reg.get("output_rail", "")
            rail_v = _parse_voltage_from_net_name(out_rail)
            if rail_v is not None and reg["estimated_vout"] > 0:
                pct_diff = abs(reg["estimated_vout"] - rail_v) / rail_v
                if pct_diff > 0.15:
                    obs["vout_net_mismatch"] = {
                        "net_name": out_rail,
                        "net_voltage": rail_v,
                        "estimated_vout": reg["estimated_vout"],
                        "percent_diff": round(pct_diff * 100, 1),
                    }
            design_observations.append(obs)

    # 7. Switching regulator bootstrap status
    for reg in results.get("power_regulators", []):
        if reg.get("topology") == "switching" and reg.get("inductor"):
            design_observations.append({
                "category": "switching_regulator",
                "component": reg["ref"],
                "value": reg["value"],
                "inductor": reg.get("inductor"),
                "has_bootstrap": reg.get("has_bootstrap", False),
                "input_rail": reg.get("input_rail"),
                "output_rail": reg.get("output_rail"),
            })

    # 8. USB data line protection status
    for net_name in ctx.nets:
        nn = net_name.upper()
        is_usb = any(x in nn for x in ("USB_D", "USBDP", "USBDM", "USB_DP", "USB_DM"))
        if not is_usb and nn in ("D+", "D-", "DP", "DM"):
            if net_name in ctx.nets:
                for p in ctx.nets[net_name]["pins"]:
                    comp = ctx.comp_lookup.get(p["component"])
                    if comp:
                        cv = (comp.get("value", "") + " " + comp.get("lib_id", "")).upper()
                        if "USB" in cv:
                            is_usb = True
                            break
        if is_usb:
            design_observations.append({
                "category": "usb_data",
                "net": net_name,
                "has_esd_protection": net_name in protected_nets,
                "devices": [p["component"] for p in ctx.nets[net_name]["pins"]
                           if not ctx.comp_lookup.get(p["component"], {}).get("type") in (None,)],
            })

    # 9. Crystal load capacitance
    for xtal in results.get("crystal_circuits", []):
        if "effective_load_pF" in xtal:
            design_observations.append({
                "category": "crystal",
                "component": xtal["reference"],
                "value": xtal.get("value"),
                "effective_load_pF": xtal["effective_load_pF"],
                "load_caps": xtal.get("load_caps", []),
                "in_typical_range": 4 <= xtal["effective_load_pF"] <= 30,
            })

    # 10. Decoupling frequency coverage per rail
    for decoup in results.get("decoupling_analysis", []):
        caps = decoup.get("capacitors", [])
        farads_list = [c.get("farads", 0) for c in caps]
        has_bulk = any(f >= 1e-6 for f in farads_list)
        has_bypass = any(10e-9 <= f <= 1e-6 for f in farads_list)
        has_hf = any(f < 10e-9 for f in farads_list)
        design_observations.append({
            "category": "decoupling_coverage",
            "rail": decoup["rail"],
            "cap_count": len(caps),
            "total_uF": decoup.get("total_capacitance_uF"),
            "has_bulk": has_bulk,
            "has_bypass": has_bypass,
            "has_high_freq": has_hf,
        })

    return design_observations
