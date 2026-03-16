#!/usr/bin/env python3
"""
KiCad Schematic Analyzer — comprehensive single-pass extraction.

Parses a .kicad_sch file and outputs structured JSON with:
- Component inventory (BOM data, properties, positions)
- Net connectivity (wires, labels, junctions, no-connects, power symbols)
- Pin-level connectivity map (which pin connects to which net)
- Subcircuit identification hints
- Design statistics

Usage:
    python analyze_schematic.py <file.kicad_sch> [--output file.json]

Output is JSON to stdout (or file if --output specified).
"""

import json
import math
import re
import sys
from pathlib import Path

# Add scripts dir to path for sexp_parser and kicad_utils
sys.path.insert(0, str(Path(__file__).parent))
from sexp_parser import (
    find_all,
    find_deep,
    find_first,
    get_at,
    get_property,
    get_value,
    parse_file,
)
from kicad_utils import (
    COORD_EPSILON,
    _LOAD_TYPE_KEYWORDS,
    _MIL_MM,
    _OUTPUT_DRIVE_KEYWORDS,
    _REGULATOR_VREF,
    classify_component,
    format_frequency as _format_frequency,
    get_two_pin_nets as _get_two_pin_nets_standalone,
    is_ground_name as _is_ground_name,
    is_power_net_name as _is_power_net_name,
    lookup_regulator_vref as _lookup_regulator_vref,
    parse_value,
    parse_voltage_from_net_name as _parse_voltage_from_net_name,
    snap_to_mil_grid as _snap_mil,
)
from kicad_types import AnalysisContext





def extract_lib_symbols(root: list) -> dict:
    """Extract library symbol definitions (pin positions, types).

    For multi-unit symbols (e.g., STM32 with separate GPIO and power units),
    pins are stored per unit so compute_pin_positions can use the correct
    offsets for each placed unit instance.
    """
    lib_symbols_node = find_first(root, "lib_symbols")
    if not lib_symbols_node:
        return {}

    def _parse_single_pin(pin):
        """Parse a single pin S-expression node into a dict."""
        pin_type = pin[1] if len(pin) > 1 else "unknown"
        pin_shape = pin[2] if len(pin) > 2 else "unknown"
        at = get_at(pin)
        pin_name_node = find_first(pin, "name")
        pin_num_node = find_first(pin, "number")
        pin_name = str(pin_name_node[1]) if pin_name_node and len(pin_name_node) > 1 and pin_name_node[1] is not None else ""
        pin_num = str(pin_num_node[1]) if pin_num_node and len(pin_num_node) > 1 and pin_num_node[1] is not None else ""
        return {
            "number": pin_num, "name": pin_name,
            "type": pin_type, "shape": pin_shape,
            "offset": list(at) if at else None,
        }

    def _extract_pins_from_node(node):
        """Extract pin definitions directly from a symbol node (not recursing into sub-symbols)."""
        return [_parse_single_pin(child) for child in node
                if isinstance(child, list) and len(child) >= 3 and child[0] == "pin"]

    symbols = {}
    for sym in find_all(lib_symbols_node, "symbol"):
        name = sym[1] if len(sym) > 1 else "unknown"
        # Skip sub-unit symbols (e.g., "Device:C_0_1", "Device:C_1_1")
        # Real sub-units have _U_V suffix where BOTH U and V are digits.
        parts = name.split(":")[-1].rsplit("_", 2)
        if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
            continue

        # Collect pins from sub-unit symbols, keyed by unit number.
        # Sub-symbols named like "SymName_U_V" where U is the unit number.
        # Unit 0 sub-symbols (_0_1) contain pins/graphics shared by all units.
        unit_pins: dict[int, list] = {}
        all_pins = []

        for child in sym:
            if not isinstance(child, list) or len(child) < 2 or child[0] != "symbol":
                continue
            sub_name = child[1] if isinstance(child[1], str) else ""
            # Parse unit number from sub-symbol name: "Name_U_V"
            parts = sub_name.rsplit("_", 2)
            if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
                unit_num = int(parts[-2])
                sub_pins = _extract_pins_from_node(child)
                if sub_pins:
                    unit_pins.setdefault(unit_num, []).extend(sub_pins)
                    all_pins.extend(sub_pins)

        # If no sub-unit pins found, fall back to find_deep on the whole symbol
        if not all_pins:
            all_pins = [_parse_single_pin(pin) for pin in find_deep(sym, "pin") if len(pin) >= 3]

        # Get symbol properties
        desc = get_property(sym, "Description") or ""
        ki_keywords = get_property(sym, "ki_keywords") or ""
        ki_fp_filters = get_property(sym, "ki_fp_filters") or ""
        lib_value = get_property(sym, "Value") or ""

        # Check for (power) flag — marks this as a power symbol regardless of lib name
        is_power = any(
            isinstance(child, list) and len(child) == 1 and child[0] == "power"
            for child in sym
        )

        # Extract alternate pin definitions (dual-function pins, e.g., GPIO/SPI/UART)
        alternates = {}
        for pin in find_deep(sym, "pin"):
            pin_num_node = find_first(pin, "number")
            pin_num = pin_num_node[1] if pin_num_node and len(pin_num_node) > 1 else ""
            alts = []
            for child in pin:
                if isinstance(child, list) and len(child) >= 2 and child[0] == "alternate":
                    alt_name = child[1] if len(child) > 1 else ""
                    alt_type = child[2] if len(child) > 2 else ""
                    alt_shape = child[3] if len(child) > 3 else ""
                    alts.append({"name": alt_name, "type": alt_type, "shape": alt_shape})
            if alts:
                alternates[pin_num] = alts

        symbols[name] = {
            "pins": all_pins,
            "unit_pins": unit_pins if unit_pins else None,
            "value": lib_value,
            "description": desc,
            "keywords": ki_keywords,
            "is_power": is_power,
            "ki_fp_filters": ki_fp_filters,
            "alternates": alternates if alternates else None,
        }

    return symbols


def apply_rotation(px: float, py: float, angle_deg: float) -> tuple[float, float]:
    """Apply rotation to a pin offset. KiCad uses degrees, CCW positive."""
    if angle_deg == 0:
        return px, py
    rad = math.radians(angle_deg)
    cos_a = round(math.cos(rad), 10)
    sin_a = round(math.sin(rad), 10)
    return (px * cos_a - py * sin_a, px * sin_a + py * cos_a)


def compute_pin_positions(component: dict, lib_symbols: dict) -> list[dict]:
    """Compute absolute pin positions for a placed component."""
    lib_id = component.get("lib_id", "")
    lib_name = component.get("lib_name", "")
    # KH-083: Try lib_name first (KiCad 7+ local name), then lib_id
    sym_def = (lib_symbols.get(lib_name) if lib_name else None) or lib_symbols.get(lib_id)
    if not sym_def:
        return []

    cx = component["x"]
    cy = component["y"]
    angle = component["angle"]
    mirror_x = component.get("mirror_x", False)
    mirror_y = component.get("mirror_y", False)

    # For multi-unit symbols, use pins from this unit PLUS unit 0 (shared pins).
    # In KiCad, sub-symbol _0_1 contains pins shared by all units (e.g., power pins).
    unit_num = component.get("unit")
    unit_pins_map = sym_def.get("unit_pins")
    if unit_num and unit_pins_map and unit_num in unit_pins_map:
        pins = list(unit_pins_map[unit_num])
        # Also include unit 0 (shared/common) pins if they exist
        if 0 in unit_pins_map:
            pins.extend(unit_pins_map[0])
    else:
        pins = sym_def["pins"]

    pin_positions = []
    for pin in pins:
        if not pin["offset"]:
            continue
        px, py = pin["offset"][0], pin["offset"][1]

        # Apply mirroring before rotation
        if mirror_x:
            py = -py
        if mirror_y:
            px = -px

        # Apply rotation to pin offset
        rpx, rpy = apply_rotation(px, py, angle)

        # Absolute position: Y-axis inversion (symbol coords are math-up, schematic is screen-down)
        abs_x = round(cx + rpx, 4)
        abs_y = round(cy - rpy, 4)

        # Ensure pin number and name are never None — coerce to string with fallback
        pin_num = pin.get("number")
        pin_name = pin.get("name")
        if pin_num is None:
            pin_num = ""
        else:
            pin_num = str(pin_num)
        if pin_name is None:
            pin_name = ""
        else:
            pin_name = str(pin_name)

        pin_positions.append({
            "number": pin_num,
            "name": pin_name,
            "type": pin["type"],
            "x": abs_x,
            "y": abs_y,
        })

    return pin_positions


def extract_symbol_instances(root: list) -> dict:
    """Extract (symbol_instances ...) from root schematic.

    Returns a dict mapping path string -> {reference, unit, value, footprint}.
    Path format: "/sheet_uuid/symbol_uuid" or "/sheet_uuid/child_uuid/symbol_uuid".
    """
    result = {}
    si_node = find_first(root, "symbol_instances")
    if not si_node:
        return result
    for path_node in si_node[1:]:
        if not isinstance(path_node, list) or path_node[0] != "path":
            continue
        if len(path_node) < 2:
            continue
        path_str = path_node[1]
        ref = get_value(path_node, "reference") or ""
        unit_val = get_value(path_node, "unit")
        try:
            unit = int(unit_val) if unit_val else 1
        except (ValueError, TypeError):
            unit = 1
        result[path_str] = {
            "reference": ref,
            "unit": unit,
        }
    return result


def extract_components(root: list, lib_symbols: dict, instance_uuid: str = "",
                       symbol_instances: dict | None = None) -> list[dict]:
    """Extract all placed component instances.

    If instance_uuid is provided, remap references from the (instances) block
    for the specified sheet instance (supports multi-instance hierarchical sheets).

    If symbol_instances is provided (from root schematic), use it as a fallback
    when a symbol has no inline (instances) block (common in older KiCad projects).
    """
    components = []

    # Placed symbols are direct children of root with (symbol (lib_id ...))
    for sym in root:
        if not isinstance(sym, list) or len(sym) == 0 or sym[0] != "symbol":
            continue
        # Skip if this is in lib_symbols (those have string name as [1], not a sub-list)
        if len(sym) > 1 and isinstance(sym[1], str):
            continue

        lib_id = get_value(sym, "lib_id")
        if not lib_id:
            continue
        # KH-083: KiCad 7+ uses (lib_name X) when the local symbol name
        # differs from the library's lib_id (e.g., after Eagle import)
        lib_name = get_value(sym, "lib_name") or ""

        at = get_at(sym)
        x, y, angle = at if at else (0, 0, 0)

        # Check for mirror
        mirror_node = find_first(sym, "mirror")
        mirror_x = "x" in mirror_node if mirror_node else False
        mirror_y = "y" in mirror_node if mirror_node else False

        # Extract unit number for multi-unit symbols
        unit_node = find_first(sym, "unit")
        unit_num = int(unit_node[1]) if unit_node and len(unit_node) > 1 else None

        ref = get_property(sym, "Reference") or ""
        value = get_property(sym, "Value") or ""
        # KH-088: Eagle-imported schematics often have empty instance Value.
        # Fall back to the lib_symbol's Value property.
        if not value:
            sym_def_val = lib_symbols.get(lib_id, {})
            value = sym_def_val.get("value", "")
        footprint = get_property(sym, "Footprint") or ""
        datasheet = get_property(sym, "Datasheet") or ""
        description = get_property(sym, "Description") or ""
        uuid_val = get_value(sym, "uuid") or ""

        # Remap reference from (instances) block for multi-instance sheets.
        # Two sources: inline (instances) in each symbol (KiCad 7+), or
        # centralized (symbol_instances) in the root schematic (KiCad 6/older projects).
        if instance_uuid:
            remapped = False
            instances_node = find_first(sym, "instances")
            if instances_node:
                for proj in instances_node[1:]:
                    if not isinstance(proj, list) or proj[0] != "project":
                        continue
                    for path_node in proj[2:]:
                        if not isinstance(path_node, list) or path_node[0] != "path":
                            continue
                        path_str = path_node[1] if len(path_node) > 1 else ""
                        if instance_uuid in path_str:
                            inst_ref = get_value(path_node, "reference")
                            if inst_ref:
                                ref = inst_ref
                                remapped = True
                            inst_unit = get_value(path_node, "unit")
                            if inst_unit:
                                try:
                                    unit_num = int(inst_unit)
                                except (ValueError, TypeError):
                                    pass
                            break

            # Fallback: use centralized symbol_instances from root schematic
            if not remapped and symbol_instances and uuid_val:
                # Build the lookup path: instance_uuid is a full hierarchical
                # path like "/sheet1_uuid" or "/sheet1_uuid/sheet2_uuid".
                # Append the symbol's own UUID to form the full path.
                lookup_path = instance_uuid + "/" + uuid_val
                si_entry = symbol_instances.get(lookup_path)
                if si_entry:
                    if si_entry["reference"]:
                        ref = si_entry["reference"]
                    if si_entry.get("unit"):
                        unit_num = si_entry["unit"]
        mpn = (get_property(sym, "MPN") or get_property(sym, "Mfg Part")
               or get_property(sym, "PartNumber") or get_property(sym, "Part Number")
               or get_property(sym, "Part#")
               or get_property(sym, "Manufacturer_Part_Number") or get_property(sym, "Mfr No.")
               or get_property(sym, "Mfr_No") or get_property(sym, "ManufacturerPartNumber")
               or get_property(sym, "mpn")
               or get_property(sym, "PARTNO") or get_property(sym, "PartNo")
               or get_property(sym, "Partno") or "")
        manufacturer = (get_property(sym, "Manufacturer") or get_property(sym, "Mfr")
                        or get_property(sym, "MFR") or "")
        digikey = (get_property(sym, "Digi-Key Part Number") or get_property(sym, "Digi-Key_PN")
                   or get_property(sym, "DigiKey") or get_property(sym, "Digikey")
                   or get_property(sym, "DigiKey Part") or get_property(sym, "Digikey Part")
                   or get_property(sym, "Digikey Part Number") or get_property(sym, "Digi-Key PN")
                   or get_property(sym, "DigiKey_Part_Number") or get_property(sym, "DigiKey Part Number")
                   or get_property(sym, "DIGIKEY") or get_property(sym, "DK") or "")
        mouser = (get_property(sym, "Mouser") or get_property(sym, "Mouser Part Number")
                  or get_property(sym, "Mouser Part") or get_property(sym, "Mouser_PN")
                  or get_property(sym, "Mouser PN") or "")
        lcsc = (get_property(sym, "LCSC") or get_property(sym, "LCSC Part #")
                or get_property(sym, "LCSC Part Number") or get_property(sym, "LCSC Part")
                or get_property(sym, "LCSCStockCode") or get_property(sym, "JLCPCB")
                or get_property(sym, "JLCPCB Part") or get_property(sym, "JLC") or "")
        element14 = (get_property(sym, "Newark") or get_property(sym, "Newark Part Number")
                     or get_property(sym, "Newark_PN") or get_property(sym, "Newark PN")
                     or get_property(sym, "Farnell") or get_property(sym, "Farnell Part Number")
                     or get_property(sym, "Farnell_PN") or get_property(sym, "Farnell PN")
                     or get_property(sym, "element14") or get_property(sym, "element14 Part Number")
                     or get_property(sym, "element14_PN") or "")

        in_bom = get_value(sym, "in_bom") != "no"
        dnp = get_value(sym, "dnp") == "yes"
        if not dnp and value.upper() in ("DNP", "DO NOT POPULATE", "DO NOT PLACE", "NP"):
            dnp = True
        on_board = get_value(sym, "on_board") != "no"

        # Get pin UUIDs for connectivity
        pin_uuids = {}
        for pin_node in find_all(sym, "pin"):
            if len(pin_node) >= 2:
                pin_num = pin_node[1]
                pin_uuid_node = find_first(pin_node, "uuid")
                if pin_uuid_node and len(pin_uuid_node) > 1:
                    pin_uuids[pin_num] = pin_uuid_node[1]

        comp = {
            "reference": ref,
            "value": value,
            "lib_id": lib_id,
            "lib_name": lib_name,
            "footprint": footprint,
            "datasheet": datasheet,
            "description": description,
            "mpn": mpn,
            "manufacturer": manufacturer,
            "digikey": digikey,
            "mouser": mouser,
            "lcsc": lcsc,
            "element14": element14,
            "x": x,
            "y": y,
            "angle": angle,
            "mirror_x": mirror_x,
            "mirror_y": mirror_y,
            "unit": unit_num,
            "uuid": uuid_val,
            "in_bom": in_bom,
            "dnp": dnp,
            "on_board": on_board,
            "pin_uuids": pin_uuids,
        }

        # Determine component type from reference prefix, lib_id, and lib_symbol flags
        # KH-083: Try lib_name first for correct lib_symbol lookup
        sym_def = (lib_symbols.get(lib_name) if lib_name else None) or lib_symbols.get(lib_id, {})
        is_power_sym = sym_def.get("is_power", False)
        comp["type"] = classify_component(ref, lib_id, value, is_power_sym, footprint, in_bom=in_bom)
        # Store ki_keywords for downstream analysis (e.g., P-channel detection)
        comp["keywords"] = sym_def.get("keywords", "")

        # Compute absolute pin positions
        comp["pins"] = compute_pin_positions(comp, lib_symbols)

        components.append(comp)

    return components


def analyze_signal_paths(components: list[dict], nets: dict, lib_symbols: dict | None = None, pin_net: dict | None = None) -> dict:
    """Analyze signal processing circuits: filters, dividers, feedback networks.

    Orchestrator that calls individual detector functions from signal_detectors.py.
    Each detector takes an AnalysisContext and returns its detection results.
    """
    from signal_detectors import (
        detect_addressable_leds,
        detect_bms_systems,
        detect_bridge_circuits,
        detect_buzzer_speakers,
        detect_crystal_circuits,
        detect_current_sense,
        detect_decoupling,
        detect_design_observations,
        detect_ethernet_interfaces,
        detect_hdmi_dvi_interfaces,
        detect_integrated_ldos,
        detect_isolation_barriers,
        detect_key_matrices,
        detect_lc_filters,
        detect_led_drivers,
        detect_memory_interfaces,
        detect_opamp_circuits,
        detect_power_regulators,
        detect_protection_devices,
        detect_rc_filters,
        detect_rf_chains,
        detect_rf_matching,
        detect_transistor_circuits,
        detect_voltage_dividers,
        postfilter_vd_and_dedup,
        _merge_series_dividers,
    )

    if pin_net is None:
        pin_net = build_pin_to_net_map(nets)
    if lib_symbols is None:
        lib_symbols = {}

    ctx = AnalysisContext(
        components=components,
        nets=nets,
        lib_symbols=lib_symbols,
        pin_net=pin_net,
    )

    # Run detectors in dependency order
    vd_result = detect_voltage_dividers(ctx)
    voltage_dividers = vd_result["voltage_dividers"]
    # KH-105/KH-115: Merge series resistors in divider chains
    voltage_dividers = _merge_series_dividers(voltage_dividers, ctx)
    feedback_networks = vd_result["feedback_networks"]

    lc_filters = detect_lc_filters(ctx)
    crystal_circuits = detect_crystal_circuits(ctx)
    # KH-107: Pass crystal_circuits to exclude crystal R/C from RC filter detection
    rc_filters = detect_rc_filters(ctx, voltage_dividers, crystal_circuits)
    decoupling_analysis = detect_decoupling(ctx)
    current_sense = detect_current_sense(ctx)
    power_regulators = detect_power_regulators(ctx, voltage_dividers)
    integrated_ldos = detect_integrated_ldos(ctx, power_regulators)
    power_regulators.extend(integrated_ldos)
    protection_devices = detect_protection_devices(ctx)
    opamp_circuits = detect_opamp_circuits(ctx)
    bridge_circuits, matched_fets, fet_pins = detect_bridge_circuits(ctx)
    transistor_circuits = detect_transistor_circuits(ctx, matched_fets, fet_pins)

    # Post-processing that needs cross-detector data
    voltage_dividers, feedback_networks = postfilter_vd_and_dedup(
        voltage_dividers, feedback_networks, transistor_circuits, nets=ctx.nets)
    detect_led_drivers(ctx, transistor_circuits)
    buzzer_speaker_circuits = detect_buzzer_speakers(ctx, transistor_circuits)
    key_matrices = detect_key_matrices(ctx)
    isolation_barriers = detect_isolation_barriers(ctx)
    ethernet_interfaces = detect_ethernet_interfaces(ctx)
    hdmi_dvi_interfaces = detect_hdmi_dvi_interfaces(ctx)
    memory_interfaces = detect_memory_interfaces(ctx)
    rf_chains = detect_rf_chains(ctx)
    rf_matching = detect_rf_matching(ctx)
    bms_systems = detect_bms_systems(ctx)
    addressable_led_chains = detect_addressable_leds(ctx)

    # Remove R/C components that appear in crystal circuits from RC filter
    # results — prevents misclassifying crystal feedback resistors + load caps
    # as RC filters (e.g., "10M + 22pF = 723Hz RC filter").
    if crystal_circuits and rc_filters:
        xtal_refs = set()
        for xc in crystal_circuits:
            for lc in xc.get("load_caps", []):
                xtal_refs.add(lc["ref"])
            if "feedback_resistor" in xc:
                xtal_refs.add(xc["feedback_resistor"])
        if xtal_refs:
            rc_filters = [
                f for f in rc_filters
                if f.get("resistor", {}).get("reference") not in xtal_refs
                and f.get("capacitor", {}).get("reference") not in xtal_refs
            ]

    results = {
        "voltage_dividers": voltage_dividers,
        "rc_filters": rc_filters,
        "lc_filters": lc_filters,
        "feedback_networks": feedback_networks,
        "crystal_circuits": crystal_circuits,
        "snubbers": [],
        "decoupling_analysis": decoupling_analysis,
        "current_sense": current_sense,
        "power_regulators": power_regulators,
        "protection_devices": protection_devices,
        "opamp_circuits": opamp_circuits,
        "bridge_circuits": bridge_circuits,
        "transistor_circuits": transistor_circuits,
        "buzzer_speaker_circuits": buzzer_speaker_circuits,
        "key_matrices": key_matrices,
        "isolation_barriers": isolation_barriers,
        "ethernet_interfaces": ethernet_interfaces,
        "hdmi_dvi_interfaces": hdmi_dvi_interfaces,
        "memory_interfaces": memory_interfaces,
        "rf_chains": rf_chains,
        "rf_matching": rf_matching,
        "bms_systems": bms_systems,
        "addressable_led_chains": addressable_led_chains,
    }

    results["design_observations"] = detect_design_observations(ctx, results)

    return results


def extract_wires(root: list) -> list[dict]:
    """Extract all wire segments."""
    wires = []
    for wire in find_all(root, "wire"):
        pts = find_first(wire, "pts")
        if not pts:
            continue
        xys = find_all(pts, "xy")
        if len(xys) >= 2:
            wires.append({
                "x1": float(xys[0][1]), "y1": float(xys[0][2]),
                "x2": float(xys[1][1]), "y2": float(xys[1][2]),
            })
    return wires


def extract_labels(root: list) -> list[dict]:
    """Extract all labels (local, global, hierarchical)."""
    labels = []

    for label_type in ["label", "global_label", "hierarchical_label"]:
        for lbl in find_all(root, label_type):
            name = lbl[1] if len(lbl) > 1 else ""
            # KH-078: Malformed s-expressions can yield a list instead of string
            if isinstance(name, list):
                name = str(name[0]) if name else ""
            at = get_at(lbl)
            x, y, angle = at if at else (0, 0, 0)
            # Shape field exists on global_label and hierarchical_label
            # Values: input, output, bidirectional, tri_state, passive
            shape = get_value(lbl, "shape") or ""
            entry = {
                "name": name,
                "type": label_type,
                "x": round(x, 4),
                "y": round(y, 4),
                "angle": angle,
            }
            if shape:
                entry["shape"] = shape
            labels.append(entry)

    return labels


def extract_power_symbols(components: list[dict]) -> list[dict]:
    """Extract power symbols from the component list (they define net names).

    Uses the computed pin positions (not the symbol placement point) so that
    power symbols connect to the correct wire endpoints in the net map.
    """
    power = []
    for comp in components:
        if comp["type"] == "power_symbol":
            # Use pin position if available (more accurate), fall back to symbol position
            pins = comp.get("pins", [])
            if pins:
                # Power symbols typically have one pin — use its position
                px, py = pins[0]["x"], pins[0]["y"]
            else:
                px, py = comp["x"], comp["y"]
            power.append({
                "net_name": comp["value"],
                "x": px,
                "y": py,
                "lib_id": comp["lib_id"],
                "_sheet": comp.get("_sheet", 0),
            })
    return power


def extract_junctions(root: list) -> list[dict]:
    """Extract junction points."""
    junctions = []
    for junc in find_all(root, "junction"):
        at = get_at(junc)
        if at:
            junctions.append({"x": round(at[0], 4), "y": round(at[1], 4)})
    return junctions


def extract_no_connects(root: list) -> list[dict]:
    """Extract no-connect markers."""
    ncs = []
    for nc in find_all(root, "no_connect"):
        at = get_at(nc)
        if at:
            ncs.append({"x": round(at[0], 4), "y": round(at[1], 4)})
    return ncs


def extract_text_annotations(root: list) -> list[dict]:
    """Extract text annotations (non-electrical notes placed on the schematic).

    These are designer notes, TODO comments, revision annotations, etc. placed
    on the schematic sheet as free text objects.  Includes both ``(text ...)``
    and ``(text_box ...)`` elements.
    """
    texts = []
    for txt in find_all(root, "text"):
        content = txt[1] if len(txt) > 1 and isinstance(txt[1], str) else ""
        if not content:
            continue
        at = get_at(txt)
        x, y, angle = at if at else (0, 0, 0)
        texts.append({
            "type": "text",
            "text": content,
            "x": round(x, 4),
            "y": round(y, 4),
            "angle": angle,
        })

    for tb in find_all(root, "text_box"):
        content = tb[1] if len(tb) > 1 and isinstance(tb[1], str) else ""
        if not content:
            continue
        at = get_at(tb)
        x, y, angle = at if at else (0, 0, 0)
        size_node = find_first(tb, "size")
        w = float(size_node[1]) if size_node and len(size_node) > 1 else 0
        h = float(size_node[2]) if size_node and len(size_node) > 2 else 0
        entry: dict = {
            "type": "text_box",
            "text": content,
            "x": round(x, 4),
            "y": round(y, 4),
            "angle": angle,
        }
        if w or h:
            entry["width"] = round(w, 4)
            entry["height"] = round(h, 4)
        texts.append(entry)

    return texts


def extract_bus_elements(root: list) -> dict:
    """Extract bus wires, bus entries, and bus aliases.

    Buses in KiCad group related signals (e.g., D[0..7]) into a single
    graphical wire. Bus entries connect individual signals to/from the bus.
    Bus aliases define named groups of signals.
    """
    buses = []
    for bus in find_all(root, "bus"):
        pts = find_first(bus, "pts")
        if pts:
            xys = find_all(pts, "xy")
            if len(xys) >= 2:
                buses.append({
                    "x1": float(xys[0][1]), "y1": float(xys[0][2]),
                    "x2": float(xys[1][1]), "y2": float(xys[1][2]),
                })

    bus_entries = []
    for entry in find_all(root, "bus_entry"):
        at = get_at(entry)
        if at:
            size = find_first(entry, "size")
            dx = float(size[1]) if size and len(size) > 1 else 0
            dy = float(size[2]) if size and len(size) > 2 else 0
            bus_entries.append({
                "x": round(at[0], 4), "y": round(at[1], 4),
                "dx": dx, "dy": dy,
            })

    bus_aliases = []
    for alias in find_all(root, "bus_alias"):
        name = alias[1] if len(alias) > 1 and isinstance(alias[1], str) else ""
        members_node = find_first(alias, "members")
        members = []
        if members_node:
            members = [m for m in members_node[1:] if isinstance(m, str)]
        if name:
            bus_aliases.append({"name": name, "members": members})

    return {
        "bus_wires": buses,
        "bus_entries": bus_entries,
        "bus_aliases": bus_aliases,
    }


def extract_title_block(root: list) -> dict:
    """Extract title block metadata (title, date, revision, company, comments).

    The title block is stored in a (title_block ...) node at the top level
    of each schematic sheet.
    """
    tb = find_first(root, "title_block")
    if not tb:
        return {}

    result = {}
    for field in ("title", "date", "rev", "company"):
        val = get_value(tb, field)
        if val:
            result[field] = val

    # Comments are numbered: (comment 1 "text"), (comment 2 "text"), ...
    for child in tb:
        if isinstance(child, list) and len(child) >= 3 and child[0] == "comment":
            try:
                num = int(child[1])
                text = child[2] if isinstance(child[2], str) else ""
                if text:
                    result[f"comment_{num}"] = text
            except (ValueError, TypeError):
                pass

    return result


def build_net_map(components: list[dict], wires: list[dict], labels: list[dict],
                  power_symbols: list[dict], junctions: list[dict]) -> dict:
    """Build a connectivity map using union-find on coordinates.

    Groups all electrically connected points into nets, then names them
    from labels and power symbols.
    """
    EPSILON = COORD_EPSILON

    # Collect all electrical points
    # Each point: (sheet, x, y, source_info)
    # The sheet index keeps each sheet's coordinate space separate so that
    # wires on different sheets at the same (x,y) don't falsely merge.
    parent = {}
    point_info = {}  # key -> list of info dicts

    def key(x, y, sheet=0):
        return (sheet, round(x / EPSILON) * EPSILON, round(y / EPSILON) * EPSILON)

    def find(p):
        while parent.get(p, p) != p:
            parent[p] = parent.get(parent[p], parent[p])
            p = parent[p]
        return p

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    def add_point(x, y, info, sheet=0):
        k = key(x, y, sheet)
        if k not in parent:
            parent[k] = k
        point_info.setdefault(k, []).append(info)
        return k

    # Add component pins (skip PWR_FLAG — it's an ERC marker, not a real connection)
    for comp in components:
        if comp.get("value") == "PWR_FLAG" or comp.get("type") == "power_flag":
            continue
        sheet = comp.get("_sheet", 0)
        for pin in comp.get("pins", []):
            add_point(pin["x"], pin["y"], {
                "source": "pin",
                "component": comp["reference"],
                "pin_number": pin["number"],
                "pin_name": pin["name"],
                "pin_type": pin["type"],
            }, sheet)

    # Add wire endpoints and union them.
    # Also build a list of wire segments so we can detect points that land
    # mid-wire (labels, pins, junctions, power symbols placed on a wire
    # between its endpoints).
    wire_segments = []  # list of (k1, k2, x1, y1, x2, y2, sheet)
    # Spatial grid index for fast wire segment lookup — avoids O(W*P) scans.
    # Grid cell size of 5mm captures typical KiCad schematic wire lengths.
    _WIRE_GRID_SIZE = 5.0
    wire_grid: dict[tuple, list[int]] = {}  # (sheet, gx, gy) -> [index into wire_segments]

    for wire in wires:
        sheet = wire.get("_sheet", 0)
        k1 = add_point(wire["x1"], wire["y1"], {"source": "wire"}, sheet)
        k2 = add_point(wire["x2"], wire["y2"], {"source": "wire"}, sheet)
        union(k1, k2)
        idx = len(wire_segments)
        wire_segments.append((k1, k2, wire["x1"], wire["y1"], wire["x2"], wire["y2"], sheet))
        # Index this segment in all grid cells its bounding box overlaps
        min_x, max_x = min(wire["x1"], wire["x2"]), max(wire["x1"], wire["x2"])
        min_y, max_y = min(wire["y1"], wire["y2"]), max(wire["y1"], wire["y2"])
        gx0 = int(min_x // _WIRE_GRID_SIZE)
        gx1 = int(max_x // _WIRE_GRID_SIZE)
        gy0 = int(min_y // _WIRE_GRID_SIZE)
        gy1 = int(max_y // _WIRE_GRID_SIZE)
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                wire_grid.setdefault((sheet, gx, gy), []).append(idx)

    def point_on_segment(px, py, x1, y1, x2, y2):
        """Check if point (px,py) lies on the wire segment (x1,y1)-(x2,y2)."""
        # Quick bounding box check with tolerance
        tol = 0.05
        if px < min(x1, x2) - tol or px > max(x1, x2) + tol:
            return False
        if py < min(y1, y2) - tol or py > max(y1, y2) + tol:
            return False
        # Cross product to check collinearity
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        seg_len_sq = (x2 - x1) ** 2 + (y2 - y1) ** 2
        if seg_len_sq < tol * tol:
            return False
        # Distance from point to line, squared
        if abs(cross) / (seg_len_sq ** 0.5) > tol:
            return False
        return True

    def union_with_overlapping_wires(k, px, py, sheet=0):
        """Union point k with any wire segment it lies on (same sheet only)."""
        gx = int(px // _WIRE_GRID_SIZE)
        gy = int(py // _WIRE_GRID_SIZE)
        candidates = wire_grid.get((sheet, gx, gy), ())
        for idx in candidates:
            wk1, wk2, wx1, wy1, wx2, wy2, ws = wire_segments[idx]
            if point_on_segment(px, py, wx1, wy1, wx2, wy2):
                union(k, wk1)
                return  # one match is enough since wire endpoints are already unioned

    # Add labels — in KiCad, labels can be placed anywhere on a wire,
    # not just at endpoints, so we must check for mid-wire placement.
    label_keys: dict[str, list] = {}  # label_name -> list of coordinate keys
    for lbl in labels:
        sheet = lbl.get("_sheet", 0)
        # KH-078: Defensive coercion — malformed labels can have list names
        lbl_name = lbl["name"]
        if isinstance(lbl_name, list):
            lbl_name = str(lbl_name[0]) if lbl_name else ""
        k = add_point(lbl["x"], lbl["y"], {
            "source": "label",
            "name": lbl_name,
            "label_type": lbl["type"],
        }, sheet)
        # Only global labels and power symbols connect across sheets.
        # Local labels only connect within the same sheet (handled by wire union).
        if lbl["type"] in ("global_label", "hierarchical_label"):
            label_keys.setdefault(lbl_name, []).append(k)
        else:
            # Local labels: union same-name labels within this sheet only
            local_key = (lbl_name, sheet)
            label_keys.setdefault(local_key, []).append(k)
        union_with_overlapping_wires(k, lbl["x"], lbl["y"], sheet)

    # Add power symbols — compute actual pin position from lib symbol.
    # PWR_FLAG is a DRC-only marker (tells ERC a net has a power source).
    # It doesn't create real connectivity, so exclude it entirely.
    for ps in power_symbols:
        if ps["net_name"] == "PWR_FLAG":
            continue
        sheet = ps.get("_sheet", 0)
        k = add_point(ps["x"], ps["y"], {
            "source": "power_symbol",
            "net_name": ps["net_name"],
        }, sheet)
        # Power symbols always connect across sheets (they define global nets)
        label_keys.setdefault(ps["net_name"], []).append(k)
        union_with_overlapping_wires(k, ps["x"], ps["y"], sheet)

    # Union global/hierarchical labels and power symbols with the same name.
    # This is what connects nets across different parts of the schematic.
    for lbl_name, keys in label_keys.items():
        for j in range(1, len(keys)):
            union(keys[0], keys[j])

    # Add junctions — also check mid-wire placement
    for junc in junctions:
        sheet = junc.get("_sheet", 0)
        k = add_point(junc["x"], junc["y"], {"source": "junction"}, sheet)
        union_with_overlapping_wires(k, junc["x"], junc["y"], sheet)

    # Union component pins that land mid-wire (rare but possible)
    for comp in components:
        if comp.get("value") == "PWR_FLAG" or comp.get("type") == "power_flag":
            continue
        sheet = comp.get("_sheet", 0)
        for pin in comp.get("pins", []):
            k = key(pin["x"], pin["y"], sheet)
            if k in parent:
                union_with_overlapping_wires(k, pin["x"], pin["y"], sheet)

    # Build net groups
    net_groups: dict[tuple, list[tuple]] = {}
    for k in parent:
        root_k = find(k)
        net_groups.setdefault(root_k, []).append(k)

    # Name the nets
    nets = {}
    net_id = 0
    for root_k, members in net_groups.items():
        # Collect all info for this net
        all_info = []
        for m in members:
            all_info.extend(point_info.get(m, []))

        # Find net name from labels or power symbols
        net_name = None
        for info in all_info:
            if info["source"] == "power_symbol":
                net_name = info["net_name"]
                break
            if info["source"] == "label":
                net_name = info["name"]

        if net_name is None:
            # Only create unnamed nets if they have component pins
            has_pins = any(i["source"] == "pin" for i in all_info)
            if not has_pins:
                continue
            net_name = f"__unnamed_{net_id}"
            net_id += 1

        # Collect pin connections
        pin_connections = []
        for info in all_info:
            if info["source"] == "pin":
                pin_connections.append({
                    "component": info["component"],
                    "pin_number": info["pin_number"],
                    "pin_name": info["pin_name"],
                    "pin_type": info["pin_type"],
                })

        # Keep nets that have pin connections, OR named nets (from labels/power symbols)
        # even without pins — this supports legacy files where pin positions aren't available
        if pin_connections or not net_name.startswith("__unnamed_"):
            if net_name in nets:
                # Merge into existing net (can happen when a local label shares a
                # name with a power symbol or global label on a disconnected wire
                # network — e.g., a "GND" label on a connector that isn't wired
                # to the main GND power symbol network).
                nets[net_name]["pins"].extend(pin_connections)
                nets[net_name]["point_count"] += len(members)
            else:
                nets[net_name] = {
                    "name": net_name,
                    "pins": pin_connections,
                    "point_count": len(members),
                }

    return nets


def generate_bom(components: list[dict]) -> list[dict]:
    """Generate grouped BOM from components."""
    groups: dict[tuple, dict] = {}

    # Deduplicate multi-unit symbols — only count each reference once
    seen_refs = set()

    for comp in components:
        if comp["type"] in ("power_symbol", "power_flag", "flag"):
            continue
        if not comp["in_bom"]:
            continue
        if comp["reference"] in seen_refs:
            continue
        seen_refs.add(comp["reference"])

        # Group key: value + footprint + MPN (or just value + footprint if no MPN)
        group_key = (comp["value"], comp["footprint"], comp["mpn"])

        if group_key not in groups:
            groups[group_key] = {
                "value": comp["value"],
                "footprint": comp["footprint"],
                "mpn": comp["mpn"],
                "manufacturer": comp["manufacturer"],
                "digikey": comp["digikey"],
                "mouser": comp["mouser"],
                "lcsc": comp["lcsc"],
                "element14": comp["element14"],
                "datasheet": comp["datasheet"],
                "description": comp["description"],
                "references": [],
                "quantity": 0,
                "dnp": comp["dnp"],
                "type": comp["type"],
            }

        groups[group_key]["references"].append(comp["reference"])
        groups[group_key]["quantity"] += 1

    # Sort by reference
    bom = sorted(groups.values(), key=lambda g: g["references"][0] if g["references"] else "")
    return bom


def compute_statistics(components: list[dict], nets: dict, bom: list[dict],
                       wires: list[dict], no_connects: list[dict]) -> dict:
    """Compute summary statistics."""
    # Deduplicate multi-unit symbols by reference
    seen_refs = set()
    non_power = []
    for c in components:
        if c["type"] in ("power_symbol", "power_flag", "flag"):
            continue
        if c["reference"] in seen_refs:
            continue
        seen_refs.add(c["reference"])
        non_power.append(c)
    bom_items = [b for b in bom if not b["dnp"]]
    dnp_items = [b for b in bom if b["dnp"]]

    type_counts = {}
    for comp in non_power:
        t = comp["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    # Power rails
    power_rails = sorted(set(
        comp["value"] for comp in components if comp["type"] == "power_symbol"
    ))
    # Also detect power rails by net name patterns (covers local/hierarchical labels)
    for net_name in nets:
        if _is_power_net_name(net_name) and net_name not in power_rails:
            power_rails.append(net_name)
    power_rails = sorted(set(power_rails))

    # Missing properties
    missing_mpn = [c["reference"] for c in non_power
                   if c["type"] not in ("test_point", "mounting_hole")
                   and not c["mpn"] and not c["dnp"] and c["in_bom"]]
    missing_footprint = [c["reference"] for c in non_power
                         if not c["footprint"] and c["in_bom"] and not c["dnp"]]

    return {
        "total_components": len(non_power),
        "unique_parts": len(bom_items),
        "dnp_parts": sum(b["quantity"] for b in dnp_items),
        "total_nets": len(nets),
        "total_wires": len(wires),
        "total_no_connects": len(no_connects),
        "component_types": type_counts,
        "power_rails": power_rails,
        "missing_mpn": missing_mpn,
        "missing_footprint": missing_footprint,
    }


def build_pin_to_net_map(nets: dict) -> dict:
    """Build a reverse map: (component, pin_number) -> (net_name, net_info)."""
    pin_net = {}
    for net_name, net_info in nets.items():
        for p in net_info["pins"]:
            pin_net[(p["component"], p["pin_number"])] = (net_name, net_info)
    return pin_net


def get_net_neighbors(net_info: dict, exclude_ref: str) -> list[dict]:
    """Get all components on a net except the given reference, with their details."""
    neighbors = []
    for p in net_info["pins"]:
        if p["component"] != exclude_ref and not p["component"].startswith("#"):
            neighbors.append({
                "component": p["component"],
                "pin_number": p["pin_number"],
                "pin_name": p["pin_name"],
                "pin_type": p["pin_type"],
            })
    return neighbors


def _classify_ic_function(lib_id: str, value: str, description: str) -> str:
    """Classify IC function from library ID, value, and description.

    Three-tier lookup:
    1. KiCad stdlib library prefix (most reliable)
    2. Value/part number keyword matching
    3. Description keyword fallback
    """
    lib_lower = lib_id.lower()
    val_lower = value.lower()
    desc_lower = description.lower()

    # Skip connectors — they're analyzed for pinout but not for IC function
    lib_prefix = lib_lower.split(":")[0] if ":" in lib_lower else ""
    if lib_prefix.startswith("connector"):
        return ""

    # Tier 1: KiCad standard library prefix mapping
    _LIB_PREFIX_MAP = {
        "regulator_linear": "linear regulator",
        "regulator_switching": "switching regulator",
        "regulator_controller": "regulator controller",
        "amplifier_operational": "operational amplifier",
        "amplifier_audio": "audio amplifier",
        "amplifier_instrumentation": "instrumentation amplifier",
        "amplifier_difference": "difference amplifier",
        "amplifier_current": "current sense amplifier",
        "amplifier_buffer": "buffer amplifier",
        "amplifier_video": "video amplifier",
        "analog_adc": "ADC",
        "analog_dac": "DAC",
        "analog_switch": "analog switch",
        "comparator": "comparator",
        "converter_dcdc": "DC-DC converter",
        "driver_motor": "motor driver",
        "driver_led": "LED driver",
        "driver_gate": "gate driver",
        "driver_display": "display driver",
        "driver_fet": "FET driver",
        "interface_can_lin": "CAN/LIN transceiver",
        "interface_ethernet": "Ethernet PHY",
        "interface_usb": "USB interface",
        "interface_uart": "UART interface",
        "interface_spi": "SPI interface",
        "interface_i2c": "I2C interface",
        "interface_rs485": "RS-485 transceiver",
        "interface_lvds": "LVDS interface",
        "interface_hdmi": "HDMI interface",
        "interface_optical": "optical interface",
        "logic_74xx": "logic IC (74xx)",
        "logic_4000": "logic IC (4000 series)",
        "logic_level": "level shifter",
        "memory_eeprom": "EEPROM",
        "memory_flash": "flash memory",
        "memory_ram": "RAM",
        "memory_rom": "ROM",
        "mcu": "microcontroller",
        "fpga": "FPGA",
        "cpld": "CPLD",
        "dsp": "DSP",
        "power_management": "power management IC",
        "power_supervisor": "voltage supervisor",
        "power_protection": "power protection IC",
        "rf_amplifier": "RF amplifier",
        "rf_mixer": "RF mixer",
        "rf_switch": "RF switch",
        "rf": "RF IC",
        "sensor_temperature": "temperature sensor",
        "sensor_pressure": "pressure sensor",
        "sensor_humidity": "humidity sensor",
        "sensor_current": "current sensor",
        "sensor_motion": "motion sensor",
        "sensor_magnetic": "magnetic sensor",
        "sensor_optical": "optical sensor",
        "sensor": "sensor IC",
        "timer": "timer IC",
        "reference_voltage": "voltage reference",
    }
    for prefix, func in _LIB_PREFIX_MAP.items():
        if lib_prefix == prefix or lib_lower.startswith(prefix + ":"):
            return func

    # Tier 2: Value / part number keyword matching
    _VALUE_KEYWORDS = [
        # Microcontrollers
        (("esp32", "esp8266", "esp32s", "esp32c", "esp32h"), "microcontroller (ESP)"),
        (("stm32", "stm8"), "microcontroller (STM)"),
        (("atmega", "attiny", "at90", "atxmega"), "microcontroller (AVR)"),
        (("pic16", "pic18", "pic24", "pic32", "dspic"), "microcontroller (PIC)"),
        (("rp2040", "rp2350"), "microcontroller (RP)"),
        (("nrf51", "nrf52", "nrf53", "nrf91"), "microcontroller (nRF)"),
        (("samd", "same", "samg", "saml", "samr"), "microcontroller (SAM)"),
        (("msp430", "msp432"), "microcontroller (MSP)"),
        (("efm32", "efr32"), "microcontroller (EFx32)"),
        (("cy8c",), "microcontroller (Cypress)"),
        (("gd32",), "microcontroller (GD32)"),
        (("ch32",), "microcontroller (CH32)"),
        (("kb2040",), "microcontroller (RP dev board)"),
        # FPGAs
        (("ice40", "ecp5", "machxo", "nexus"), "FPGA (Lattice)"),
        (("xc7", "xc6", "xczu", "xc2", "xcku", "artix", "spartan", "kintex", "virtex", "zynq"), "FPGA (Xilinx)"),
        (("10cl", "10m0", "5cg", "max10", "cyclone"), "FPGA (Intel)"),
        # Regulators
        (("lm117", "lm317", "lm337", "lm78", "lm79", "ams1117", "ap2112",
          "mic5205", "mic5504", "xc6206", "xc6220", "tps73", "tps76", "rt9013",
          "ld1117", "mcp1700", "mcp1703", "mcp1826", "ht7333", "ht7350"), "linear regulator"),
        (("lm2596", "lm2576", "mc34063", "tps54", "tps56", "tps61", "tps62",
          "tps63", "tps65", "mp1584", "mp2307", "mp2315", "mp2359",
          "ap3012", "sy80", "mt3608"), "switching regulator"),
        # Shift registers / logic
        (("74hc", "74lvc", "74ahc", "74act", "74ac", "sn74"), "logic IC"),
        (("cd4", "hef4", "mc14"), "logic IC (CMOS)"),
        # Communication
        (("max232", "max3232", "sp3232"), "RS-232 transceiver"),
        (("max485", "max3485", "sn65hvd", "thvd1", "isl317"), "RS-485 transceiver"),
        (("mcp2515", "mcp2551", "mcp2562", "sn65hvd23", "tja1"), "CAN transceiver"),
        (("cp2102", "ch340", "ft232", "ft2232", "pl2303", "ch9102"), "USB-UART bridge"),
        (("usb3300", "usb3320", "usb2514", "tusb"), "USB IC"),
        (("lan87", "lan91", "lan8720", "lan8710", "ksz", "dp83", "rtl81", "ip101"), "Ethernet PHY"),
        (("w5500", "w5100", "enc28j60"), "Ethernet controller"),
        (("sx127", "sx126", "rfm9", "rfm6", "cc1101", "at86rf"), "radio transceiver"),
        # Audio
        (("max9", "ssm2", "tpa", "lm386", "tda", "pam8"), "audio amplifier"),
        (("wm8", "es8", "ak4", "pcm51", "pcm17", "cs42", "sgtl5", "tlv320"), "audio codec"),
        # Display / LED
        (("ssd1306", "ssd1309", "st7735", "st7789", "ili9", "hx8357",
          "uc1701", "nt35", "sharp_ls"), "display controller"),
        (("ws2812", "sk6812", "apa102", "ws2813", "ws2815"), "addressable LED"),
        (("pca9685", "tlc5940", "is31fl"), "LED driver IC"),
        # Sensors
        (("bme280", "bme680", "bmp280", "bmp390"), "environmental sensor"),
        (("mpu6", "mpu9", "icm20", "lsm6", "bno0", "lis3"), "IMU/motion sensor"),
        (("ina21", "ina22", "ina23", "ina18", "ina19"), "current sense amplifier"),
        (("ads1", "mcp33", "mcp34", "mcp35", "max114", "max119"), "ADC"),
        (("mcp47", "dac8", "ad56", "ad57"), "DAC"),
        # Power management
        (("bq24", "bq25", "bq40", "ltc40", "mcp738"), "battery management"),
        (("tps20", "tps21", "ap22"), "power switch/load switch"),
        # Miscellaneous
        (("drv8", "a4988", "tmc2", "tmc5"), "motor driver"),
        (("esd", "prtr", "usblc", "tpd", "pesd", "sp05"), "ESD protection"),
        (("ds1307", "ds3231", "pcf8523", "rv3028", "rv8803"), "RTC"),
        (("at24c", "24lc", "24aa", "m24c", "cat24"), "EEPROM"),
        (("w25q", "at25", "mx25", "gd25", "is25", "sst26"), "SPI flash"),
    ]
    for keywords, func in _VALUE_KEYWORDS:
        if any(val_lower.startswith(k) for k in keywords):
            return func

    # Also check lib_id part name (after colon)
    lib_part = lib_lower.split(":")[-1] if ":" in lib_lower else ""
    if lib_part:
        for keywords, func in _VALUE_KEYWORDS:
            if any(lib_part.startswith(k) for k in keywords):
                return func

    # Tier 3: Description keyword fallback
    _DESC_KEYWORDS = [
        ("microcontroller", "microcontroller"),
        ("mcu", "microcontroller"),
        ("fpga", "FPGA"),
        ("cpld", "CPLD"),
        ("voltage regulator", "voltage regulator"),
        ("ldo", "linear regulator"),
        ("buck converter", "switching regulator"),
        ("boost converter", "switching regulator"),
        ("dc-dc", "DC-DC converter"),
        ("operational amplifier", "operational amplifier"),
        ("op-amp", "operational amplifier"),
        ("opamp", "operational amplifier"),
        ("comparator", "comparator"),
        ("adc", "ADC"),
        ("dac", "DAC"),
        ("uart", "UART interface"),
        ("usart", "UART interface"),
        ("spi", "SPI interface"),
        ("i2c", "I2C interface"),
        ("can transceiver", "CAN transceiver"),
        ("rs-485", "RS-485 transceiver"),
        ("rs-232", "RS-232 transceiver"),
        ("ethernet", "Ethernet IC"),
        ("usb", "USB IC"),
        ("motor driver", "motor driver"),
        ("gate driver", "gate driver"),
        ("led driver", "LED driver"),
        ("audio", "audio IC"),
        ("codec", "audio codec"),
        ("sensor", "sensor IC"),
        ("eeprom", "EEPROM"),
        ("flash", "flash memory"),
        ("shift register", "shift register"),
        ("multiplexer", "multiplexer"),
        ("level shift", "level shifter"),
        ("voltage reference", "voltage reference"),
        ("timer", "timer IC"),
        ("rtc", "RTC"),
        ("real-time clock", "RTC"),
        ("power supervisor", "voltage supervisor"),
        ("watchdog", "watchdog timer"),
        ("battery", "battery management"),
        ("charger", "battery charger"),
        ("rf", "RF IC"),
    ]
    for keyword, func in _DESC_KEYWORDS:
        if keyword in desc_lower:
            return func

    return ""


def analyze_ic_pinouts(components: list[dict], nets: dict, no_connects: list[dict], pin_net: dict | None = None) -> list[dict]:
    """Analyze each IC's pinout for datasheet cross-referencing.

    For every IC, produces a detailed per-pin analysis showing:
    - What net each pin connects to
    - What other components are on that net (with their values)
    - Whether power pins have decoupling capacitors
    - Whether input pins have pull-up/pull-down resistors
    - Pins that are unconnected (and whether they should be)
    """
    EPSILON = COORD_EPSILON
    if pin_net is None:
        pin_net = build_pin_to_net_map(nets)

    # Build component lookup for values/types
    comp_lookup = {}
    for c in components:
        comp_lookup[c["reference"]] = c

    # Build no-connect position set
    nc_positions = set()
    for nc in no_connects:
        nc_positions.add((round(nc["x"] / EPSILON) * EPSILON,
                          round(nc["y"] / EPSILON) * EPSILON))

    results = []

    # Analyze ICs and other complex components (connectors, crystals, oscillators, etc.)
    target_types = {"ic", "connector", "crystal", "oscillator"}
    target_components = [c for c in components if c["type"] in target_types]

    for ic in target_components:
        ref = ic["reference"]
        pin_analysis = []
        decap_summary = {}  # net_name -> list of capacitor refs
        unconnected = []
        power_pins_detail = []
        signal_pins_detail = []

        for pin in ic.get("pins", []):
            # Ensure pin number and name are never null in output
            pin_number = pin.get("number") or ""
            pin_name = pin.get("name") or ""
            if not pin_number:
                # Fallback: use pin UUID if available, otherwise "unknown"
                pin_number = ic.get("pin_uuids", {}).get("", "unknown") if not pin_number else pin_number
                if not pin_number or pin_number == "unknown":
                    pin_number = f"unknown_{pin.get('x', 0):.0f}_{pin.get('y', 0):.0f}"

            pin_key = (ref, pin_number)
            net_name, net_info = pin_net.get(pin_key, (None, None))

            # Check if pin has a no-connect marker
            pin_pos = (round(pin["x"] / EPSILON) * EPSILON,
                       round(pin["y"] / EPSILON) * EPSILON)
            has_no_connect = pin_pos in nc_positions

            # Get components sharing this net
            neighbors = []
            neighbor_summary = []
            if net_info:
                neighbors = get_net_neighbors(net_info, ref)
                for nb in neighbors:
                    nb_comp = comp_lookup.get(nb["component"])
                    if nb_comp:
                        neighbor_summary.append({
                            "ref": nb["component"],
                            "value": nb_comp.get("value", ""),
                            "type": nb_comp.get("type", ""),
                            "pin": nb["pin_number"],
                            "pin_name": nb["pin_name"],
                        })

            # Classify what's connected
            connected_caps = [n for n in neighbor_summary if n["type"] == "capacitor"]
            connected_resistors = [n for n in neighbor_summary if n["type"] == "resistor"]
            connected_inductors = [n for n in neighbor_summary if n["type"] == "inductor"]

            pin_entry = {
                "pin_number": pin_number,
                "pin_name": pin_name,
                "pin_type": pin["type"],
                "net": net_name or ("NO_CONNECT" if has_no_connect else "UNCONNECTED"),
                "connected_to": neighbor_summary,
            }

            # Determine if this is functionally a power pin based on type OR net name.
            # Many lib symbols mark power pins as "input" or "passive", so also check
            # if the net is a known power rail.
            is_power_pin = pin["type"] in ("power_in", "power_out")
            if not is_power_pin and net_name:
                # Check net name against common power rail patterns
                net_upper = net_name.upper()
                is_power_pin = (
                    net_upper in ("GND", "VSS", "AGND", "DGND", "PGND",
                                  "VCC", "VDD", "AVCC", "AVDD", "DVCC", "DVDD",
                                  "VBUS", "V_USB")
                    or net_upper.startswith("+")
                    or net_upper.startswith("V+")
                )
            # Also check pin name for power pin hints
            if not is_power_pin and pin["name"]:
                pname = pin["name"].upper()
                is_power_pin = pname in (
                    "VCC", "VDD", "VSS", "GND", "AVCC", "AVDD", "DVCC", "DVDD",
                    "VIN", "VOUT", "PGND", "AGND", "DGND", "VBUS",
                )

            if is_power_pin:
                # For decoupling cap detection, only list caps directly on THIS net
                # (not the entire GND net which connects everything)
                net_is_ground = net_name and net_name.upper() in (
                    "GND", "VSS", "AGND", "DGND", "PGND")
                if net_is_ground:
                    # Don't list decoupling caps for ground pins — they're shared globally
                    pin_entry["has_decoupling_cap"] = True  # ground is always decoupled
                    pin_entry["decoupling_caps"] = []
                    pin_entry["note"] = "Ground net — decoupling caps listed on VCC/VDD pins"
                else:
                    pin_entry["has_decoupling_cap"] = len(connected_caps) > 0
                    pin_entry["decoupling_caps"] = [
                        {"ref": c["ref"], "value": c["value"]} for c in connected_caps
                    ]
                    if net_name and connected_caps:
                        decap_summary.setdefault(net_name, []).extend(
                            {"ref": c["ref"], "value": c["value"]} for c in connected_caps
                        )
                power_pins_detail.append(pin_entry)

            elif pin["type"] in ("input", "bidirectional", "open_collector", "open_emitter"):
                # Check for pull-up/pull-down resistors
                pull_resistors = []
                for r in connected_resistors:
                    r_comp = comp_lookup.get(r["ref"])
                    if r_comp:
                        # Check where the other end of the resistor goes
                        other_pin = "1" if r["pin"] == "2" else "2"
                        other_key = (r["ref"], other_pin)
                        other_net, _ = pin_net.get(other_key, (None, None))
                        if other_net in ("GND", "VSS"):
                            pull_resistors.append({
                                "ref": r["ref"], "value": r_comp["value"],
                                "direction": "pull-down", "to_net": other_net,
                            })
                        elif other_net and any(kw in other_net.upper()
                                               for kw in ("VCC", "VDD", "+3", "+5", "VBUS")):
                            pull_resistors.append({
                                "ref": r["ref"], "value": r_comp["value"],
                                "direction": "pull-up", "to_net": other_net,
                            })
                        else:
                            pull_resistors.append({
                                "ref": r["ref"], "value": r_comp["value"],
                                "direction": "series", "to_net": other_net,
                            })
                if pull_resistors:
                    pin_entry["resistors"] = pull_resistors
                signal_pins_detail.append(pin_entry)

            else:
                signal_pins_detail.append(pin_entry)

            if not net_name and not has_no_connect:
                unconnected.append(pin_entry)

            pin_analysis.append(pin_entry)

        # Deduplicate decoupling caps per net
        unique_decaps = {}
        for net_name, caps in decap_summary.items():
            seen = set()
            unique = []
            for c in caps:
                if c["ref"] not in seen:
                    seen.add(c["ref"])
                    unique.append(c)
            unique_decaps[net_name] = unique

        # Build IC analysis summary
        ic_result = {
            "reference": ref,
            "value": ic["value"],
            "type": ic["type"],
            "lib_id": ic["lib_id"],
            "mpn": ic.get("mpn", ""),
            "description": ic.get("description", ""),
            "datasheet": ic.get("datasheet", ""),
            "function": _classify_ic_function(ic["lib_id"], ic["value"], ic.get("description", "")),
            "total_pins": len(pin_analysis),
            "unconnected_pins": len(unconnected),
            "pins": sorted(pin_analysis, key=lambda p: _pin_sort_key(p["pin_number"])),
            "power_pins": power_pins_detail,
            "signal_pins": signal_pins_detail,
            "decoupling_caps_by_rail": unique_decaps,
        }

        if unconnected:
            ic_result["unconnected_pin_list"] = unconnected

        results.append(ic_result)

    return results


def _pin_sort_key(pin_num: str):
    """Sort pin numbers numerically when possible, alphabetically otherwise."""
    try:
        return (0, int(pin_num))
    except ValueError:
        return (1, pin_num)


def identify_subcircuits(components: list[dict], nets: dict, pin_net: dict | None = None) -> list[dict]:
    """Identify potential subcircuit groupings around ICs."""
    if pin_net is None:
        pin_net = build_pin_to_net_map(nets)
    comp_lookup = {c["reference"]: c for c in components}
    subcircuits = []

    ics = [c for c in components if c["type"] == "ic"]

    for ic in ics:
        ref = ic["reference"]

        # Find all nets this IC connects to
        ic_nets = set()
        for pin in ic.get("pins", []):
            net_name, _ = pin_net.get((ref, pin["number"]), (None, None))
            if net_name:
                ic_nets.add(net_name)

        # Find all components that share nets with this IC (1-hop neighbors)
        # Skip power/ground nets — every IC shares VCC/GND, inflating neighbors
        neighbors = set()
        for net_name in ic_nets:
            if _is_power_net_name(net_name) or _is_ground_name(net_name):
                continue
            if net_name in nets:
                for p in nets[net_name]["pins"]:
                    r = p["component"]
                    if r != ref and not r.startswith("#"):
                        neighbors.add(r)

        # Build neighbor details with values
        neighbor_details = []
        for nb_ref in sorted(neighbors):
            nb_comp = comp_lookup.get(nb_ref)
            if nb_comp:
                neighbor_details.append({
                    "ref": nb_ref,
                    "value": nb_comp.get("value", ""),
                    "type": nb_comp.get("type", ""),
                })

        subcircuits.append({
            "center_ic": ref,
            "ic_value": ic["value"],
            "ic_mpn": ic.get("mpn", ""),
            "ic_lib_id": ic["lib_id"],
            "neighbor_components": neighbor_details,
            "description": ic.get("description", ""),
        })

    return subcircuits


# KiCad 5 legacy .lib pin type codes → KiCad 6+ type strings
_LEGACY_PIN_TYPE_MAP = {
    "I": "input",
    "O": "output",
    "B": "bidirectional",
    "T": "tri_state",
    "P": "passive",
    "W": "power_in",
    "w": "power_out",
    "C": "open_collector",
    "E": "open_emitter",
    "N": "unconnected",
    "U": "unspecified",
}

# Built-in pin definitions for standard KiCad 4/5 library symbols that won't
# be found in project .lib files (power/device/conn libs).  Offsets are in mm
# at the connection endpoint (where wires attach), matching the output of
# _parse_legacy_lib() after mil→mm conversion.
#
# Values derived from the most common pin positions across 1292 KiCad 4/5
# cache libraries.  Different KiCad versions use slightly different offsets
# (e.g., R/C at ±100, ±150, ±200, or ±250 mils); the wire-snap fallback
# (_snap_pins_to_wires) handles version mismatches automatically.
_M = 0.0254  # 1 mil in mm — shorthand for readability

def _conn_1xN(n):
    """Generate pin list for CONN_01X{n} (single-row, n pins, x=-200 mils)."""
    half = (n - 1) * 50  # half-span in mils
    return [{"number": str(i + 1), "name": f"P{i + 1}", "type": "passive",
             "offset": [-200 * _M, (half - i * 100) * _M]} for i in range(n)]

def _conn_2xN(n):
    """Generate pin list for CONN_02X{n} (2-row, 2n pins, x=±250 mils)."""
    half = (n - 1) * 50
    pins = []
    for i in range(n):
        y = (half - i * 100) * _M
        pins.append({"number": str(2 * i + 1), "name": f"P{2*i+1}", "type": "passive",
                      "offset": [-250 * _M, y]})
        pins.append({"number": str(2 * i + 2), "name": f"P{2*i+2}", "type": "passive",
                      "offset": [250 * _M, y]})
    return pins

_STANDARD_LIB_PINS = {
    # --- Passives (most common: ±150 mils vertical) ---
    "R": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 150 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -150 * _M]},
    ],
    "C": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 150 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -150 * _M]},
    ],
    "L": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 150 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -150 * _M]},
    ],
    "CP": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 150 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -150 * _M]},
    ],
    "CP1": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 150 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -150 * _M]},
    ],
    "C_Polarized": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 150 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -150 * _M]},
    ],
    "INDUCTOR": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 300 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -300 * _M]},
    ],
    # --- Small passives (±100 mils vertical) ---
    "C_Small": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 100 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -100 * _M]},
    ],
    "R_Small": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 100 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -100 * _M]},
    ],
    "L_Small": [
        {"number": "1", "name": "~", "type": "passive", "offset": [0, 100 * _M]},
        {"number": "2", "name": "~", "type": "passive", "offset": [0, -100 * _M]},
    ],
    "INDUCTOR_SMALL": [
        {"number": "1", "name": "~", "type": "passive", "offset": [-250 * _M, 0]},
        {"number": "2", "name": "~", "type": "passive", "offset": [250 * _M, 0]},
    ],
    # --- Diodes (±150 mils horizontal, A=anode K=cathode) ---
    "D": [
        {"number": "1", "name": "K", "type": "passive", "offset": [-150 * _M, 0]},
        {"number": "2", "name": "A", "type": "passive", "offset": [150 * _M, 0]},
    ],
    "D_Schottky": [
        {"number": "1", "name": "K", "type": "passive", "offset": [-150 * _M, 0]},
        {"number": "2", "name": "A", "type": "passive", "offset": [150 * _M, 0]},
    ],
    "D_Zener": [
        {"number": "1", "name": "K", "type": "passive", "offset": [-150 * _M, 0]},
        {"number": "2", "name": "A", "type": "passive", "offset": [150 * _M, 0]},
    ],
    "DIODESCH": [
        {"number": "1", "name": "K", "type": "passive", "offset": [-200 * _M, 0]},
        {"number": "2", "name": "A", "type": "passive", "offset": [200 * _M, 0]},
    ],
    "LED": [
        {"number": "1", "name": "K", "type": "passive", "offset": [-200 * _M, 0]},
        {"number": "2", "name": "A", "type": "passive", "offset": [200 * _M, 0]},
    ],
    # --- Crystals ---
    "CRYSTAL": [
        {"number": "1", "name": "1", "type": "passive", "offset": [-300 * _M, 0]},
        {"number": "2", "name": "2", "type": "passive", "offset": [300 * _M, 0]},
    ],
    "Crystal": [
        {"number": "1", "name": "1", "type": "passive", "offset": [-150 * _M, 0]},
        {"number": "2", "name": "2", "type": "passive", "offset": [150 * _M, 0]},
    ],
    "Crystal_GND24": [
        {"number": "1", "name": "1", "type": "passive", "offset": [-150 * _M, 0]},
        {"number": "2", "name": "2", "type": "passive", "offset": [0, -200 * _M]},
        {"number": "3", "name": "3", "type": "passive", "offset": [150 * _M, 0]},
        {"number": "4", "name": "4", "type": "passive", "offset": [0, 200 * _M]},
    ],
    # --- Switches ---
    "SW_PUSH": [
        {"number": "1", "name": "1", "type": "passive", "offset": [-300 * _M, 0]},
        {"number": "2", "name": "2", "type": "passive", "offset": [300 * _M, 0]},
    ],
    "SW_Push": [
        {"number": "1", "name": "1", "type": "passive", "offset": [-300 * _M, 0]},
        {"number": "2", "name": "2", "type": "passive", "offset": [300 * _M, 0]},
    ],
    # --- Transistors ---
    "Q_NPN_BEC": [
        {"number": "1", "name": "B", "type": "input", "offset": [-200 * _M, 0]},
        {"number": "2", "name": "E", "type": "passive", "offset": [100 * _M, -200 * _M]},
        {"number": "3", "name": "C", "type": "passive", "offset": [100 * _M, 200 * _M]},
    ],
    "Q_PNP_BEC": [
        {"number": "1", "name": "B", "type": "input", "offset": [-200 * _M, 0]},
        {"number": "2", "name": "E", "type": "passive", "offset": [100 * _M, 200 * _M]},
        {"number": "3", "name": "C", "type": "passive", "offset": [100 * _M, -200 * _M]},
    ],
    "Q_NMOS_GSD": [
        {"number": "1", "name": "G", "type": "input", "offset": [-200 * _M, 0]},
        {"number": "2", "name": "S", "type": "passive", "offset": [100 * _M, -200 * _M]},
        {"number": "3", "name": "D", "type": "passive", "offset": [100 * _M, 200 * _M]},
    ],
    "Q_PMOS_GSD": [
        {"number": "1", "name": "G", "type": "input", "offset": [-200 * _M, 0]},
        {"number": "2", "name": "S", "type": "passive", "offset": [100 * _M, 200 * _M]},
        {"number": "3", "name": "D", "type": "passive", "offset": [100 * _M, -200 * _M]},
    ],
    "MOSFET_N": [
        {"number": "1", "name": "G", "type": "input", "offset": [-200 * _M, 0]},
        {"number": "2", "name": "S", "type": "passive", "offset": [100 * _M, -200 * _M]},
        {"number": "3", "name": "D", "type": "passive", "offset": [100 * _M, 200 * _M]},
    ],
    "MOSFET_P": [
        {"number": "1", "name": "G", "type": "input", "offset": [-200 * _M, 0]},
        {"number": "2", "name": "S", "type": "passive", "offset": [100 * _M, 200 * _M]},
        {"number": "3", "name": "D", "type": "passive", "offset": [100 * _M, -200 * _M]},
    ],
    # --- Resistor packs ---
    "R_PACK4": [
        {"number": str(i + 1), "name": f"P{i+1}", "type": "passive",
         "offset": [-200 * _M, (150 - i * 100) * _M]} for i in range(4)
    ] + [
        {"number": str(i + 5), "name": f"R{4-i}", "type": "passive",
         "offset": [200 * _M, (-150 + i * 100) * _M]} for i in range(4)
    ],
    "R_PACK8": [
        {"number": str(i + 1), "name": f"P{i+1}", "type": "passive",
         "offset": [-200 * _M, (350 - i * 100) * _M]} for i in range(8)
    ] + [
        {"number": str(i + 9), "name": f"R{8-i}", "type": "passive",
         "offset": [200 * _M, (-350 + i * 100) * _M]} for i in range(8)
    ],
    # --- Single-row connectors (CONN_01X01 through CONN_01X20) ---
    **{f"CONN_01X{n:02d}": _conn_1xN(n) for n in range(1, 21)},
    # --- Short-form single-row connectors (CONN_1 through CONN_20) ---
    **{f"CONN_{n}": _conn_1xN(n) for n in range(1, 21)},
    # --- Dual-row connectors (CONN_02Xnn format) ---
    **{f"CONN_02X{n:02d}": _conn_2xN(n) for n in range(2, 21)},
    # --- Short-form dual-row connectors (CONN_NX2 format) ---
    **{f"CONN_{n}X2": _conn_2xN(n) for n in range(2, 21)},
    "CONN_5X2": _conn_2xN(5),
}


def _parse_legacy_lib(path: str) -> dict:
    """Parse a KiCad 5 .lib file and return symbol definitions.

    Returns dict matching extract_lib_symbols() format:
    {symbol_name: {"pins": [...], "unit_pins": {unit: [...]}, ...}}
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return {}

    if not lines or not lines[0].startswith("EESchema-LIBRARY"):
        return {}

    MIL_TO_MM = _MIL_MM
    symbols = {}
    current_name = None
    current_pins = []
    current_unit_pins = {}
    current_datasheet = ""
    in_draw = False

    for line in lines:
        line = line.strip()

        if line.startswith("DEF "):
            parts = line.split()
            if len(parts) >= 2:
                current_name = parts[1].lstrip("~")
                current_pins = []
                current_unit_pins = {}
                current_datasheet = ""
                in_draw = False

        elif line.startswith("F3 ") and current_name is not None:
            m = re.match(r'F3\s+"([^"]*)"', line)
            if m:
                current_datasheet = m.group(1)

        elif line == "DRAW":
            in_draw = True

        elif line == "ENDDRAW":
            in_draw = False

        elif line.startswith("X ") and in_draw and current_name is not None:
            # X name number x y length dir sz1 sz2 unit convert elec_type [shape]
            parts = line.split()
            if len(parts) >= 12:
                pin_name = parts[1]
                pin_number = parts[2]
                try:
                    px = int(parts[3]) * MIL_TO_MM
                    py = int(parts[4]) * MIL_TO_MM
                    unit_num = int(parts[9])
                except (ValueError, IndexError):
                    continue
                elec_code = parts[11]
                pin_type = _LEGACY_PIN_TYPE_MAP.get(elec_code, "unspecified")

                pin = {
                    "number": pin_number,
                    "name": pin_name if pin_name != "~" else "",
                    "type": pin_type,
                    "shape": "",
                    "offset": [round(px, 4), round(py, 4)],
                }
                current_pins.append(pin)
                current_unit_pins.setdefault(unit_num, []).append(pin)

        elif line == "ENDDEF" and current_name is not None:
            # Check if multi-unit: has pins in more than one non-zero unit
            non_zero_units = {u for u in current_unit_pins if u != 0}
            has_multi_unit = len(non_zero_units) > 1

            symbols[current_name] = {
                "pins": current_pins,
                "unit_pins": current_unit_pins if has_multi_unit else None,
                "description": "",
                "keywords": "",
                "is_power": False,
                "ki_fp_filters": "",
                "alternates": None,
            }
            if current_datasheet:
                symbols[current_name]["datasheet"] = current_datasheet
            current_name = None

    return symbols


def _resolve_legacy_libs(sch_path: str, all_sch_lines: dict) -> dict:
    """Find and parse .lib files for legacy schematics.

    Args:
        sch_path: Path to the root .sch file.
        all_sch_lines: Dict mapping sheet paths to their lines (for LIBS: extraction).

    Strategy:
    1. Look for *-cache.lib alongside the root .sch file (self-contained, preferred)
    2. Parse LIBS: directives, search for each .lib in project directory tree
    3. Fall back to built-in defaults for standard KiCad symbols
    """
    base = Path(sch_path)
    base_dir = base.parent
    stem = base.stem

    # Strategy 1: cache lib — if present, it contains ALL symbols
    cache_path = base_dir / f"{stem}-cache.lib"
    if cache_path.exists():
        symbols = _parse_legacy_lib(str(cache_path))
        # Also add standard library fallbacks for anything missing
        for name, pins in _STANDARD_LIB_PINS.items():
            if name not in symbols:
                symbols[name] = {
                    "pins": pins, "unit_pins": None, "description": "",
                    "keywords": "", "is_power": False, "ki_fp_filters": "",
                    "alternates": None,
                }
        return symbols

    # Strategy 2: collect LIBS: directives from all parsed sheets
    lib_names = []
    seen_lib_names = set()
    for sheet_lines in all_sch_lines.values():
        for line in sheet_lines:
            if line.startswith("LIBS:"):
                name = line[5:].strip()
                # Skip cache lib references (they reference the missing cache)
                if name.endswith("-cache") or name.endswith("-rescue"):
                    continue
                # Skip standard KiCad libs we handle with built-in defaults
                if name in ("power", "device", "conn", "transistors",
                            "linear", "regul", "74xx", "cmos4000",
                            "adc-dac", "memory", "xilinx", "microcontrollers",
                            "dsp", "microchip", "analog_switches",
                            "motorola", "texas", "intel", "audio",
                            "interface", "digital-audio", "philips",
                            "display", "cypress", "siliconi", "opto",
                            "atmel", "contrib", "valves"):
                    continue
                if name not in seen_lib_names:
                    lib_names.append(name)
                    seen_lib_names.add(name)

    # Search for each .lib file
    # Build search dirs: from .sch dir, walk up to 4 levels
    search_dirs = []
    d = base_dir
    for _ in range(5):
        search_dirs.append(d)
        # Also check lib/ subdirectory
        lib_subdir = d / "lib"
        if lib_subdir.is_dir():
            search_dirs.append(lib_subdir)
        parent = d.parent
        if parent == d:
            break
        d = parent

    symbols = {}
    for lib_name in lib_names:
        lib_file = f"{lib_name}.lib"
        for search_dir in search_dirs:
            candidate = search_dir / lib_file
            if candidate.exists():
                parsed = _parse_legacy_lib(str(candidate))
                symbols.update(parsed)
                break

    # Add standard library fallbacks
    for name, pins in _STANDARD_LIB_PINS.items():
        if name not in symbols:
            symbols[name] = {
                "pins": pins, "unit_pins": None, "description": "",
                "keywords": "", "is_power": False, "ki_fp_filters": "",
                "alternates": None,
            }

    return symbols


def _snap_pins_to_wires(components: list[dict], wires: list[dict]) -> None:
    """Snap component pins to nearby wire endpoints when positions don't match.

    Legacy KiCad standard library pin offsets vary between versions (±100, ±150,
    ±200, ±250 mils for R/C/L/D).  When the built-in fallback positions don't
    match the actual wire endpoints, this function finds the closest wire
    endpoint for each pin and snaps to it.

    Only snaps when the computed position has no wire endpoint within
    COORD_EPSILON, and a wire endpoint exists within _MAX_SNAP_DIST of the
    component center (generous enough for the largest standard symbols).
    """
    _MAX_SNAP_DIST = 12.0  # mm (~470 mils) — covers all standard symbols
    EPS = COORD_EPSILON

    # Build per-sheet wire endpoint set for fast lookup, and list for snapping
    wire_eps_by_sheet: dict[int, set[tuple[float, float]]] = {}
    wire_pts_by_sheet: dict[int, list[tuple[float, float]]] = {}
    for w in wires:
        sheet = w.get("_sheet", 0)
        for x, y in ((w["x1"], w["y1"]), (w["x2"], w["y2"])):
            sx = round(x / EPS) * EPS
            sy = round(y / EPS) * EPS
            wire_eps_by_sheet.setdefault(sheet, set()).add((sx, sy))
            wire_pts_by_sheet.setdefault(sheet, []).append((x, y))

    for comp in components:
        if comp.get("type") in ("power_symbol", "power_flag", "flag"):
            continue
        pins = comp.get("pins")
        if not pins:
            continue

        sheet = comp.get("_sheet", 0)
        wire_set = wire_eps_by_sheet.get(sheet, set())
        wire_list = wire_pts_by_sheet.get(sheet, [])
        cx, cy = comp["x"], comp["y"]

        # Check which pins are already matched to wire endpoints
        unmatched = []
        for pin in pins:
            sk = (round(pin["x"] / EPS) * EPS, round(pin["y"] / EPS) * EPS)
            if sk not in wire_set:
                unmatched.append(pin)

        if not unmatched:
            continue  # all pins already matched

        # Collect nearby wire endpoints (not already claimed by another pin)
        claimed = set()
        for pin in pins:
            if pin not in unmatched:
                claimed.add((round(pin["x"] / EPS) * EPS, round(pin["y"] / EPS) * EPS))

        for pin in unmatched:
            best_dist = _MAX_SNAP_DIST
            best_pt = None
            for wx, wy in wire_list:
                sk = (round(wx / EPS) * EPS, round(wy / EPS) * EPS)
                if sk in claimed:
                    continue
                dx = wx - cx
                dy = wy - cy
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < best_dist:
                    # Prefer wire endpoints roughly in the direction of the
                    # original computed pin (relative to component center)
                    opx, opy = pin["x"] - cx, pin["y"] - cy
                    dot = dx * opx + dy * opy
                    if dot > 0 or (opx == 0 and opy == 0):
                        best_dist = dist
                        best_pt = (wx, wy)
            if best_pt is not None:
                pin["x"] = _snap_mil(best_pt[0])
                pin["y"] = _snap_mil(best_pt[1])
                claimed.add((round(pin["x"] / EPS) * EPS, round(pin["y"] / EPS) * EPS))


def _parse_legacy_single_sheet(path: str) -> tuple:
    """Parse a single legacy .sch file and return raw extracted data.

    Returns: (components, wires, labels, junctions, no_connects, sub_sheet_paths, lib_lines)
    where sub_sheet_paths is a list of resolved Path strings for $Sheet references,
    and lib_lines is a list of raw 'LIBS:...' lines from the file header.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    components = []
    wires = []
    labels = []
    junctions = []
    no_connects = []
    sub_sheet_paths = []

    MIL_TO_MM = _MIL_MM
    base_dir = Path(path).parent

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Component block
        if line == "$Comp":
            comp = {
                "reference": "", "value": "", "lib_id": "", "footprint": "",
                "datasheet": "", "description": "", "mpn": "", "manufacturer": "",
                "digikey": "", "mouser": "", "lcsc": "", "element14": "",
                "x": 0, "y": 0, "angle": 0,
                "mirror_x": False, "mirror_y": False,
                "uuid": "", "in_bom": True, "dnp": False, "on_board": True,
                "pin_uuids": {}, "pins": [], "type": "other",
            }
            i += 1
            while i < len(lines) and lines[i].strip() != "$EndComp":
                cl = lines[i].strip()

                # L Library:Symbol Reference
                if cl.startswith("L "):
                    parts = cl.split()
                    if len(parts) >= 3:
                        comp["lib_id"] = parts[1]
                        comp["reference"] = parts[2]

                # U unit mm_part timestamp
                elif cl.startswith("U "):
                    parts = cl.split()
                    if len(parts) >= 4:
                        comp["uuid"] = parts[3]
                        try:
                            comp["unit"] = int(parts[1])
                        except (ValueError, IndexError):
                            pass

                # P x y
                elif cl.startswith("P "):
                    parts = cl.split()
                    if len(parts) >= 3:
                        comp["x"] = _snap_mil(int(parts[1]) * MIL_TO_MM)
                        comp["y"] = _snap_mil(int(parts[2]) * MIL_TO_MM)

                # F N "value" orientation x y size flags visibility hjustify [font [italic bold]]
                elif cl.startswith("F "):
                    # Parse field: F N "value" ...
                    fm = re.match(r'F\s+(\d+)\s+"([^"]*)"', cl)
                    if fm:
                        field_num = int(fm.group(1))
                        field_val = fm.group(2)
                        if field_num == 0:
                            comp["reference"] = field_val
                        elif field_num == 1:
                            comp["value"] = field_val
                        elif field_num == 2:
                            comp["footprint"] = field_val
                        elif field_num == 3:
                            comp["datasheet"] = field_val
                        # Fields 4+ are custom — try to capture them
                        elif field_num >= 4 and field_val:
                            # Check if the field has a name after the positional data
                            # Format: F N "value" H x y size flags visibility hjustify "FieldName"
                            name_match = re.search(r'"([^"]*)"[^"]*$', cl[fm.end():])
                            fname = name_match.group(1) if name_match else f"Field{field_num}"
                            if name_match:
                                fu = fname.upper()
                                if fu in ("MPN", "MFG PART", "MFGPART", "MANF#",
                                          "MPN#", "PART#", "MANUFACTURER_PART_NUMBER",
                                          "PARTNO", "PART NUMBER", "PART_NUMBER", "PART NO"):
                                    comp["mpn"] = field_val
                                elif fu in ("MANUFACTURER", "MFG", "MANF", "MFR"):
                                    comp["manufacturer"] = field_val
                                elif fu in ("DIGIKEY", "DIGIKEY#", "DIGI-KEY",
                                           "DIGI-KEY PART NUMBER", "DIGI-KEY_PN",
                                           "DIGIKEY PART NUMBER", "DIGIKEY_PART_NUMBER",
                                           "DIGI-KEY PN", "DIGIKEY PART", "DK"):
                                    comp["digikey"] = field_val
                                elif fu in ("MOUSER", "MOUSER#", "MOUSER PART NUMBER",
                                           "MOUSER PART", "MOUSER_PN", "MOUSER PN"):
                                    comp["mouser"] = field_val
                                elif fu in ("LCSC", "LCSC#", "JLC#", "JLCPCB#",
                                           "LCSC PART #", "LCSC PART NUMBER",
                                           "LCSC PART", "LCSCSTKCODE", "LCSCSTOCKCODE",
                                           "JLCPCB", "JLCPCB PART", "JLC"):
                                    comp["lcsc"] = field_val
                                elif fu in ("NEWARK", "NEWARK PART NUMBER", "NEWARK_PN",
                                           "NEWARK PN", "FARNELL", "FARNELL PART NUMBER",
                                           "FARNELL_PN", "FARNELL PN", "ELEMENT14",
                                           "ELEMENT14 PART NUMBER", "ELEMENT14_PN"):
                                    comp["element14"] = field_val
                                elif fu == "DNP":
                                    comp["dnp"] = field_val.strip() not in ("", "0", "false")
                                elif fu in ("NOTE", "NOTES", "COMMENT"):
                                    if any(dnp_kw in field_val.upper() for dnp_kw in ("DNP", "DO NOT POPULATE", "DO NOT PLACE")):
                                        comp["dnp"] = True
                                elif fu in ("DESCRIPTION", "DESC"):
                                    comp["description"] = field_val
                            # Track generic-named fields for positional fallback
                            if re.match(r'^Field\d+$', fname):
                                comp.setdefault("_generic_fields", {})[fname] = field_val

                # Orientation matrix line (after position line)
                # Format: a b c d  (2x2 transform matrix [a b; c d])
                # det = a*d - b*c: +1 = no mirror, -1 = mirrored (X axis)
                elif cl and cl[0].isdigit() and len(cl.split()) == 4:
                    parts = cl.split()
                    try:
                        a, b, c, d = [int(p) for p in parts]
                        det = a * d - b * c
                        if det < 0:
                            comp["mirror_x"] = True
                            # Remove mirror to extract pure rotation
                            c, d = -c, -d
                        if (a, b) == (1, 0):
                            comp["angle"] = 0
                        elif (a, b) == (0, 1):
                            comp["angle"] = 90
                        elif (a, b) == (-1, 0):
                            comp["angle"] = 180
                        elif (a, b) == (0, -1):
                            comp["angle"] = 270
                    except ValueError:
                        pass

                i += 1
            # Positional fallback for generic field names (Field1=manufacturer, Field2=MPN)
            gf = comp.pop("_generic_fields", {})
            if not comp.get("mpn") and gf:
                if "Field2" in gf and gf["Field2"]:
                    comp["mpn"] = gf["Field2"]
                if not comp.get("manufacturer") and "Field1" in gf and gf["Field1"]:
                    comp["manufacturer"] = gf["Field1"]

            # Legacy power symbol detection: #PWR/#FLG refs or library named "power"
            lib_prefix = comp["lib_id"].split(":")[0].lower()
            is_power = (comp["reference"].startswith("#")
                        or lib_prefix == "power"
                        or lib_prefix.endswith("_power"))
            # Check value field for DNP indication
            if not comp["dnp"] and comp["value"].upper() in ("DNP", "DO NOT POPULATE", "DO NOT PLACE", "NP"):
                comp["dnp"] = True
            comp["type"] = classify_component(comp["reference"], comp["lib_id"], comp["value"], is_power, comp.get("footprint", ""))
            components.append(comp)

        # Hierarchical sheet block — extract subsheet filename
        elif line == "$Sheet":
            sheet_file = None
            i += 1
            while i < len(lines) and lines[i].strip() != "$EndSheet":
                sl = lines[i].strip()
                # F1 "filename.sch" size — the sheet filename field
                sm = re.match(r'F1\s+"([^"]+\.sch)"', sl)
                if sm:
                    sheet_file = sm.group(1)
                i += 1
            if sheet_file:
                sub_path = base_dir / sheet_file
                if sub_path.exists():
                    sub_sheet_paths.append(str(sub_path.resolve()))

        # Wire
        elif line == "Wire Wire Line":
            i += 1
            if i < len(lines):
                parts = lines[i].strip().split()
                if len(parts) >= 4:
                    wires.append({
                        "x1": _snap_mil(int(parts[0]) * MIL_TO_MM),
                        "y1": _snap_mil(int(parts[1]) * MIL_TO_MM),
                        "x2": _snap_mil(int(parts[2]) * MIL_TO_MM),
                        "y2": _snap_mil(int(parts[3]) * MIL_TO_MM),
                    })

        # Junction / Connection
        elif line.startswith("Connection ~"):
            parts = line.split()
            if len(parts) >= 4:
                junctions.append({
                    "x": _snap_mil(int(parts[2]) * MIL_TO_MM),
                    "y": _snap_mil(int(parts[3]) * MIL_TO_MM),
                })

        # No-connect
        elif line.startswith("NoConn ~"):
            parts = line.split()
            if len(parts) >= 4:
                no_connects.append({
                    "x": _snap_mil(int(parts[2]) * MIL_TO_MM),
                    "y": _snap_mil(int(parts[3]) * MIL_TO_MM),
                })

        # Labels
        elif line.startswith("Text Label "):
            parts = line.split()
            if len(parts) >= 5:
                x = _snap_mil(int(parts[2]) * MIL_TO_MM)
                y = _snap_mil(int(parts[3]) * MIL_TO_MM)
                # Next line is the label text
                i += 1
                if i < len(lines):
                    name = lines[i].strip()
                    labels.append({"name": name, "type": "label", "x": x, "y": y, "angle": 0})

        elif line.startswith("Text GLabel "):
            parts = line.split()
            if len(parts) >= 5:
                x = _snap_mil(int(parts[2]) * MIL_TO_MM)
                y = _snap_mil(int(parts[3]) * MIL_TO_MM)
                i += 1
                if i < len(lines):
                    name = lines[i].strip()
                    labels.append({"name": name, "type": "global_label", "x": x, "y": y, "angle": 0})

        elif line.startswith("Text HLabel "):
            parts = line.split()
            if len(parts) >= 5:
                x = _snap_mil(int(parts[2]) * MIL_TO_MM)
                y = _snap_mil(int(parts[3]) * MIL_TO_MM)
                i += 1
                if i < len(lines):
                    name = lines[i].strip()
                    labels.append({"name": name, "type": "hierarchical_label", "x": x, "y": y, "angle": 0})

        i += 1

    # Collect LIBS: directives from header for lib resolution
    lib_lines = [l.strip() for l in lines if l.strip().startswith("LIBS:")]

    return components, wires, labels, junctions, no_connects, sub_sheet_paths, lib_lines


def parse_legacy_schematic(path: str) -> dict:
    """Parse a KiCad 5 legacy .sch file and return the same structure as analyze_schematic.

    Legacy format uses line-oriented text with $Comp/$EndComp blocks, coordinates
    in mils (1/1000 inch), and positional field numbering (F0=ref, F1=value, etc.).

    For hierarchical designs, recursively parses all subsheets referenced by
    $Sheet blocks and merges connectivity across sheets.

    Parses .lib files to populate component pin data for pin-to-net mapping
    and signal analysis.
    """
    all_components = []
    all_wires = []
    all_labels = []
    all_junctions = []
    all_no_connects = []
    sheets_parsed = []
    all_sch_lines = {}  # path -> LIBS: lines for lib resolution

    to_parse = [str(Path(path).resolve())]
    parsed = set()

    while to_parse:
        sheet_path = to_parse.pop(0)
        if sheet_path in parsed:
            continue
        parsed.add(sheet_path)

        components, wires, labels, junctions, no_connects, sub_sheets, lib_lines = \
            _parse_legacy_single_sheet(sheet_path)

        all_sch_lines[sheet_path] = lib_lines

        # Tag elements with sheet index to keep coordinate spaces separate
        sheet_idx = len(sheets_parsed)
        for c in components:
            c["_sheet"] = sheet_idx
        for w in wires:
            w["_sheet"] = sheet_idx
        for lbl in labels:
            lbl["_sheet"] = sheet_idx
        for j in junctions:
            j["_sheet"] = sheet_idx

        all_components.extend(components)
        all_wires.extend(wires)
        all_labels.extend(labels)
        all_junctions.extend(junctions)
        all_no_connects.extend(no_connects)
        sheets_parsed.append(sheet_path)

        for sub_path in sub_sheets:
            if sub_path not in parsed:
                to_parse.append(sub_path)

    # Resolve .lib files and parse pin data
    root_path = str(Path(path).resolve())
    lib_symbols = _resolve_legacy_libs(root_path, all_sch_lines)

    # Build a reverse lookup for cache lib names: bare_symbol -> full cache name.
    # Cache libs store "Library_Symbol" while schematics reference bare "Symbol"
    # or "Library:Symbol".  This handles both forms.
    _cache_suffix_map: dict[str, str] = {}
    for sym_name in lib_symbols:
        if "_" in sym_name:
            bare = sym_name.rsplit("_", 1)[-1]
            # Only map if the bare name is unique (avoid ambiguity)
            if bare not in _cache_suffix_map:
                _cache_suffix_map[bare] = sym_name
            else:
                _cache_suffix_map[bare] = None  # ambiguous — don't use
        # Also try full name after last colon-like underscore
        # e.g., "OLIMEX_IC_BL4054B-42TPRN(SOT23-5)" → "BL4054B-42TPRN(SOT23-5)"
        parts = sym_name.split("_")
        if len(parts) >= 3:
            tail = "_".join(parts[2:])
            if tail and tail not in _cache_suffix_map:
                _cache_suffix_map[tail] = sym_name

    # Populate pin positions for each component using lib symbol data.
    # V2 format uses bare symbol names ("MIC5207-BM5"), V4 uses "Library:Symbol".
    # Cache libs use underscores ("Device_C") instead of colons ("Device:C").
    for comp in all_components:
        lib_id = comp.get("lib_id", "")
        sym_def = lib_symbols.get(lib_id)
        if not sym_def and ":" in lib_id:
            # V4: try underscore form (cache lib naming) and bare symbol name
            bare_name = lib_id.split(":", 1)[1]
            sym_def = (lib_symbols.get(lib_id.replace(":", "_"))
                       or lib_symbols.get(bare_name))
        else:
            bare_name = lib_id
        if not sym_def:
            # Bare name lookup via cache suffix map (handles cache libs
            # that store "Library_Symbol" while schematic uses bare "Symbol")
            cache_key = _cache_suffix_map.get(bare_name)
            if cache_key:
                sym_def = lib_symbols.get(cache_key)
        if sym_def:
            comp["pins"] = compute_pin_positions(comp, {lib_id: sym_def})
            # Snap pin positions to mil grid — eliminates floating-point drift
            # from trig-based rotation so pins align with wire endpoints.
            for pin in comp["pins"]:
                pin["x"] = _snap_mil(pin["x"])
                pin["y"] = _snap_mil(pin["y"])

    # KH-016 fix: Snap component pins to nearby wire endpoints when the
    # computed pin positions don't match any wire (caused by KiCad version
    # differences in standard library pin offsets).  For each pin, if no wire
    # endpoint is within COORD_EPSILON, find the closest wire endpoint on the
    # same sheet within a generous radius and snap to it.
    _snap_pins_to_wires(all_components, all_wires)

    # Extract power symbols (preserve _sheet so build_net_map keeps coordinate
    # spaces separate — without it, power symbols from sub-sheets all land in
    # sheet 0's coordinate space and fail to connect to their actual wires).
    power_symbols = []
    for comp in all_components:
        if comp["type"] == "power_symbol":
            ps = {
                "net_name": comp["value"],
                "x": comp["x"],
                "y": comp["y"],
                "lib_id": comp["lib_id"],
            }
            if "_sheet" in comp:
                ps["_sheet"] = comp["_sheet"]
            power_symbols.append(ps)

    # Generate BOM
    bom = generate_bom(all_components)

    # Build nets from wires + labels + power symbols + component pins
    nets = build_net_map(all_components, all_wires, all_labels, power_symbols, all_junctions)

    stats = compute_statistics(all_components, nets, bom, all_wires, all_no_connects)

    # Subcircuit detection (IC + 1-hop neighbors)
    pin_net = build_pin_to_net_map(nets)
    subcircuits = identify_subcircuits(all_components, nets, pin_net=pin_net)

    # Signal path and filter analysis
    signal_analysis = analyze_signal_paths(all_components, nets, lib_symbols, pin_net=pin_net)

    # Design rule analysis
    design_analysis = analyze_design_rules(all_components, nets, all_no_connects, signal_analysis, pin_net=pin_net)

    # Filter to real components (non-power) for annotation check
    real_components = [
        c for c in all_components
        if c["type"] not in ("power_symbol", "power_flag", "flag")
    ]
    annotation_issues = check_annotation_completeness(real_components)

    return {
        "file": str(path),
        "kicad_version": "5 (legacy)",
        "file_version": "4",
        "sheets_parsed": len(sheets_parsed),
        "sheet_files": sheets_parsed,
        "statistics": stats,
        "bom": bom,
        "components": real_components,
        "nets": nets,
        "subcircuits": subcircuits,
        "signal_analysis": signal_analysis,
        "design_analysis": design_analysis,
        "labels": all_labels,
        "no_connects": all_no_connects,
        "power_symbols": power_symbols,
        "annotation_issues": annotation_issues,
    }


def parse_single_sheet(path: str, instance_uuid: str = "",
                       symbol_instances: dict | None = None) -> tuple:
    """Parse a single .kicad_sch file and return raw extracted data.

    If instance_uuid is provided, remap component references from the
    (instances) block for this specific sheet instance.

    If symbol_instances is provided, use it as fallback for remapping when
    inline (instances) blocks are absent.

    Returns: (root, components, wires, labels, junctions, no_connects,
              sub_sheet_paths, lib_symbols, text_annotations, bus_elements, title_block)
    """
    root = parse_file(path)
    lib_symbols = extract_lib_symbols(root)
    components = extract_components(root, lib_symbols, instance_uuid=instance_uuid,
                                    symbol_instances=symbol_instances)
    wires = extract_wires(root)
    labels = extract_labels(root)
    junctions = extract_junctions(root)
    no_connects = extract_no_connects(root)
    text_annotations = extract_text_annotations(root)
    bus_elements = extract_bus_elements(root)
    title_block = extract_title_block(root)

    # Find sub-sheet references, including UUIDs for multi-instance support.
    # Also extract sheet pin stubs — these are the parent-side endpoints of
    # hierarchical connections.  Each (pin "NAME" direction (at X Y ANGLE))
    # inside a (sheet) block acts like a hierarchical_label at that position,
    # connecting the parent sheet's wires to the child sheet's matching
    # hierarchical_label via the label name union in build_net_map().
    sub_sheet_paths = []
    base_dir = Path(path).parent
    for sheet in find_all(root, "sheet"):
        # Sheet file property name varies by KiCad version:
        # KiCad 6+: (property "Sheetfile" "filename.kicad_sch")
        # KiCad 7+: (property "Sheet file" "filename.kicad_sch")
        sheet_file = get_property(sheet, "Sheetfile") or get_property(sheet, "Sheet file")
        if sheet_file:
            sub_path = base_dir / sheet_file
            if sub_path.exists():
                sheet_uuid = get_value(sheet, "uuid") or ""
                sub_sheet_paths.append((str(sub_path), sheet_uuid))

        # Extract sheet pin stubs as hierarchical labels so parent-sheet wires
        # connecting to the sheet symbol get unioned with the child sheet's nets.
        # Tag with _sheet_uuid so the traversal loop can namespace them per
        # instance (KH-026: multi-instance hierarchical net isolation).
        sheet_uuid_for_pins = get_value(sheet, "uuid") or ""
        for pin in find_all(sheet, "pin"):
            if len(pin) < 2:
                continue
            pin_name = pin[1]
            at = get_at(pin)
            if at:
                labels.append({
                    "name": pin_name,
                    "type": "hierarchical_label",
                    "x": round(at[0], 4),
                    "y": round(at[1], 4),
                    "angle": at[2] if len(at) > 2 else 0,
                    "_sheet_uuid": sheet_uuid_for_pins,
                })

    return (root, components, wires, labels, junctions, no_connects,
            sub_sheet_paths, lib_symbols, text_annotations, bus_elements, title_block)


def analyze_connectivity(components: list[dict], nets: dict, no_connects: list[dict]) -> dict:
    """Analyze the connectivity graph for potential issues.

    Returns a dict with:
    - unconnected_pins: pins not on any net (and not marked no-connect)
    - single_pin_nets: nets with only one pin (likely unfinished connections)
    - multi_driver_nets: nets with multiple output/bidirectional drivers
    - power_net_summary: per power rail, which components connect
    """
    EPSILON = COORD_EPSILON

    # Build set of no-connect positions for quick lookup
    nc_positions = set()
    for nc in no_connects:
        nc_positions.add((round(nc["x"] / EPSILON) * EPSILON,
                          round(nc["y"] / EPSILON) * EPSILON))

    # Build set of all pins that appear in any net
    connected_pins = set()
    for net_info in nets.values():
        for p in net_info["pins"]:
            connected_pins.add((p["component"], p["pin_number"]))

    # Find unconnected pins
    unconnected_pins = []
    for comp in components:
        if comp["type"] in ("power_symbol", "power_flag", "flag"):
            continue
        for pin in comp.get("pins", []):
            pin_key = (comp["reference"], pin["number"])
            if pin_key not in connected_pins:
                # Check if there's a no-connect marker at this pin
                pin_pos = (round(pin["x"] / EPSILON) * EPSILON,
                           round(pin["y"] / EPSILON) * EPSILON)
                if pin_pos not in nc_positions:
                    unconnected_pins.append({
                        "component": comp["reference"],
                        "pin_number": pin["number"],
                        "pin_name": pin["name"],
                        "pin_type": pin["type"],
                    })

    # Find single-pin nets (likely unfinished wiring)
    single_pin_nets = []
    for net_name, net_info in nets.items():
        if len(net_info["pins"]) == 1 and not net_name.startswith("__unnamed_"):
            single_pin_nets.append({
                "net": net_name,
                "pin": net_info["pins"][0],
            })

    # Find multi-driver nets (multiple outputs driving the same net)
    # Exclude power flags (#FLG, #PWR) — they're virtual, not real drivers
    multi_driver_nets = []
    output_types = {"output", "tri_state", "power_out"}
    for net_name, net_info in nets.items():
        drivers = [p for p in net_info["pins"]
                   if p["pin_type"] in output_types
                   and not p["component"].startswith("#")]
        if len(drivers) > 1:
            multi_driver_nets.append({
                "net": net_name,
                "drivers": drivers,
            })

    # Power net summary — for each named power rail, which real components connect
    power_net_summary = {}
    for net_name, net_info in nets.items():
        if net_name.startswith("__unnamed_"):
            continue
        is_power = any(p["pin_type"] in ("power_in", "power_out") for p in net_info["pins"])
        if is_power or net_name in ("GND", "VCC", "VDD", "+3V3", "+3.3V", "+5V", "+12V", "VBUS"):
            real_components = sorted(set(
                p["component"] for p in net_info["pins"]
                if not p["component"].startswith("#")
            ))
            power_net_summary[net_name] = {
                "pin_count": len([p for p in net_info["pins"] if not p["component"].startswith("#")]),
                "components": real_components,
            }

    return {
        "unconnected_pins": unconnected_pins,
        "single_pin_nets": single_pin_nets,
        "multi_driver_nets": multi_driver_nets,
        "power_net_summary": power_net_summary,
    }


def analyze_design_rules(components: list[dict], nets: dict, no_connects: list[dict],
                         results_in: dict | None = None, pin_net: dict | None = None) -> dict:
    """Deep EE analysis: power domains, bus protocols, differential pairs, ERC checks.

    Returns:
    - power_domains: which ICs are on which voltage rails
    - cross_domain_signals: signals crossing between different power domains
    - bus_analysis: I2C pull-ups, SPI CS assignments, UART TX/RX pairs
    - differential_pairs: USB, CAN, LVDS with termination checks
    - erc_warnings: input-to-input, output-to-output, undriven inputs
    - net_classification: every net tagged by type (power/gnd/clock/data/analog/etc.)
    """
    if results_in is None:
        results_in = {}
    if pin_net is None:
        pin_net = build_pin_to_net_map(nets)
    comp_lookup = {c["reference"]: c for c in components}
    parsed_values = {}
    for c in components:
        pv = parse_value(c.get("value", ""))
        if pv is not None:
            parsed_values[c["reference"]] = pv

    # Build set of known power rail names from nets that came from power symbols
    known_power_rails = set()
    for net_name, net_info in nets.items():
        for p in net_info.get("pins", []):
            if p["component"].startswith("#"):
                known_power_rails.add(net_name)
                break

    def is_ground(net_name: str | None) -> bool:
        return _is_ground_name(net_name)

    def is_power_net(net_name: str | None) -> bool:
        return _is_power_net_name(net_name, known_power_rails)

    # ---- Net Classification ----
    net_classes = {}
    for net_name, net_info in nets.items():
        if net_name.startswith("__unnamed_"):
            # Classify unnamed nets by pin types
            pin_types = set(p["pin_type"] for p in net_info["pins"])
            has_power = bool(pin_types & {"power_in", "power_out"})
            if has_power:
                net_classes[net_name] = "power_internal"
            else:
                net_classes[net_name] = "signal"
            continue

        nu = net_name.upper()
        if is_ground(net_name):
            net_classes[net_name] = "ground"
        elif is_power_net(net_name):
            net_classes[net_name] = "power"
        elif any(kw in nu for kw in ("SCL", "SCK", "CLK", "MCLK", "SCLK", "XTAL", "OSC")):
            net_classes[net_name] = "clock"
        elif any(kw in nu for kw in ("SDA", "MOSI", "MISO", "SDI", "SDO", "UART", "TX", "RX")):
            net_classes[net_name] = "data"
        elif any(kw in nu for kw in ("USB", "CAN", "LVDS", "ETH")):
            net_classes[net_name] = "high_speed"
        elif any(kw in nu for kw in ("ADC", "AIN", "VREF", "VSENSE", "ISENSE")):
            net_classes[net_name] = "analog"
        elif any(kw in nu for kw in ("RESET", "NRST", "RST", "EN", "ENABLE")):
            net_classes[net_name] = "control"
        elif any(kw in nu for kw in ("CS", "SS", "NSS", "CE", "SEL")):
            # KH-097: Exclude video sync signals (CSYNC, HSYNC, VSYNC)
            if any(sync in nu for sync in ("CSYNC", "HSYNC", "VSYNC", "SYNC")):
                net_classes[net_name] = "signal"
            else:
                net_classes[net_name] = "chip_select"
        elif any(kw in nu for kw in ("INT", "IRQ", "ALERT", "DRDY")):
            net_classes[net_name] = "interrupt"
        elif any(kw in nu for kw in _OUTPUT_DRIVE_KEYWORDS):
            net_classes[net_name] = "output_drive"
        elif any(kw in nu for kw in ("SWD", "SWCLK", "SWDIO", "SWO", "JTAG", "TCK", "TMS", "TDI", "TDO")):
            net_classes[net_name] = "debug"
        elif any(kw in nu for kw in ("BOOT", "TEST")):
            net_classes[net_name] = "config"
        else:
            net_classes[net_name] = "signal"

    # ---- Power Domain Mapping ----
    # For each IC, determine which power rails it connects to
    # Track IO-level reference pins (VDDIO, VIO) separately from internal supplies
    # (VCC, VDD) — for cross-domain analysis, the IO rail determines signal levels
    _io_pin_names = {"VDDIO", "VIO", "VCCA", "VCCB", "VREF", "VLOGIC",
                     "DVDD", "DVCC", "IOVDD", "IOVCC"}
    _sense_pin_names = {"IN+", "IN-", "INP", "INN", "SENSE", "VSENSE", "SNS",
                        "CSP", "CSN", "CS+", "CS-", "ISENSE", "IMON", "IOUT",
                        "SEN", "VS+", "VS-"}
    power_domains = {}
    ics = [c for c in components if c["type"] == "ic"]
    for ic in ics:
        ref = ic["reference"]
        rails = set()
        io_rails = set()
        for pin in ic.get("pins", []):
            net_name, _ = pin_net.get((ref, pin["number"]), (None, None))
            if not net_name:
                continue
            pname_upper = pin["name"].upper()
            if pname_upper in _sense_pin_names:
                continue
            is_pwr = is_power_net(net_name) and not is_ground(net_name)
            is_named_pwr = pname_upper in ("VCC", "VDD", "AVCC", "AVDD", "VBUS",
                                           "VIN", "VOUT", "VDDIO", "VIO", "VCCA",
                                           "VCCB", "VREF", "VLOGIC", "DVDD", "DVCC",
                                           "IOVDD", "IOVCC", "VCCREG", "VREG")
            if is_pwr or is_named_pwr:
                rails.add(net_name)
                if pname_upper in _io_pin_names:
                    io_rails.add(net_name)
        if rails:
            power_domains[ref] = {
                "value": ic["value"],
                "power_rails": sorted(rails),
                "io_rails": sorted(io_rails) if io_rails else None,
            }

    # Fallback for legacy files where pin_net may be incomplete:
    # Match IC power pin NAMES (VCC, VDD, etc.) against known_power_rails by string.
    _pwr_pin_to_rail = {"VCC", "VDD", "AVCC", "AVDD", "DVCC", "DVDD",
                        "VDDIO", "VIO", "VCCA", "VCCB", "VBUS"}
    for ic in ics:
        ref = ic["reference"]
        if ref in power_domains:
            continue
        rails = set()
        for pin in ic.get("pins", []):
            pname = pin.get("name", "").upper()
            if pname not in _pwr_pin_to_rail:
                continue
            # Find a known power rail whose name matches this pin name
            for rail in known_power_rails:
                ru = rail.upper()
                if pname == ru or pname in ru or ru.startswith(pname):
                    rails.add(rail)
        if rails:
            power_domains[ref] = {
                "value": ic["value"],
                "power_rails": sorted(rails),
                "io_rails": None,
            }

    # Group ICs by power domain
    domain_groups = {}
    for ref, info in power_domains.items():
        for rail in info["power_rails"]:
            domain_groups.setdefault(rail, []).append(ref)

    # ---- Cross-Domain Signal Detection ----
    # Find signal nets that connect ICs from different power domains
    cross_domain = []
    # Build set of ESD protection IC references for cross-domain filtering
    esd_ic_refs = set()
    for pd in results_in.get("protection_devices", []):
        if pd.get("type") == "esd_ic":
            esd_ic_refs.add(pd["ref"])

    # Detect level translator ICs — these bridge domains intentionally so
    # signals through them don't need additional level shifting.
    level_translator_keywords = (
        "leveltranslator", "level_translator", "levelshift", "level_shift",
        "txb0", "txs0", "tca9", "lsf0", "sn74lvc", "sn74avc",
        "sn74cb3", "sn74cbt", "nlsx", "nts0", "fxl", "adg320",
        "max395", "gtl2", "pca960", "tca641",
    )
    level_translator_desc_keywords = (
        "level translator", "level shifter", "level-shifting",
        "voltage translator", "voltage level",
    )
    level_translator_refs = set()
    for ic in components:
        if ic["type"] != "ic":
            continue
        val_low = ic.get("value", "").lower()
        lib_low = ic.get("lib_id", "").lower()
        desc_low = ic.get("description", "").lower()
        kw_low = ic.get("keywords", "").lower()
        if (any(k in val_low or k in lib_low for k in level_translator_keywords) or
                any(k in desc_low or k in kw_low for k in level_translator_desc_keywords)):
            level_translator_refs.add(ic["reference"])
    for net_name, net_info in nets.items():
        if is_power_net(net_name) or is_ground(net_name):
            continue
        # Find all ICs on this net
        ic_refs = set()
        for p in net_info["pins"]:
            if p["component"] in power_domains and not p["component"].startswith("#"):
                ic_refs.add(p["component"])
        if len(ic_refs) < 2:
            continue

        # Check if they're on different power domains
        def _get_power_domains(refs: set) -> set:
            domains = set()
            for r in refs:
                for rail in power_domains.get(r, {}).get("power_rails", []):
                    if not is_ground(rail):
                        domains.add(rail)
            return domains

        def _get_io_domains(refs: set) -> set:
            """Get I/O-level domains for cross-domain comparison.

            When an IC has a dedicated IO-level pin (VDDIO, VIO, etc.),
            use that rail for signal-level comparison instead of all power
            rails.  This avoids false positives where internal supplies
            (VCC charge pump, analog VDD) differ but the actual I/O
            voltage matches the other IC.
            """
            domains = set()
            for r in refs:
                info = power_domains.get(r, {})
                io = info.get("io_rails")
                if io:
                    for rail in io:
                        domains.add(rail)
                else:
                    # No dedicated IO pin — use all power rails
                    for rail in info.get("power_rails", []):
                        if not is_ground(rail):
                            domains.add(rail)
            return domains

        domains_on_net = _get_power_domains(ic_refs)
        if len(domains_on_net) > 1:
            # Don't flag as needing level shifter when the only cross-domain
            # connection is through an ESD/protection IC — those clamp voltage
            # but don't change signal levels (e.g., USBLC6 on USB D+/D-)
            non_esd_refs = ic_refs - esd_ic_refs
            non_esd_domains = _get_power_domains(non_esd_refs)

            # Check if a level translator is on this net — if so, it already
            # handles the voltage translation
            translators_on_net = ic_refs & level_translator_refs
            has_translator = len(translators_on_net) > 0

            if len(non_esd_domains) > 1:
                if has_translator:
                    # Level translator present — shifting is handled
                    needs_shifter = False
                else:
                    # Use IO-level domains for the level shifter decision.
                    # Check pairwise: every pair of ICs must share at least one
                    # common IO rail. A multi-rail SoC (e.g., +3.3V + VDDA)
                    # connecting to a simple IC on +3.3V shares +3.3V, so no
                    # level shifter is needed despite different rail counts.
                    # Also treat rails at the same voltage as equivalent (e.g.,
                    # +3V3 and VCC_3V3 are both 3.3V).
                    def _rails_voltage_compatible(rails_a: set, rails_b: set) -> bool:
                        """Check if two sets of rails share a rail or voltage."""
                        if rails_a & rails_b:
                            return True
                        # Parse voltages from net names and check overlap
                        va = {_parse_voltage_from_net_name(r) for r in rails_a} - {None}
                        vb = {_parse_voltage_from_net_name(r) for r in rails_b} - {None}
                        return bool(va & vb)

                    functional_refs = non_esd_refs - level_translator_refs
                    ic_list = sorted(functional_refs) if functional_refs else sorted(non_esd_refs)
                    needs_shifter = False
                    for i_idx in range(len(ic_list)):
                        for j_idx in range(i_idx + 1, len(ic_list)):
                            a_io = _get_io_domains({ic_list[i_idx]})
                            b_io = _get_io_domains({ic_list[j_idx]})
                            if not _rails_voltage_compatible(a_io, b_io):
                                needs_shifter = True
                                break
                        if needs_shifter:
                            break
            else:
                needs_shifter = False
            entry = {
                "net": net_name,
                "ics": sorted(ic_refs),
                "power_domains": sorted(domains_on_net),
                "needs_level_shifter": needs_shifter,
            }
            if has_translator:
                entry["level_translators"] = sorted(translators_on_net)
            cross_domain.append(entry)

    # ---- Bus Protocol Analysis ----
    buses = {"i2c": [], "spi": [], "uart": [], "can": []}

    # I2C: look for SDA/SCL net pairs by net name
    i2c_nets = {}
    for net_name in nets:
        nu = net_name.upper()
        if "SDA" in nu or "SCL" in nu or "I2C" in nu:
            # Skip SPI signals (MISO contains no I2C keywords, but SCLK/SCK match SCL)
            # Skip I2S signals (I2S0_RX_SDA etc. contain SDA as substring)
            # KH-086: Also exclude MOSI/MISO nets (SPI data lines on dual-function ICs)
            if "SCLK" in nu or nu.endswith("SCK") or "SPI" in nu or "MOSI" in nu or "MISO" in nu:
                continue
            if "I2S" in nu:
                continue
            bus_id = nu.replace("SDA", "").replace("SCL", "").replace("I2C", "").replace("_", "").strip()
            i2c_nets.setdefault(bus_id, {})[nu] = net_name

    # Generate I2C bus entries from net-name matches
    i2c_seen_nets = set()
    for bus_id, net_map in i2c_nets.items():
        for nu_key, net_name in net_map.items():
            if net_name in i2c_seen_nets:
                continue
            i2c_seen_nets.add(net_name)
            net_info = nets.get(net_name, {})
            line = "SDA" if "SDA" in nu_key else "SCL"
            devices = [p["component"] for p in net_info.get("pins", [])
                       if comp_lookup.get(p["component"], {}).get("type") == "ic"]
            # Find pull-up resistors
            pullups = []
            for p in net_info.get("pins", []):
                comp = comp_lookup.get(p["component"])
                if comp and comp["type"] == "resistor":
                    r_val = parsed_values.get(p["component"])
                    if r_val:
                        other_pin = "1" if p["pin_number"] == "2" else "2"
                        other_net, _ = pin_net.get((p["component"], other_pin), (None, None))
                        if other_net and is_power_net(other_net) and not is_ground(other_net):
                            pullups.append({
                                "ref": p["component"],
                                "value": comp["value"],
                                "ohms": r_val,
                                "to_rail": other_net,
                            })
            if devices:  # Skip connector-only routing with no ICs
                buses["i2c"].append({
                    "net": net_name,
                    "line": line,
                    "devices": devices,
                    "pull_ups": pullups,
                    "has_pull_up": len(pullups) > 0,
                })

    # Also detect I2C from pin names (for nets without SDA/SCL in their name)
    for net_name, net_info in nets.items():
        if net_name in i2c_seen_nets:
            continue  # Already found by net name
        # KH-086: Exclude SPI nets — sensors with dual-function SDA/SCL pin names
        nn_upper = net_name.upper()
        if "SPI" in nn_upper or "MOSI" in nn_upper or "MISO" in nn_upper:
            continue
        sda_pins = [p for p in net_info["pins"]
                    if "SDA" in p.get("pin_name", "").upper()
                    and "I2S" not in p.get("pin_name", "").upper()]
        # Exclude SPI clock pins (SCLK, SCK) which contain "SCL" as substring
        # Exclude I2S pins which may contain SCL as substring
        scl_pins = [p for p in net_info["pins"]
                    if "SCL" in p.get("pin_name", "").upper()
                    and "SCLK" not in p.get("pin_name", "").upper()
                    and "I2S" not in p.get("pin_name", "").upper()
                    and p.get("pin_name", "").upper() not in ("SCK",)]
        if sda_pins or scl_pins:
            # Find pull-up resistors on this net
            pullups = []
            for p in net_info["pins"]:
                comp = comp_lookup.get(p["component"])
                if comp and comp["type"] == "resistor":
                    r_val = parsed_values.get(p["component"])
                    if r_val:
                        # Check if other end goes to a power rail
                        other_pin = "1" if p["pin_number"] == "2" else "2"
                        other_net, _ = pin_net.get((p["component"], other_pin), (None, None))
                        if other_net and is_power_net(other_net) and not is_ground(other_net):
                            pullups.append({
                                "ref": p["component"],
                                "value": comp["value"],
                                "ohms": r_val,
                                "to_rail": other_net,
                            })

            bus_type = "SDA" if sda_pins else "SCL"
            devices = [p["component"] for p in net_info["pins"]
                       if comp_lookup.get(p["component"], {}).get("type") == "ic"]

            if devices:  # Skip connector-only routing with no ICs
                buses["i2c"].append({
                    "net": net_name,
                    "line": bus_type,
                    "devices": devices,
                    "pull_ups": pullups,
                    "has_pull_up": len(pullups) > 0,
                })

    # SPI: look for MOSI/MISO/SCK/CS patterns (and newer COPI/CIPO/SDI/SDO)
    # Build a set of nets already identified as SPI to guard I2C pin-name
    # detection against false positives (SCL substring matches on SCLK).
    _spi_net_kw = ("MOSI", "MISO", "SCK", "SCLK", "COPI", "CIPO", "SDI", "SDO")
    _spi_canon = {  # normalize alternative names to canonical SPI signals
        "COPI": "MOSI", "SDO": "MOSI", "SDI": "MISO", "CIPO": "MISO",
        "SCLK": "SCK",
    }
    spi_signals = {}
    spi_nets: set[str] = set()
    for net_name, net_info in nets.items():
        nu = net_name.upper()
        for kw in _spi_net_kw:
            if kw in nu:
                canon = _spi_canon.get(kw, kw)
                bus_id = nu.replace(kw, "").replace("_", "").strip() or "0"
                spi_signals.setdefault(bus_id, {})[canon] = {
                    "net": net_name,
                    "devices": [p["component"] for p in net_info["pins"]
                                if comp_lookup.get(p["component"], {}).get("type") == "ic"],
                }
                spi_nets.add(net_name)
        # Also check pin names
        for p in net_info["pins"]:
            pn = p.get("pin_name", "").upper()
            for kw in _spi_net_kw:
                if pn == kw:
                    canon = _spi_canon.get(kw, kw)
                    bus_id = "pin_" + p["component"]
                    spi_signals.setdefault(bus_id, {})[canon] = {
                        "net": net_name,
                        "devices": [pp["component"] for pp in net_info["pins"]
                                    if comp_lookup.get(pp["component"], {}).get("type") == "ic"],
                    }
                    spi_nets.add(net_name)

    for bus_id, signals in spi_signals.items():
        if len(signals) >= 2:  # At least 2 SPI signals to count as a bus
            # Skip if no ICs on any signal net (connector-only routing)
            has_ic = any(s.get("devices") for s in signals.values())
            if not has_ic:
                continue
            buses["spi"].append({
                "bus_id": bus_id,
                "signals": signals,
            })

    # UART: look for TX/RX pairs (exclude other protocols that happen to contain TX/RX)
    _uart_exclude = ("CAN", "SPI", "I2C", "MOSI", "MISO", "SCL", "SDA",
                     "RMII", "MII", "EMAC", "ENET", "ETH",
                     "PCIE", "PCI_", "HDMI", "LVDS", "MIPI",
                     "CLK", "CLOCK", "USB_D", "USBDM", "USBDP", "I2S")
    uart_nets = {}
    for net_name, net_info in nets.items():
        nu = net_name.upper()
        # Skip nets that belong to other bus types
        if any(kw in nu for kw in _uart_exclude):
            continue
        if any(kw in nu for kw in ("UART", "TX", "RX", "TXD", "RXD")):
            # Identify which devices connect
            devices = [p["component"] for p in net_info["pins"]
                       if comp_lookup.get(p["component"], {}).get("type") == "ic"]
            uart_nets[net_name] = {
                "net": net_name,
                "devices": devices,
                "pin_count": len(net_info["pins"]),
            }
    if uart_nets:
        buses["uart"] = list(uart_nets.values())

    # SDIO/SD/eMMC: look for CLK + CMD + D0 minimum
    _sdio_prefixes = ("SDIO", "SD_", "SD1_", "SD2_", "EMMC", "MMC", "WL_SDIO")
    sdio_signals = {}
    for net_name in nets:
        nu = net_name.upper()
        # Check if net matches an SDIO-related pattern
        matched_prefix = None
        for pfx in _sdio_prefixes:
            if pfx in nu:
                matched_prefix = pfx
                break
        if not matched_prefix:
            # Also match bare SD signal patterns like SDCLK, SDCMD
            if nu.startswith("SD") and any(nu.endswith(sig) for sig in ("CLK", "CMD", "D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7")):
                matched_prefix = "SD"
            else:
                continue

        # Classify the signal type
        sig_type = None
        if "CLK" in nu:
            sig_type = "CLK"
        elif "CMD" in nu:
            sig_type = "CMD"
        else:
            # Match data lines D0-D7
            dm = re.search(r'D(\d)', nu)
            if dm:
                sig_type = f"D{dm.group(1)}"

        if sig_type:
            # Group by bus prefix for multi-bus designs
            bus_key = matched_prefix.rstrip("_")
            sdio_signals.setdefault(bus_key, {})[sig_type] = net_name

    # Build SDIO bus entries
    for bus_key, sigs in sdio_signals.items():
        if "CLK" not in sigs or "CMD" not in sigs or "D0" not in sigs:
            continue
        # Count data lines
        data_lines = sorted(k for k in sigs if k.startswith("D") and k[1:].isdigit())
        bus_width = len(data_lines)

        # Check for pull-ups on CMD and data lines
        pullup_nets = []
        for sig_name in ["CMD"] + data_lines:
            net_name = sigs.get(sig_name)
            if not net_name or net_name not in nets:
                continue
            for p in nets[net_name]["pins"]:
                comp = comp_lookup.get(p["component"])
                if comp and comp["type"] == "resistor":
                    r_n1, _ = pin_net.get((p["component"], "1"), (None, None))
                    r_n2, _ = pin_net.get((p["component"], "2"), (None, None))
                    other = r_n2 if r_n1 == net_name else r_n1
                    if other and is_power_net(other):
                        pullup_nets.append(sig_name)
                        break

        # Find connected devices
        all_devices = set()
        for sig_name, net_name in sigs.items():
            if net_name in nets:
                for p in nets[net_name]["pins"]:
                    if comp_lookup.get(p["component"], {}).get("type") == "ic":
                        all_devices.add(p["component"])

        buses.setdefault("sdio", []).append({
            "bus_id": bus_key,
            "bus_width": bus_width,
            "signals": {sig: net for sig, net in sigs.items()},
            "devices": sorted(all_devices),
            "has_pullups": len(pullup_nets) > 0,
            "pullup_signals": pullup_nets,
        })

    # CAN: look for CAN_TX/CAN_RX nets, CANH/CANL, or CAN transceiver ICs
    can_keywords = ("can_tx", "can_rx", "cantx", "canrx", "canh", "canl", "can_h", "can_l")
    # SN65HVD2xx/10xx are CAN; SN65HVD7x are RS-485 — use specific prefixes
    can_transceiver_kw = ("sn65hvd2", "sn65hvd10", "mcp2551", "mcp2562", "mcp251",
                          "tja10", "tja11", "iso1050", "max3051", "ata6561",
                          "mcp2561", "iso1042")
    can_nets_found = {}
    for net_name, net_info in nets.items():
        nu = net_name.upper()
        if any(kw in nu.lower() for kw in can_keywords):
            devices = [p["component"] for p in net_info["pins"]
                       if comp_lookup.get(p["component"], {}).get("type") == "ic"]
            can_nets_found[net_name] = {"net": net_name, "devices": devices}
    # Also detect by CAN transceiver IC presence — add transceiver info to bus
    for comp in components:
        if comp["type"] != "ic":
            continue
        val = comp.get("value", "").lower()
        lib = comp.get("lib_id", "").lower()
        if any(k in val or k in lib for k in can_transceiver_kw):
            if not can_nets_found:
                # No CAN nets found by name — add a placeholder entry
                can_nets_found["__can_transceiver__"] = {
                    "net": "CAN",
                    "transceiver": comp["reference"],
                    "devices": [comp["reference"]],
                }
    if can_nets_found:
        buses["can"] = list(can_nets_found.values())

    # RS-485/RS-422: detect transceiver ICs by part number
    _rs485_kw = ("max485", "max3485", "max13485", "max14840", "max22500",
                 "sn65hvd7", "sn65hvd3", "sn75176", "sn75lbc",
                 "thvd14", "thvd15", "thvd16",
                 "isl317", "isl318", "isl319",
                 "sp3485", "sp3481", "sp3082", "sp485",
                 "adm485", "adm2485", "adm2587", "adm3485",
                 "ltc285", "ltc248", "ltc249",
                 "st3485", "st485")
    for comp in components:
        if comp["type"] != "ic":
            continue
        val_lib = (comp.get("value", "") + " " + comp.get("lib_id", "")).lower()
        if not any(k in val_lib for k in _rs485_kw):
            continue
        ref = comp["reference"]
        # Map known pin functions
        a_net = b_net = di_net = ro_net = de_net = re_net = None
        for pin in comp.get("pins", []):
            pn = pin.get("name", "").upper()
            net_name, _ = pin_net.get((ref, pin["number"]), (None, None))
            if not net_name:
                continue
            if pn in ("A", "A/Y"):
                a_net = net_name
            elif pn in ("B", "B/Z"):
                b_net = net_name
            elif pn in ("DI", "D", "TXD", "DIN"):
                di_net = net_name
            elif pn in ("RO", "R", "RXD", "DOUT"):
                ro_net = net_name
            elif pn in ("DE",):
                de_net = net_name
            elif pn in ("RE", "~RE", "~{RE}", "/RE"):
                re_net = net_name
        entry = {
            "transceiver": ref,
            "value": comp.get("value", ""),
        }
        if a_net:
            entry["a_net"] = a_net
        if b_net:
            entry["b_net"] = b_net
        if di_net:
            entry["di_net"] = di_net
        if ro_net:
            entry["ro_net"] = ro_net
        if de_net:
            entry["de_net"] = de_net
        if re_net:
            entry["re_net"] = re_net
        buses.setdefault("rs485", []).append(entry)

    # SPI enrichment: add chip_select count and bus_mode
    for spi_entry in buses.get("spi", []):
        sigs = spi_entry.get("signals", {})
        # Count CS/SS nets for this bus
        cs_nets = []
        bus_id = spi_entry.get("bus_id", "")
        for net_name in nets:
            nu = net_name.upper()
            if any(kw in nu for kw in ("CS", "SS", "NSS", "SPI_CS", "SPI_SS")):
                if bus_id in ("0", "") or bus_id in nu.replace("_", ""):
                    cs_nets.append(net_name)
        spi_entry["chip_select_count"] = len(cs_nets)
        has_mosi = "MOSI" in sigs
        has_miso = "MISO" in sigs
        if has_mosi and has_miso:
            spi_entry["bus_mode"] = "full_duplex"
        elif has_mosi or has_miso:
            spi_entry["bus_mode"] = "half_duplex"

    # ---- Differential Pair Detection ----
    diff_pairs = []

    # Suffix pair table: (positive_suffix, negative_suffix) → protocol guess
    _diff_suffix_pairs = [
        # USB
        ("_DP", "_DM"), ("_D+", "_D-"), ("_D_P", "_D_N"), ("DP", "DM"),
        # Generic differential
        ("_P", "_N"), ("+", "-"),
        # LVDS / Ethernet
        ("_TX+", "_TX-"), ("_RX+", "_RX-"),
        ("_TXP", "_TXN"), ("_RXP", "_RXN"),
        ("_TD+", "_TD-"), ("_RD+", "_RD-"),
        ("_TDP", "_TDN"), ("_RDP", "_RDN"),
    ]

    def _guess_diff_protocol(net_name: str) -> str:
        """Guess the protocol from a differential pair net name."""
        nu = net_name.upper()
        if "USB" in nu:
            return "USB"
        if "LVDS" in nu:
            return "LVDS"
        if "ETH" in nu or "MDIO" in nu or "RGMII" in nu or "SGMII" in nu:
            return "Ethernet"
        if "HDMI" in nu or "TMDS" in nu:
            return "HDMI"
        if "MIPI" in nu or "DSI" in nu or "CSI" in nu:
            return "MIPI"
        if "PCIE" in nu or "PCI" in nu:
            return "PCIe"
        if "SATA" in nu:
            return "SATA"
        if "CAN" in nu:
            return "CAN"
        if "RS485" in nu or "RS-485" in nu:
            return "RS-485"
        return "differential"

    # Net-name-based detection: find matching suffix pairs
    net_names_upper = {n.upper(): n for n in nets}
    found_pairs: set[tuple[str, str]] = set()  # track to avoid duplicates

    for pos_sfx, neg_sfx in _diff_suffix_pairs:
        for nu, real_name in net_names_upper.items():
            if nu.endswith(pos_sfx.upper()):
                base = nu[:-len(pos_sfx)]
                neg_candidate = base + neg_sfx.upper()
                if neg_candidate in net_names_upper:
                    pos_real = real_name
                    neg_real = net_names_upper[neg_candidate]
                    pair_key = (min(pos_real, neg_real), max(pos_real, neg_real))
                    if pair_key in found_pairs:
                        continue
                    # Skip power/ground nets (V+/V-, IN+/IN- on power rails)
                    if _is_power_net_name(pos_real) or _is_power_net_name(neg_real):
                        continue
                    if _is_ground_name(pos_real) or _is_ground_name(neg_real):
                        continue
                    found_pairs.add(pair_key)

                    protocol = _guess_diff_protocol(pos_real)
                    entry: dict = {
                        "type": protocol,
                        "positive": pos_real,
                        "negative": neg_real,
                    }

                    # Find shared ICs (connected to both nets)
                    pos_comps = {p["component"] for p in nets[pos_real]["pins"]
                                 if not p["component"].startswith("#")}
                    neg_comps = {p["component"] for p in nets[neg_real]["pins"]
                                 if not p["component"].startswith("#")}
                    shared = pos_comps & neg_comps
                    if shared:
                        entry["shared_ics"] = sorted(shared)

                    # Check for ESD protection
                    esd_chips = [c for c in shared
                                 if comp_lookup.get(c, {}).get("type") == "ic"]
                    entry["has_esd"] = len(esd_chips) > 0
                    if esd_chips:
                        entry["esd_protection"] = esd_chips

                    # CAN-specific: check for termination resistor
                    if protocol == "CAN":
                        term_resistors = []
                        for c in components:
                            if c["type"] == "resistor":
                                r_n1 = pin_net.get((c["reference"], "1"), (None, None))[0]
                                r_n2 = pin_net.get((c["reference"], "2"), (None, None))[0]
                                if r_n1 and r_n2:
                                    if ({r_n1, r_n2} == {pos_real, neg_real}):
                                        term_resistors.append({
                                            "ref": c["reference"],
                                            "value": c["value"],
                                            "ohms": parsed_values.get(c["reference"]),
                                        })
                        entry["termination"] = term_resistors
                        entry["has_termination"] = len(term_resistors) > 0

                    # Series resistors on either net
                    series_res = []
                    for net_r in (pos_real, neg_real):
                        for p in nets[net_r]["pins"]:
                            comp = comp_lookup.get(p["component"])
                            if comp and comp["type"] == "resistor":
                                series_res.append(p["component"])
                    if series_res:
                        entry["series_resistors"] = sorted(set(series_res))

                    diff_pairs.append(entry)

    # ---- ERC-Style Warnings ----
    erc_warnings = []

    for net_name, net_info in nets.items():
        if is_power_net(net_name) or is_ground(net_name):
            continue
        if net_name.startswith("__unnamed_"):
            continue

        pin_types = [p["pin_type"] for p in net_info["pins"] if not p["component"].startswith("#")]
        type_set = set(pin_types)

        # Input-only net: all pins are inputs, no driver
        outputs = {"output", "tri_state", "power_out", "open_collector", "open_emitter"}
        drivers = {"output", "tri_state", "power_out", "open_collector", "open_emitter", "bidirectional"}
        has_driver = bool(type_set & drivers)
        has_input = bool(type_set & {"input", "power_in"})

        if has_input and not has_driver and len(pin_types) > 1:
            erc_warnings.append({
                "type": "no_driver",
                "net": net_name,
                "message": f"Net '{net_name}' has input pins but no output driver",
                "pins": [p for p in net_info["pins"] if not p["component"].startswith("#")],
            })

        # Multiple outputs on same net (non-tristate)
        hard_outputs = [p for p in net_info["pins"]
                        if p["pin_type"] == "output" and not p["component"].startswith("#")]
        if len(hard_outputs) > 1:
            # Suppress when all drivers are from the same component (paralleled pins)
            driver_components = {p["component"] for p in hard_outputs}
            if len(driver_components) > 1:
                erc_warnings.append({
                    "type": "output_conflict",
                    "net": net_name,
                    "message": f"Net '{net_name}' has {len(hard_outputs)} output drivers (potential conflict)",
                    "drivers": hard_outputs,
                })

    # ---- Passive Ratings Check ----
    # Flag components that might be under-rated based on rail voltage
    passive_warnings = []
    for c in components:
        if c["type"] == "capacitor" and c.get("value"):
            # Check if capacitor voltage rating is in the value string
            val_str = c["value"]
            # Common pattern: "100n/16V", "10u 25V", "22uF 6.3V"
            v_match = re.search(r'(\d+(?:\.\d+)?)\s*[Vv]', val_str)
            if v_match:
                rated_v = float(v_match.group(1))
                # Check which rails this cap connects to
                c_n1, _ = pin_net.get((c["reference"], "1"), (None, None))
                c_n2, _ = pin_net.get((c["reference"], "2"), (None, None))
                # Rough rail voltage estimation from name
                for net in [c_n1, c_n2]:
                    if net:
                        v_est = _estimate_rail_voltage(net)
                        if v_est and rated_v < v_est * 1.5:
                            passive_warnings.append({
                                "component": c["reference"],
                                "value": c["value"],
                                "rated_voltage": rated_v,
                                "rail": net,
                                "estimated_rail_v": v_est,
                                "warning": f"Voltage derating margin < 50% ({rated_v}V rated on ~{v_est}V rail)",
                            })

    return {
        "net_classification": net_classes,
        "power_domains": {
            "ic_power_rails": power_domains,
            "domain_groups": domain_groups,
        },
        "cross_domain_signals": cross_domain,
        "bus_analysis": buses,
        "differential_pairs": diff_pairs,
        "erc_warnings": erc_warnings,
        "passive_warnings": passive_warnings,
    }


def _estimate_rail_voltage(net_name: str) -> float | None:
    """Estimate voltage of a power rail from its name."""
    if not net_name:
        return None
    nu = net_name.upper()
    if nu in ("GND", "VSS", "AGND", "DGND"):
        return 0
    # Parse voltage from name: +3.3V, +5V, +12V, +3V3, +1V8, etc.
    m = re.match(r'\+?(\d+)V(\d+)', nu)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    m = re.match(r'\+?(\d+\.?\d*)V?$', nu)
    if m:
        return float(m.group(1))
    # Common names
    if "3.3" in nu or "3V3" in nu:
        return 3.3
    if "5V" in nu or nu == "+5":
        return 5.0
    if "12V" in nu:
        return 12.0
    if "1.8" in nu or "1V8" in nu:
        return 1.8
    if "2.5" in nu or "2V5" in nu:
        return 2.5
    if "VBUS" in nu or "USB" in nu:
        return 5.0
    return None


def check_annotation_completeness(components: list[dict]) -> dict:
    """Check for annotation issues: duplicate references, unannotated ('?') refs, missing values.

    These are common pre-fabrication mistakes that KiCad's ERC should also catch,
    but detecting them in the script output helps catch them earlier in the workflow.
    """
    # Skip power symbols and flags
    real_components = [c for c in components
                       if c["type"] not in ("power_symbol", "power_flag", "flag")]

    # Duplicate references (same ref, different UUID — not multi-unit which share refs)
    ref_uuids: dict[str, list[str]] = {}
    for c in real_components:
        ref_uuids.setdefault(c["reference"], []).append(c["uuid"])
    # Multi-unit symbols legitimately share a reference, so only flag if the
    # UUIDs come from symbols with unit=None (single-unit) or if there are
    # more instances than expected units
    duplicates = []
    for ref, uuids in ref_uuids.items():
        if len(uuids) > 1:
            # Check if these are multi-unit instances (different unit numbers)
            units = [c.get("unit") for c in real_components if c["reference"] == ref]
            unique_units = set(u for u in units if u is not None)
            if len(unique_units) < len(uuids):
                duplicates.append(ref)

    # Unannotated references (contain '?')
    unannotated = sorted(set(
        c["reference"] for c in real_components if "?" in c["reference"]
    ))

    # Missing values (empty or "~" which KiCad uses as placeholder)
    missing_value = sorted(set(
        c["reference"] for c in real_components
        if c["type"] not in ("test_point", "mounting_hole", "fiducial", "graphic")
        and (not c["value"] or c["value"] == "~")
    ))

    # References that don't follow standard numbering (e.g., R0, C0 — unusual starting point)
    zero_indexed = sorted(set(
        c["reference"] for c in real_components
        if re.match(r'^[A-Z]+0$', c["reference"])
    ))

    result = {}
    if duplicates:
        result["duplicate_references"] = sorted(duplicates)
    if unannotated:
        result["unannotated"] = unannotated
    if missing_value:
        result["missing_value"] = missing_value
    if zero_indexed:
        result["zero_indexed_refs"] = zero_indexed
    return result


def validate_label_shapes(labels: list[dict], nets: dict) -> list[dict]:
    """Validate global/hierarchical label shapes against net signal direction.

    Label shapes (input, output, bidirectional, tri_state, passive) should be
    consistent for the same net name and should match the electrical direction
    of the signals on that net.
    """
    warnings = []

    # Group labels by net name
    net_labels: dict[str, list[dict]] = {}
    for lbl in labels:
        if lbl["type"] in ("global_label", "hierarchical_label") and lbl.get("shape"):
            net_labels.setdefault(lbl["name"], []).append(lbl)

    # Check for shape inconsistency within the same net
    for net_name, lbls in net_labels.items():
        shapes = set(l["shape"] for l in lbls)
        if len(shapes) > 1:
            warnings.append({
                "type": "inconsistent_shape",
                "net": net_name,
                "shapes": sorted(shapes),
                "message": f"Net '{net_name}' has labels with different shapes: {sorted(shapes)}",
            })

    # Check for input-shaped labels on nets driven only by other inputs (no source)
    for net_name, lbls in net_labels.items():
        shapes = set(l["shape"] for l in lbls)
        if shapes == {"input"} and net_name in nets:
            net_info = nets[net_name]
            pin_types = set(p["pin_type"] for p in net_info["pins"]
                           if not p["component"].startswith("#"))
            drivers = {"output", "tri_state", "power_out", "open_collector", "open_emitter", "bidirectional"}
            if not (pin_types & drivers):
                warnings.append({
                    "type": "undriven_input_label",
                    "net": net_name,
                    "message": f"Net '{net_name}' has input-shaped label(s) but no driver pins on the net",
                })

    return warnings


def audit_pwr_flags(components: list[dict], nets: dict, known_power_rails: set) -> list[dict]:
    """Audit power rails for missing PWR_FLAG symbols.

    KiCad requires PWR_FLAG on power nets that are only driven by power_in pins
    (e.g., a connector supplying power). Without PWR_FLAG, ERC reports "power pin
    not driven" errors.
    """
    warnings = []

    # Find nets with PWR_FLAG
    flagged_nets = set()
    for c in components:
        if c["type"] == "power_flag" or (c["type"] == "flag" and "PWR_FLAG" in c.get("lib_id", "")):
            # PWR_FLAG connects to whatever net its pin is on
            for pin in c.get("pins", []):
                px, py = pin["x"], pin["y"]
                # Find which net this pin is on
                for net_name, net_info in nets.items():
                    for p in net_info["pins"]:
                        if p["component"] == c["reference"]:
                            flagged_nets.add(net_name)

    # Check each power rail
    for net_name in known_power_rails:
        if net_name in flagged_nets:
            continue
        if net_name not in nets:
            continue
        net_info = nets[net_name]
        pin_types = set(p["pin_type"] for p in net_info["pins"])

        # If the net has only power_in pins (no power_out), it needs PWR_FLAG
        has_power_out = "power_out" in pin_types
        has_power_in = "power_in" in pin_types

        if has_power_in and not has_power_out:
            # Skip well-known power/ground names — these are nearly always driven
            # globally via power symbols on another sheet
            if _is_power_net_name(net_name) or _is_ground_name(net_name):
                continue
            warnings.append({
                "net": net_name,
                "message": f"Power rail '{net_name}' has power_in pins but no power_out or PWR_FLAG — ERC will flag this",
                "pin_types": sorted(pin_types),
            })

    return warnings


def validate_footprint_filters(components: list[dict], lib_symbols: dict) -> list[dict]:
    """Validate assigned footprints against library symbol ki_fp_filters.

    If a symbol defines footprint filter patterns (ki_fp_filters), the assigned
    footprint should match at least one pattern. Mismatches suggest wrong footprint
    assignment (e.g., through-hole resistor assigned to SMD symbol).
    """
    import fnmatch
    warnings = []

    for c in components:
        if c["type"] in ("power_symbol", "power_flag", "flag", "test_point",
                          "mounting_hole", "fiducial", "graphic"):
            continue
        if not c["footprint"]:
            continue

        sym_def = lib_symbols.get(c["lib_id"], {})
        fp_filters_str = sym_def.get("ki_fp_filters", "")
        if not fp_filters_str:
            continue

        # ki_fp_filters is a space-separated list of glob patterns
        patterns = fp_filters_str.split()
        if not patterns:
            continue

        # Extract just the footprint name (after the library prefix)
        fp_name = c["footprint"].split(":")[-1] if ":" in c["footprint"] else c["footprint"]
        fp_full = c["footprint"]

        # Check if any pattern matches
        matched = False
        for pat in patterns:
            if fnmatch.fnmatch(fp_name, pat) or fnmatch.fnmatch(fp_full, pat):
                matched = True
                break

        if not matched:
            # Check if the footprint is from a custom/project-local library.
            # Standard KiCad libraries use well-known prefixes; anything else
            # is project-local and mismatches are intentional.
            _STANDARD_FP_LIBS = {
                "Capacitor_SMD", "Capacitor_THT", "Resistor_SMD", "Resistor_THT",
                "Inductor_SMD", "Inductor_THT", "Package_SO", "Package_QFP",
                "Package_DFN_QFN", "Package_BGA", "Package_TO_SOT_SMD",
                "Package_TO_SOT_THT", "Connector_PinHeader", "Connector_PinSocket",
                "Connector_USB", "Crystal", "LED_SMD", "LED_THT", "Diode_SMD",
                "Diode_THT", "RF_Module", "Button_Switch_SMD", "Button_Switch_THT",
                "Fuse", "TestPoint", "Buzzer_Beeper", "Relay_SMD", "Relay_THT",
                "Transformer_SMD", "Transformer_THT", "Varistor", "Jumper",
                "MountingHole", "Fiducial", "Heatsink",
            }
            sym_lib = c["lib_id"].split(":")[0] if ":" in c["lib_id"] else ""
            fp_lib = c["footprint"].split(":")[0] if ":" in c["footprint"] else ""
            custom_library = bool(fp_lib and (
                (sym_lib and sym_lib.lower() == fp_lib.lower())
                or fp_lib not in _STANDARD_FP_LIBS
            ))

            warnings.append({
                "component": c["reference"],
                "footprint": c["footprint"],
                "filters": patterns,
                "custom_library": custom_library,
                "message": f"{c['reference']}: footprint '{fp_name}' doesn't match any filter pattern {patterns}",
            })

    return warnings


def audit_sourcing_fields(components: list[dict]) -> dict:
    """Audit component sourcing completeness: MPN, distributor part numbers.

    For manufacturing readiness, every BOM component needs at minimum an MPN.
    Distributor PNs (DigiKey, Mouser, LCSC) accelerate ordering.
    """
    real = [c for c in components
            if c["type"] not in ("power_symbol", "power_flag", "flag", "test_point",
                                  "mounting_hole", "fiducial", "graphic")
            and c["in_bom"] and not c["dnp"]]

    # Deduplicate by reference (multi-unit symbols)
    seen = set()
    unique = []
    for c in real:
        if c["reference"] not in seen:
            seen.add(c["reference"])
            unique.append(c)

    missing_mpn = [c["reference"] for c in unique if not c.get("mpn")]
    missing_digikey = [c["reference"] for c in unique if not c.get("digikey")]
    missing_mouser = [c["reference"] for c in unique if not c.get("mouser")]
    missing_lcsc = [c["reference"] for c in unique if not c.get("lcsc")]

    total = len(unique)
    result = {
        "total_bom_components": total,
        "mpn_coverage": f"{total - len(missing_mpn)}/{total}",
    }
    if missing_mpn:
        result["missing_mpn"] = sorted(missing_mpn)
    if missing_digikey:
        result["missing_digikey"] = sorted(missing_digikey)
    if missing_lcsc:
        result["missing_lcsc"] = sorted(missing_lcsc)

    # Compute readiness score
    if total > 0:
        mpn_pct = (total - len(missing_mpn)) / total * 100
        result["mpn_percent"] = round(mpn_pct, 1)
    return result


# Generic transistor symbol prefixes that encode assumed pin order
_GENERIC_TRANSISTOR_PREFIXES = ("Q_NPN_", "Q_PNP_", "Q_NMOS_", "Q_PMOS_")

# Map prefix to human-readable type
_GENERIC_TYPE_LABELS = {
    "Q_NPN_": "NPN",
    "Q_PNP_": "PNP",
    "Q_NMOS_": "NMOS",
    "Q_PMOS_": "PMOS",
}

# Map single-letter pin abbreviations to full names
_PIN_LETTER_NAMES = {
    "B": "Base", "C": "Collector", "E": "Emitter",
    "G": "Gate", "S": "Source", "D": "Drain",
}


def check_generic_transistor_symbols(components: list[dict],
                                     schematic_path: str = "") -> list[dict]:
    """Flag transistors using generic KiCad symbols instead of device-specific ones.

    Generic symbols (Q_NPN_BCE, Q_NMOS_GSD, etc.) encode an assumed pin order
    that may not match the actual part. SOT-23 pin mapping varies by manufacturer:
    BCE vs BEC vs CBE for BJTs, GSD vs GDS vs SGD for MOSFETs. Using a generic
    symbol with the wrong pin order produces a board that silently doesn't work.

    Device-specific symbols (MMBT3904, AO3400A) encode the correct pinout for
    that particular part and are always safer.

    If a datasheets/index.json exists next to the schematic, the check also notes
    whether a datasheet is available for manual pinout verification.
    """
    warnings = []

    # Load datasheet index if available
    ds_index: dict[str, dict] = {}
    if schematic_path:
        sch_dir = Path(schematic_path).parent
        idx_path = sch_dir / "datasheets" / "index.json"
        if idx_path.is_file():
            try:
                ds_index = json.loads(idx_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

    # Deduplicate by reference (multi-unit symbols)
    seen: set[str] = set()

    for c in components:
        if c["type"] != "transistor":
            continue
        ref = c["reference"]
        if ref in seen:
            continue
        seen.add(ref)

        lib_id = c.get("lib_id", "")
        # Extract symbol name (part after the colon)
        sym_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id

        # Check if this is a generic transistor symbol
        matched_prefix = None
        for prefix in _GENERIC_TRANSISTOR_PREFIXES:
            if sym_name.startswith(prefix):
                matched_prefix = prefix
                break

        if matched_prefix is None:
            continue

        # Extract pin order suffix (e.g., "GSD" from "Q_NMOS_GSD")
        pin_suffix = sym_name[len(matched_prefix):]
        sym_type = _GENERIC_TYPE_LABELS.get(matched_prefix, "transistor")

        # Expand pin abbreviations for the message
        pin_names = "-".join(
            _PIN_LETTER_NAMES.get(ch, ch) for ch in pin_suffix
        ) if pin_suffix else pin_suffix

        mpn = c.get("mpn", "")
        value = c.get("value", "")
        footprint = c.get("footprint", "")
        fp_name = footprint.split(":")[-1] if ":" in footprint else footprint

        # Check datasheet availability by MPN
        has_datasheet = False
        if mpn and ds_index:
            # index.json keys may be MPN strings or nested under "components"
            if isinstance(ds_index, dict):
                if mpn in ds_index:
                    has_datasheet = True
                elif "components" in ds_index:
                    comps = ds_index["components"]
                    if isinstance(comps, dict) and mpn in comps:
                        has_datasheet = True
                    elif isinstance(comps, list):
                        has_datasheet = any(
                            e.get("mpn") == mpn for e in comps
                            if isinstance(e, dict)
                        )

        # Build human-readable part identifier
        part_id = mpn or value or "unknown part"

        # Build message
        if has_datasheet:
            action = f"Verify pinout against the {part_id} datasheet (available in datasheets/) or switch to a device-specific symbol."
        elif mpn:
            action = f"Verify pinout against the {part_id} datasheet or switch to a device-specific symbol."
        else:
            action = "Add an MPN and verify pinout against the datasheet, or switch to a device-specific symbol."

        msg = (
            f"{ref}: Generic {sym_type} symbol ({sym_name}) used"
            f"{' for ' + part_id if part_id != 'unknown part' else ''}"
            f"{' in ' + fp_name if fp_name else ''}."
            f" Pin order ({pin_names}) may not match the actual part."
            f" {action}"
        )

        warnings.append({
            "component": ref,
            "lib_id": lib_id,
            "value": value,
            "mpn": mpn,
            "footprint": footprint,
            "symbol_pin_order": pin_suffix,
            "symbol_type": sym_type,
            "has_datasheet": has_datasheet,
            "severity": "warning",
            "message": msg,
        })

    return warnings


def summarize_alternate_pins(lib_symbols: dict) -> list[dict]:
    """Summarize symbols that have alternate pin definitions (dual-function pins).

    Alternate pins are common on MCUs where GPIO pins can serve as SPI/I2C/UART/PWM
    peripherals. This summary helps understand the pin multiplexing capabilities.
    """
    results = []
    for name, sym in lib_symbols.items():
        alts = sym.get("alternates")
        if not alts:
            continue

        pin_summary = []
        for pin_num, alt_list in sorted(alts.items()):
            # Find the primary pin name
            primary_name = ""
            for p in sym["pins"]:
                if p["number"] == pin_num:
                    primary_name = p["name"]
                    break
            pin_summary.append({
                "pin": pin_num,
                "primary": primary_name,
                "alternates": [a["name"] for a in alt_list],
            })

        results.append({
            "symbol": name,
            "pins_with_alternates": len(alts),
            "total_pins": len(sym["pins"]),
            "details": pin_summary,
        })

    return results


def classify_ground_domains(nets: dict, components: list[dict]) -> dict:
    """Classify ground nets into domains: signal, analog, digital, earth, chassis, power.

    Multiple ground domains in a design need careful management — star grounding,
    proper domain separation, and single-point connections between domains.
    """
    ground_nets = {}
    for net_name, net_info in nets.items():
        if not _is_ground_name(net_name):
            continue

        nu = net_name.upper()
        if any(x in nu for x in ("AGND", "GNDA", "VSS_A", "VSSA")):
            domain = "analog"
        elif any(x in nu for x in ("DGND", "GNDD", "VSS_D", "VSSD")):
            domain = "digital"
        elif any(x in nu for x in ("PGND", "GNDPWR", "VSS_P")):
            domain = "power"
        elif any(x in nu for x in ("EARTH", "PE", "FG", "CHASSIS", "SHIELD")):
            domain = "earth/chassis"
        else:
            domain = "signal"

        pin_count = len([p for p in net_info["pins"] if not p["component"].startswith("#")])
        connected_components = sorted(set(
            p["component"] for p in net_info["pins"] if not p["component"].startswith("#")
        ))
        ground_nets[net_name] = {
            "domain": domain,
            "connections": pin_count,
            "components": connected_components,
        }

    domains = {}
    for net_name, info in ground_nets.items():
        d = info["domain"]
        domains.setdefault(d, []).append(net_name)

    result = {"ground_nets": ground_nets}
    if len(domains) > 1:
        result["multiple_domains"] = True
        result["domains"] = domains
        result["note"] = "Multiple ground domains detected — verify proper star/single-point connection between domains"
    else:
        result["multiple_domains"] = False
    return result


def analyze_bus_topology(bus_elements: dict, labels: list[dict], nets: dict) -> dict:
    """Analyze bus structure: which signals are grouped, naming consistency, member coverage.

    Checks that bus aliases have corresponding labels for all members, and that
    bus naming follows consistent patterns (e.g., D[0..7] has labels D0..D7).
    """
    result = {
        "bus_wire_count": len(bus_elements.get("bus_wires", [])),
        "bus_entry_count": len(bus_elements.get("bus_entries", [])),
    }

    aliases = bus_elements.get("bus_aliases", [])
    if aliases:
        alias_info = []
        all_label_names = set(l["name"] for l in labels)
        for alias in aliases:
            members = alias["members"]
            present = [m for m in members if m in all_label_names]
            missing = [m for m in members if m not in all_label_names]
            # Check which member names resolve to actual nets
            resolved = [m for m in members if m in nets]
            entry = {
                "name": alias["name"],
                "member_count": len(members),
                "present_labels": len(present),
                "resolved_nets": len(resolved),
            }
            if missing:
                entry["missing_labels"] = missing
            unresolved = [m for m in members if m not in nets]
            if unresolved:
                entry["unresolved_members"] = unresolved
            alias_info.append(entry)
        result["aliases"] = alias_info

    # Detect bus-like label patterns (D0..D7, ADDR0..ADDR15, etc.)
    bus_patterns: dict[str, list[str]] = {}
    for lbl in labels:
        m = re.match(r'^([A-Za-z_]+)(\d+)$', lbl["name"])
        if m:
            prefix = m.group(1)
            bus_patterns.setdefault(prefix, []).append(lbl["name"])

    detected_buses = []
    for prefix, members in sorted(bus_patterns.items()):
        if len(members) >= 3:  # At least 3 signals to be bus-like
            nums = sorted(int(re.search(r'\d+$', m).group()) for m in members)
            expected = list(range(nums[0], nums[-1] + 1))
            missing_nums = [n for n in expected if n not in nums]
            entry = {
                "prefix": prefix,
                "width": len(members),
                "range": f"{prefix}{nums[0]}..{prefix}{nums[-1]}",
            }
            if missing_nums:
                entry["missing"] = [f"{prefix}{n}" for n in missing_nums]
            detected_buses.append(entry)

    if detected_buses:
        result["detected_bus_signals"] = detected_buses

    return result


def analyze_wire_geometry(wires: list[dict]) -> dict:
    """Analyze wire routing geometry for schematic cleanliness.

    Flags non-orthogonal wires (diagonal), very short wires (possible stubs),
    and computes overall wire statistics.
    """
    if not wires:
        return {"total_wires": 0}

    diagonal = []
    short_wires = []
    total_length = 0.0

    for w in wires:
        dx = abs(w["x2"] - w["x1"])
        dy = abs(w["y2"] - w["y1"])
        length = math.sqrt(dx * dx + dy * dy)
        total_length += length

        # Non-orthogonal: neither horizontal nor vertical (with tolerance)
        is_h = dy < 0.01
        is_v = dx < 0.01
        if not is_h and not is_v and length > 0.1:
            diagonal.append({
                "from": [round(w["x1"], 2), round(w["y1"], 2)],
                "to": [round(w["x2"], 2), round(w["y2"], 2)],
                "length": round(length, 2),
            })

        # Very short wires (< 1mm) — possible stubs or misclicks
        if 0 < length < 1.0:
            short_wires.append({
                "from": [round(w["x1"], 2), round(w["y1"], 2)],
                "to": [round(w["x2"], 2), round(w["y2"], 2)],
                "length": round(length, 3),
            })

    result = {
        "total_wires": len(wires),
        "total_length_mm": round(total_length, 1),
        "avg_length_mm": round(total_length / len(wires), 1) if wires else 0,
    }
    if diagonal:
        result["diagonal_wires"] = diagonal[:20]  # Cap output
        result["diagonal_count"] = len(diagonal)
    if short_wires:
        result["short_wires"] = short_wires[:20]
        result["short_wire_count"] = len(short_wires)
    return result


def check_simulation_readiness(components: list[dict], lib_symbols: dict) -> dict:
    """Check if components have SPICE simulation models assigned.

    KiCad's built-in NGSPICE simulator requires each component to have a
    simulation model (Sim.Type, Sim.Params, etc.) for the circuit to simulate.
    """
    real = [c for c in components
            if c["type"] not in ("power_symbol", "power_flag", "flag",
                                  "test_point", "mounting_hole", "fiducial", "graphic")]

    # Check for Sim_* properties which indicate SPICE model assignment
    # These are stored as component properties but we extracted only standard ones.
    # We can at least check lib_symbol descriptions for SPICE-related keywords.
    has_model_hint = []
    no_model = []

    for c in real:
        sym = lib_symbols.get(c["lib_id"], {})
        desc = (sym.get("description", "") + " " + sym.get("keywords", "")).lower()
        ctype = c["type"]

        # Passives (R, C, L) have built-in SPICE models
        if ctype in ("resistor", "capacitor", "inductor"):
            has_model_hint.append(c["reference"])
        elif "spice" in desc or "simulation" in desc or "sim_" in desc:
            has_model_hint.append(c["reference"])
        elif ctype in ("diode", "led", "transistor"):
            # Common discrete parts — may have models
            has_model_hint.append(c["reference"])
        else:
            no_model.append(c["reference"])

    total = len(real)
    modeled = len(has_model_hint)

    result = {
        "total_components": total,
        "likely_simulatable": modeled,
        "needs_model": len(no_model),
    }
    if no_model:
        result["components_without_model"] = sorted(set(no_model))[:30]
    if total > 0:
        result["simulatable_percent"] = round(modeled / total * 100, 1)
    return result


def audit_property_patterns(components: list[dict]) -> dict:
    """Audit property naming consistency across components.

    Checks that MPN, manufacturer, and distributor fields use consistent
    property names (e.g., not a mix of "MPN" vs "Mfg Part" vs "Part Number").
    Also checks for common data entry issues.
    """
    real = [c for c in components
            if c["type"] not in ("power_symbol", "power_flag", "flag")]

    issues = []

    # Check for value field anomalies
    for c in real:
        val = c.get("value", "")
        ref = c["reference"]

        # Reference designator accidentally used as value
        if val == ref:
            issues.append({
                "component": ref,
                "issue": "value_equals_reference",
                "message": f"{ref}: value is same as reference designator (likely placeholder)",
            })

        # Lib_id used as value (forgot to set value)
        if val and ":" in val and val == c.get("lib_id", ""):
            issues.append({
                "component": ref,
                "issue": "value_is_lib_id",
                "message": f"{ref}: value appears to be the library ID '{val}' (not a real value)",
            })

        # Footprint in the value field
        if val and ("_SMD:" in val or "_THT:" in val or "Resistor_SMD" in val):
            issues.append({
                "component": ref,
                "issue": "value_looks_like_footprint",
                "message": f"{ref}: value '{val}' looks like a footprint, not a component value",
            })

    # Check for MPN/value inconsistency within same BOM group
    # (same value + footprint should have same MPN)
    bom_groups: dict[tuple, list] = {}
    for c in real:
        if c["in_bom"] and not c["dnp"] and c.get("value"):
            key = (c["value"], c["footprint"])
            bom_groups.setdefault(key, []).append(c)

    for key, group in bom_groups.items():
        mpns = set(c["mpn"] for c in group if c["mpn"])
        if len(mpns) > 1:
            refs = sorted(c["reference"] for c in group)
            issues.append({
                "components": refs,
                "issue": "inconsistent_mpn",
                "message": f"Components with value '{key[0]}' / footprint '{key[1]}' have different MPNs: {sorted(mpns)}",
            })

    result = {}
    if issues:
        result["issues"] = issues
        result["issue_count"] = len(issues)
    return result


def spatial_clustering(components: list[dict]) -> dict:
    """Analyze component placement clustering to identify functional groups.

    Groups components by proximity to help identify subcircuit boundaries
    and check for spatial organization of the schematic.
    """
    real = [c for c in components
            if c["type"] not in ("power_symbol", "power_flag", "flag")]

    if not real:
        return {"clusters": []}

    # Simple grid-based clustering: divide the schematic into regions
    xs = [c["x"] for c in real]
    ys = [c["y"] for c in real]

    if not xs or not ys:
        return {"clusters": []}

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_range = x_max - x_min or 1
    y_range = y_max - y_min or 1

    # Use quadrant-based grouping (4x4 grid)
    grid_cols = min(4, max(1, int(x_range / 40)))
    grid_rows = min(4, max(1, int(y_range / 30)))

    cell_w = x_range / grid_cols if grid_cols > 1 else x_range + 1
    cell_h = y_range / grid_rows if grid_rows > 1 else y_range + 1

    grid: dict[tuple, list] = {}
    for c in real:
        col = min(int((c["x"] - x_min) / cell_w), grid_cols - 1)
        row = min(int((c["y"] - y_min) / cell_h), grid_rows - 1)
        grid.setdefault((row, col), []).append(c)

    clusters = []
    for (row, col), members in sorted(grid.items()):
        type_counts: dict[str, int] = {}
        for c in members:
            type_counts[c["type"]] = type_counts.get(c["type"], 0) + 1

        refs = sorted(c["reference"] for c in members)
        clusters.append({
            "region": f"row{row}_col{col}",
            "component_count": len(members),
            "types": type_counts,
            "references": refs if len(refs) <= 20 else refs[:20] + [f"... +{len(refs)-20} more"],
        })

    # Component density and spread statistics
    result = {
        "bounding_box": {
            "x_min": round(x_min, 1), "y_min": round(y_min, 1),
            "x_max": round(x_max, 1), "y_max": round(y_max, 1),
            "width_mm": round(x_range, 1), "height_mm": round(y_range, 1),
        },
        "clusters": clusters,
        "grid_size": f"{grid_rows}x{grid_cols}",
    }
    return result


def verify_pin_coverage(components: list[dict], lib_symbols: dict) -> list[dict]:
    """Verify that all non-NC library pins are accounted for on placed symbols.

    Checks if a placed symbol has fewer pins connected than the library definition
    expects, which could indicate missing connections or wrong unit placement.
    """
    warnings = []

    # Group components by reference (multi-unit symbols)
    ref_components: dict[str, list[dict]] = {}
    for c in components:
        if c["type"] in ("power_symbol", "power_flag", "flag"):
            continue
        ref_components.setdefault(c["reference"], []).append(c)

    for ref, comp_list in ref_components.items():
        c = comp_list[0]  # Use first instance for lib_id lookup
        sym_def = lib_symbols.get(c["lib_id"])
        if not sym_def:
            continue

        lib_pins = sym_def["pins"]
        if not lib_pins:
            continue

        # Count placed pins across all units
        placed_pin_nums = set()
        for comp in comp_list:
            for pin in comp.get("pins", []):
                placed_pin_nums.add(pin.get("number", ""))

        # Count expected pins from library (excluding no-connect type pins)
        expected_pins = set()
        for p in lib_pins:
            if p["type"] != "no_connect":
                expected_pins.add(p["number"])

        missing = expected_pins - placed_pin_nums
        if missing and len(missing) > len(expected_pins) * 0.3:
            # More than 30% of pins missing — likely a real issue
            warnings.append({
                "component": ref,
                "lib_id": c["lib_id"],
                "expected_pins": len(expected_pins),
                "placed_pins": len(placed_pin_nums),
                "missing_count": len(missing),
                "message": f"{ref}: {len(missing)}/{len(expected_pins)} library pins not placed (may need more units or check symbol)",
            })

    return warnings


def check_instance_consistency(components: list[dict]) -> list[dict]:
    """Check multi-unit and multi-instance symbol consistency.

    For multi-unit symbols (e.g., quad op-amp), verify all expected units are placed.
    For multi-instance sheets, verify reference numbering doesn't collide.
    """
    warnings = []

    # Group by lib_id to find multi-unit symbols
    lib_groups: dict[str, list[dict]] = {}
    for c in components:
        if c["type"] in ("power_symbol", "power_flag", "flag"):
            continue
        lib_groups.setdefault(c["lib_id"], []).append(c)

    # Check for partial unit placement
    for lib_id, comp_list in lib_groups.items():
        # Group by reference
        ref_units: dict[str, set[int]] = {}
        for c in comp_list:
            if c["unit"] is not None:
                ref_units.setdefault(c["reference"], set()).add(c["unit"])

        for ref, units in ref_units.items():
            if not units:
                continue
            max_unit = max(units)
            expected = set(range(1, max_unit + 1))
            missing = expected - units
            if missing:
                warnings.append({
                    "component": ref,
                    "lib_id": lib_id,
                    "placed_units": sorted(units),
                    "missing_units": sorted(missing),
                    "message": f"{ref}: units {sorted(missing)} not placed (of {max_unit} total)",
                })

    # Check for reference collisions across sheets
    ref_sheets: dict[str, set] = {}
    for c in components:
        if c["type"] in ("power_symbol", "power_flag", "flag"):
            continue
        sheet = c.get("_sheet", 0)
        ref_sheets.setdefault(c["reference"], set()).add(sheet)

    for ref, sheets in ref_sheets.items():
        if len(sheets) > 1:
            # Same reference on different sheets with different UUIDs
            instances = [c for c in components if c["reference"] == ref]
            uuids = set(c["uuid"] for c in instances)
            units = set(c["unit"] for c in instances if c["unit"] is not None)
            # Multi-unit on different sheets is OK (unit != None and different units)
            if len(uuids) > len(units) and len(units) > 0:
                pass  # Multi-unit, different units — fine
            elif len(uuids) > 1 and not units:
                warnings.append({
                    "component": ref,
                    "sheets": sorted(sheets),
                    "message": f"{ref}: appears on {len(sheets)} sheets with different UUIDs (reference collision?)",
                })

    return warnings


def validate_hierarchical_labels(labels: list[dict], nets: dict) -> dict:
    """Validate hierarchical label usage for cross-sheet connectivity.

    Checks for orphaned hierarchical labels (no matching sheet pin), hierarchical
    labels that don't connect to any net, and naming consistency.
    """
    hier_labels = [l for l in labels if l["type"] == "hierarchical_label"]
    global_labels = [l for l in labels if l["type"] == "global_label"]

    result = {
        "hierarchical_label_count": len(hier_labels),
        "global_label_count": len(global_labels),
    }

    # Check for hierarchical labels that don't appear in any net
    hier_names = set(l["name"] for l in hier_labels)
    global_names = set(l["name"] for l in global_labels)
    net_names = set(nets.keys())

    unconnected_hier = sorted(hier_names - net_names)
    if unconnected_hier:
        result["unconnected_hierarchical"] = unconnected_hier

    # Check for naming conflicts between global and hierarchical labels
    conflicts = sorted(hier_names & global_names)
    if conflicts:
        result["global_hier_name_conflicts"] = conflicts
        result["conflict_warning"] = "Same name used as both global and hierarchical label — may cause unexpected connections"

    # Group global labels by name and check for single-instance labels
    # (a global label used only once is suspicious — it should connect to something)
    global_name_counts: dict[str, int] = {}
    for l in global_labels:
        global_name_counts[l["name"]] = global_name_counts.get(l["name"], 0) + 1

    single_use = sorted(n for n, c in global_name_counts.items() if c == 1)
    if single_use:
        result["single_use_global_labels"] = single_use

    return result


# ---------------------------------------------------------------------------
# Tier 3: High-level design analysis functions
# ---------------------------------------------------------------------------


def analyze_pdn_impedance(components: list[dict], nets: dict, pin_net: dict) -> dict:
    """PDN impedance profiling per power rail.

    Groups all capacitors by power rail, estimates ESR/ESL from package size,
    computes combined impedance at frequency points (1 kHz to 1 GHz), and flags
    frequency gaps and anti-resonances.
    """
    # Package-dependent parasitics
    esl_by_pkg = {
        "0201": 0.2e-9, "0402": 0.3e-9, "0603": 0.5e-9,
        "0805": 0.8e-9, "1206": 1.2e-9, "1210": 1.5e-9,
        "1812": 2.0e-9, "2220": 2.5e-9,
    }
    esr_base_by_pkg = {
        "0201": 0.020, "0402": 0.015, "0603": 0.012,
        "0805": 0.010, "1206": 0.010, "1210": 0.010,
        "1812": 0.010, "2220": 0.010,
    }

    def _extract_package_code(footprint: str) -> str | None:
        if not footprint:
            return None
        # Match common patterns like C_0402_1005Metric, R_0805_...
        m = re.search(r'(\d{4})_\d{4}Metric', footprint)
        if m:
            return m.group(1)
        # Direct 4-digit codes
        m = re.search(r'\b(0201|0402|0603|0805|1206|1210|1812|2220)\b', footprint)
        if m:
            return m.group(1)
        return None

    def _is_electrolytic_or_tantalum(comp: dict) -> bool:
        fp = comp.get("footprint", "").lower()
        val = comp.get("value", "").lower()
        lib = comp.get("lib_id", "").lower()
        combined = fp + " " + val + " " + lib
        if any(k in combined for k in ("electrolytic", "tantalum", "cp_", "c_radial", "c_axial")):
            return True
        # Large THT caps or large values without SMD footprint
        if "tht" in fp or "radial" in fp or "axial" in fp:
            return True
        return False

    def _cap_impedance(f: float, c_farads: float, esr: float, esl: float) -> float:
        x_c = 1.0 / (2.0 * math.pi * f * c_farads) if c_farads > 0 else 1e12
        x_l = 2.0 * math.pi * f * esl
        return math.sqrt(esr ** 2 + (x_l - x_c) ** 2)

    comp_lookup = {c["reference"]: c for c in components}

    # Build power rail -> caps mapping
    rail_caps: dict[str, list[dict]] = {}
    for net_name, net_info in nets.items():
        if net_name.startswith("__unnamed_"):
            continue
        if _is_ground_name(net_name):
            continue
        if not _is_power_net_name(net_name):
            continue

        for p in net_info["pins"]:
            comp = comp_lookup.get(p["component"])
            if not comp or comp["type"] != "capacitor":
                continue
            cap_val = parse_value(comp.get("value", ""))
            if not cap_val or cap_val <= 0:
                continue
            # Check other pin goes to ground
            n1, _ = pin_net.get((p["component"], "1"), (None, None))
            n2, _ = pin_net.get((p["component"], "2"), (None, None))
            other = n2 if n1 == net_name else n1
            if not _is_ground_name(other):
                continue

            pkg = _extract_package_code(comp.get("footprint", ""))
            is_elec = _is_electrolytic_or_tantalum(comp)
            if is_elec:
                esr = max(0.1, 0.5 / math.sqrt(cap_val * 1e6)) if cap_val > 0 else 0.5
                esl = 7.5e-9  # typical 5-10 nH
            elif pkg and pkg in esl_by_pkg:
                esl = esl_by_pkg[pkg]
                base_esr = esr_base_by_pkg.get(pkg, 0.015)
                # ESR scales roughly with 1/sqrt(C in uF), clamped
                c_uf = cap_val * 1e6
                esr = max(0.005, base_esr / math.sqrt(max(c_uf, 0.001)))
            else:
                # Unknown package — assume 0603 MLCC defaults
                esl = 0.5e-9
                c_uf = cap_val * 1e6
                esr = max(0.005, 0.012 / math.sqrt(max(c_uf, 0.001)))

            srf = 1.0 / (2.0 * math.pi * math.sqrt(esl * cap_val)) if esl > 0 and cap_val > 0 else 0

            cap_entry = {
                "ref": p["component"],
                "value": comp["value"],
                "farads": cap_val,
                "package": pkg,
                "esr_ohm": round(esr, 4),
                "esl_nH": round(esl * 1e9, 2),
                "srf_hz": round(srf),
                "srf_formatted": _format_frequency(srf) if srf > 0 else "N/A",
            }
            cap_entry["type"] = "electrolytic/tantalum" if is_elec else "MLCC"

            rail_caps.setdefault(net_name, []).append(cap_entry)

    if not rail_caps:
        return {}

    # Frequency sweep points: 1 kHz to 1 GHz, 10 points per decade
    freq_points = []
    f = 1e3
    while f <= 1.01e9:
        freq_points.append(f)
        f *= 10 ** 0.1  # 10 points per decade

    rails_result = {}
    observations = []

    for rail_name, caps in rail_caps.items():
        # Compute combined impedance at each frequency point
        impedance_profile = []
        for f in freq_points:
            z_parallel_inv = 0.0
            for cap in caps:
                z_i = _cap_impedance(f, cap["farads"], cap["esr_ohm"], cap["esl_nH"] * 1e-9)
                if z_i > 0:
                    z_parallel_inv += 1.0 / z_i
            z_total = 1.0 / z_parallel_inv if z_parallel_inv > 0 else 1e12
            impedance_profile.append({
                "freq_hz": round(f),
                "freq_formatted": _format_frequency(f),
                "impedance_ohm": round(z_total, 6),
            })

        # Find anti-resonance peaks (local maxima in impedance)
        anti_resonances = []
        for i in range(1, len(impedance_profile) - 1):
            z_prev = impedance_profile[i - 1]["impedance_ohm"]
            z_curr = impedance_profile[i]["impedance_ohm"]
            z_next = impedance_profile[i + 1]["impedance_ohm"]
            if z_curr > z_prev and z_curr > z_next:
                anti_resonances.append({
                    "freq_formatted": impedance_profile[i]["freq_formatted"],
                    "freq_hz": impedance_profile[i]["freq_hz"],
                    "impedance_ohm": z_curr,
                })

        # Find min impedance
        min_z = min(impedance_profile, key=lambda x: x["impedance_ohm"])

        rail_result = {
            "capacitors": caps,
            "cap_count": len(caps),
            "total_capacitance_uF": round(sum(c["farads"] for c in caps) * 1e6, 3),
            "impedance_profile": impedance_profile,
            "min_impedance": {
                "freq_formatted": min_z["freq_formatted"],
                "impedance_ohm": min_z["impedance_ohm"],
            },
        }
        if anti_resonances:
            rail_result["anti_resonances"] = anti_resonances
            for ar in anti_resonances:
                if ar["impedance_ohm"] > 1.0:
                    observations.append(
                        f"{rail_name}: anti-resonance at {ar['freq_formatted']} "
                        f"({ar['impedance_ohm']:.3f} ohm) — consider adding cap with SRF near this frequency"
                    )

        # Check for frequency gaps: if all cap SRFs are below 100 MHz,
        # high-frequency decoupling may be lacking
        max_srf = max((c["srf_hz"] for c in caps), default=0)
        if max_srf < 100e6 and len(caps) > 0:
            observations.append(
                f"{rail_name}: highest SRF is {_format_frequency(max_srf)} — "
                f"consider adding small (100pF-1nF) MLCC for >100 MHz coverage"
            )

        rails_result[rail_name] = rail_result

    result = {"rails": rails_result}
    if observations:
        result["observations"] = observations
    return result


def analyze_sleep_current(components: list[dict], nets: dict, pin_net: dict,
                          signal_analysis: dict | None = None) -> dict:
    """Sleep/quiescent current audit.

    Finds all always-on current paths: resistive dividers between power and
    ground, pull-up/pull-down resistors to power rails, LED indicators, and
    regulator quiescent currents (estimated from part family).
    """
    comp_lookup = {c["reference"]: c for c in components}
    rail_currents: dict[str, list[dict]] = {}

    def _get_two_pin_nets(ref: str) -> tuple[str | None, str | None]:
        n1, _ = pin_net.get((ref, "1"), (None, None))
        n2, _ = pin_net.get((ref, "2"), (None, None))
        return n1, n2

    # --- Resistors between power and ground ---
    for comp in components:
        if comp["type"] != "resistor":
            continue
        r_val = parse_value(comp.get("value", ""))
        if not r_val or r_val <= 0:
            continue
        n1, n2 = _get_two_pin_nets(comp["reference"])
        if not n1 or not n2:
            continue

        pwr_net = None
        gnd_net = None
        if _is_power_net_name(n1) and not _is_ground_name(n1) and _is_ground_name(n2):
            pwr_net, gnd_net = n1, n2
        elif _is_power_net_name(n2) and not _is_ground_name(n2) and _is_ground_name(n1):
            pwr_net, gnd_net = n2, n1

        if pwr_net and gnd_net:
            v_rail = _estimate_rail_voltage(pwr_net)
            if v_rail and v_rail > 0:
                current_a = v_rail / r_val
                entry = {
                    "ref": comp["reference"],
                    "value": comp["value"],
                    "type": "resistor_to_gnd",
                    "resistance_ohm": r_val,
                    "rail_voltage": v_rail,
                    "current_uA": round(current_a * 1e6, 2),
                }
                # Check if this is part of a voltage divider (second resistor on the non-gnd side)
                if pwr_net in nets:
                    other_resistors = [
                        p["component"] for p in nets[pwr_net]["pins"]
                        if p["component"] != comp["reference"]
                        and comp_lookup.get(p["component"], {}).get("type") == "resistor"
                    ]
                    if other_resistors:
                        entry["note"] = f"part of divider with {', '.join(other_resistors)}"
                rail_currents.setdefault(pwr_net, []).append(entry)
            continue

        # Pull-up resistor: one side to power rail, other side to a signal net
        if _is_power_net_name(n1) and not _is_ground_name(n1) and not _is_power_net_name(n2):
            pwr_net = n1
        elif _is_power_net_name(n2) and not _is_ground_name(n2) and not _is_power_net_name(n1):
            pwr_net = n2
        else:
            continue

        v_rail = _estimate_rail_voltage(pwr_net)
        if v_rail and v_rail > 0:
            # Pull-up: worst case current is V/R (pin driven low)
            current_a = v_rail / r_val
            rail_currents.setdefault(pwr_net, []).append({
                "ref": comp["reference"],
                "value": comp["value"],
                "type": "pull_up",
                "resistance_ohm": r_val,
                "rail_voltage": v_rail,
                "current_uA": round(current_a * 1e6, 2),
                "note": "worst-case (signal driven low)",
            })

    # --- LEDs with series resistors ---
    for comp in components:
        if comp["type"] != "led":
            continue
        ref = comp["reference"]
        # Find nets connected to LED pins
        led_nets = []
        for pkey, (net_name, _) in pin_net.items():
            if pkey[0] == ref:
                led_nets.append(net_name)

        for net_name in led_nets:
            if not net_name or net_name not in nets:
                continue
            # Find series resistor on same net
            for p in nets[net_name]["pins"]:
                if p["component"] == ref:
                    continue
                r_comp = comp_lookup.get(p["component"])
                if not r_comp or r_comp["type"] != "resistor":
                    continue
                r_val = parse_value(r_comp.get("value", ""))
                if not r_val or r_val <= 0:
                    continue
                # Find what power rail this LED circuit connects to
                r_n1, r_n2 = _get_two_pin_nets(r_comp["reference"])
                for rn in (r_n1, r_n2):
                    if rn and rn != net_name and _is_power_net_name(rn) and not _is_ground_name(rn):
                        v_rail = _estimate_rail_voltage(rn)
                        if v_rail and v_rail > 0:
                            # LED forward voltage ~2V typical
                            v_led = 2.0
                            if v_rail > v_led:
                                current_a = (v_rail - v_led) / r_val
                                rail_currents.setdefault(rn, []).append({
                                    "ref": ref,
                                    "value": comp.get("value", "LED"),
                                    "type": "led_indicator",
                                    "series_resistor": r_comp["reference"],
                                    "resistance_ohm": r_val,
                                    "rail_voltage": v_rail,
                                    "current_uA": round(current_a * 1e6, 2),
                                    "note": "assuming ~2V forward drop, always-on if no switch",
                                })

    # --- Regulator quiescent current estimates ---
    # Use detected regulators from signal analysis to estimate Iq per output rail.
    # These are rough estimates based on part family — actual values depend on
    # load current, switching frequency, and mode (PFM vs PWM).
    _iq_estimates_uA: dict[str, float] = {
        # Part prefix → typical Iq in uA (from datasheets, sleep/shutdown mode)
        "TPS6": 15,      # TPS61xxx/62xxx — ~15-25 uA typical
        "TPS5": 100,     # TPS54xxx — ~100-300 uA typical (not ultra-low-power)
        "LMR51": 24,     # LMR514xx — 24 uA typical
        "LMR33": 25,     # LMR336xx — 24-30 uA
        "RT5": 20,       # Richtek RT56xx — ~20-40 uA
        "AP2112": 55,    # Diodes AP2112 LDO — 55 uA
        "AP6": 20,       # Diodes AP6xxx — ~20 uA
        "MIC29": 500,    # Microchip MIC29xxx — ~500 uA (older LDO)
        "MIC55": 100,    # Microchip MIC55xx — ~100 uA
        "LM317": 5000,   # LM317 — ~5 mA Iq
        "AMS1117": 5000, # AMS1117 — ~5 mA Iq
        "LD1117": 5000,  # LD1117 — ~5 mA Iq
        "XC6": 1,        # Torex XC6xxx — ~0.5-8 uA (ultra low power)
        "TLV71": 3.4,    # TI TLV713/715 — ~3.4 uA
        "TLV75": 18,     # TI TLV757 — ~18 uA
        "NCP1": 50,      # ON Semi NCP1xxx — ~50 uA
    }
    if signal_analysis:
        for reg in signal_analysis.get("power_regulators", []):
            out_rail = reg.get("output_rail", "")
            if not out_rail:
                continue
            # Look up Iq by part prefix
            reg_value = reg.get("value", "").upper()
            reg_lib = reg.get("lib_id", "").split(":")[-1].upper() if ":" in reg.get("lib_id", "") else ""
            iq_ua = None
            for prefix, iq in _iq_estimates_uA.items():
                if reg_value.startswith(prefix.upper()) or reg_lib.startswith(prefix.upper()):
                    iq_ua = iq
                    break
            if iq_ua is None:
                # Default estimate based on topology
                topo = reg.get("topology", "")
                if topo == "LDO":
                    iq_ua = 100  # generic LDO
                elif topo == "switching":
                    iq_ua = 50  # generic switcher
                else:
                    continue

            # Check if regulator has an EN pin that could disable it
            has_en = False
            for comp in components:
                if comp["reference"] == reg["ref"]:
                    for pin in comp.get("pins", []):
                        pn_upper = pin.get("name", "").upper()
                        if pn_upper in ("EN", "ENABLE", "ON/OFF", "ON", "SHDN", "CE"):
                            has_en = True
                            break
                    break

            rail_currents.setdefault(out_rail, []).append({
                "ref": reg["ref"],
                "value": reg.get("value", ""),
                "type": "regulator_iq",
                "current_uA": round(iq_ua, 2),
                "has_enable_pin": has_en,
                "note": f"estimated Iq ({'can be disabled via EN' if has_en else 'always-on, no EN pin detected'})",
            })

    if not rail_currents:
        return {}

    # Summarize per rail
    result_rails = {}
    total_sleep_uA = 0.0
    for rail, entries in rail_currents.items():
        rail_total_uA = sum(e["current_uA"] for e in entries)
        total_sleep_uA += rail_total_uA
        result_rails[rail] = {
            "current_paths": entries,
            "total_uA": round(rail_total_uA, 2),
        }

    return {
        "rails": result_rails,
        "total_estimated_sleep_uA": round(total_sleep_uA, 2),
        "observations": [
            f"Total estimated always-on current: {total_sleep_uA:.1f} uA ({total_sleep_uA / 1000:.2f} mA)"
        ],
    }


def analyze_voltage_derating(components: list[dict], nets: dict,
                             signal_analysis: dict, pin_net: dict) -> dict:
    """Check capacitor voltage ratings against applied rail voltage.

    Parses voltage rating from cap value string (e.g. '100nF/25V' -> 25V),
    compares against the power rail voltage, and flags caps with insufficient
    derating margin.
    """

    def _parse_voltage_rating(value_str: str) -> float | None:
        """Extract voltage rating from value string."""
        if not value_str:
            return None
        # Patterns: "100nF/25V", "10uF 16V", "4.7u/10V/X5R", "100n 50V 0805"
        # Also: "25V", "16V" as standalone segments
        for part in re.split(r'[/\s]+', value_str):
            m = re.match(r'^(\d+\.?\d*)\s*[Vv]$', part.strip())
            if m:
                return float(m.group(1))
        return None

    derating_issues = []
    caps_checked = 0

    for comp in components:
        if comp["type"] != "capacitor":
            continue
        rated_v = _parse_voltage_rating(comp.get("value", ""))
        if not rated_v:
            continue

        ref = comp["reference"]
        # Find which power rail this cap is on
        n1, _ = pin_net.get((ref, "1"), (None, None))
        n2, _ = pin_net.get((ref, "2"), (None, None))

        pwr_net = None
        if n1 and _is_power_net_name(n1) and not _is_ground_name(n1):
            pwr_net = n1
        elif n2 and _is_power_net_name(n2) and not _is_ground_name(n2):
            pwr_net = n2

        if not pwr_net:
            continue

        # Get rail voltage — first try regulators, then name-based estimate
        v_rail = None
        for reg in signal_analysis.get("power_regulators", []):
            if reg.get("output_rail") == pwr_net:
                v_rail = reg.get("estimated_vout")
                break
        if v_rail is None:
            v_rail = _estimate_rail_voltage(pwr_net)
        if v_rail is None or v_rail <= 0:
            continue

        caps_checked += 1
        margin_pct = ((rated_v - v_rail) / rated_v) * 100 if rated_v > 0 else 0
        severity = None
        if v_rail > rated_v:
            severity = "critical"
        elif margin_pct < 20:
            severity = "warning"

        if severity:
            derating_issues.append({
                "ref": ref,
                "value": comp["value"],
                "rail": pwr_net,
                "rail_voltage": v_rail,
                "rated_voltage": rated_v,
                "margin_pct": round(margin_pct, 1),
                "severity": severity,
            })

    if not derating_issues and caps_checked == 0:
        return {}

    result: dict = {
        "caps_checked": caps_checked,
        "issues": derating_issues,
    }
    observations = []
    critical = [i for i in derating_issues if i["severity"] == "critical"]
    warnings = [i for i in derating_issues if i["severity"] == "warning"]
    if critical:
        observations.append(
            f"{len(critical)} cap(s) exceed rated voltage — risk of failure"
        )
    if warnings:
        observations.append(
            f"{len(warnings)} cap(s) have <20% voltage derating margin"
        )
    if observations:
        result["observations"] = observations
    return result


def analyze_power_budget(components: list[dict], nets: dict,
                         signal_analysis: dict, pin_net: dict) -> dict:
    """Power budget estimation per rail.

    Identifies each rail's regulator and max current, counts ICs per rail with
    rough current estimation by type, and estimates thermal dissipation for LDOs.
    """
    comp_lookup = {c["reference"]: c for c in components}

    # Rough current estimates by IC type keywords (mA)
    ic_current_estimates = {
        "esp32": 240, "esp8266": 170, "esp": 200,
        "nrf52": 15, "nrf51": 12, "nrf53": 20, "nrf91": 50,
        "stm32f4": 100, "stm32f1": 50, "stm32l4": 20, "stm32l0": 10,
        "stm32h7": 200, "stm32f7": 150, "stm32": 80,
        "atmega": 20, "attiny": 10, "atsamd": 30, "samd": 30,
        "rp2040": 50, "rp2350": 60,
        "wifi": 250, "wlan": 250, "bluetooth": 50, "ble": 15,
        "lora": 120, "sx127": 120, "sx126": 50,
        "ethernet": 150, "phy": 100, "lan87": 80, "ksz": 100,
        "sensor": 5, "bme": 3, "bmp": 3, "lis": 2, "mpu": 5,
        "flash": 25, "eeprom": 5, "sram": 10,
        "adc": 10, "dac": 10,
        "codec": 30, "amplifier": 20, "opamp": 5,
        "usb": 50, "uart": 5, "spi": 5, "i2c": 5,
    }

    # Build power domain mapping: rail -> list of ICs
    rail_ics: dict[str, list[dict]] = {}
    for comp in components:
        if comp["type"] != "ic":
            continue
        ref = comp["reference"]
        for pkey, (net_name, _) in pin_net.items():
            if pkey[0] != ref:
                continue
            if net_name and _is_power_net_name(net_name) and not _is_ground_name(net_name):
                # Check if this is a power pin (by pin type or name)
                if net_name in nets:
                    for p in nets[net_name]["pins"]:
                        if p["component"] == ref:
                            ptype = p.get("pin_type", "")
                            pname = p.get("pin_name", "").upper()
                            if ptype == "power_in" or pname in (
                                "VCC", "VDD", "AVCC", "AVDD", "VDDIO", "DVDD",
                                "VIN", "VCCA", "VCCB", "VDDQ", "VBUS"
                            ):
                                ic_entry = {
                                    "ref": ref,
                                    "value": comp["value"],
                                }
                                # Estimate current
                                val_lower = comp.get("value", "").lower()
                                lib_lower = comp.get("lib_id", "").lower()
                                search_str = val_lower + " " + lib_lower
                                est_ma = 10  # default
                                for kw, ma in ic_current_estimates.items():
                                    if kw in search_str:
                                        est_ma = ma
                                        break
                                ic_entry["estimated_mA"] = est_ma
                                rail_ics.setdefault(net_name, []).append(ic_entry)
                            break

    # Deduplicate ICs per rail (an IC may have multiple power pins on same rail)
    for rail in rail_ics:
        seen_refs = set()
        deduped = []
        for ic in rail_ics[rail]:
            if ic["ref"] not in seen_refs:
                seen_refs.add(ic["ref"])
                deduped.append(ic)
        rail_ics[rail] = deduped

    # Map regulators to output rails
    reg_by_rail: dict[str, dict] = {}
    for reg in signal_analysis.get("power_regulators", []):
        out_rail = reg.get("output_rail")
        if out_rail:
            reg_by_rail[out_rail] = reg

    if not rail_ics and not reg_by_rail:
        return {}

    # All rails of interest
    all_rails = set(rail_ics.keys()) | set(reg_by_rail.keys())

    rails_result = {}
    observations = []

    for rail in sorted(all_rails):
        ics = rail_ics.get(rail, [])
        total_ic_mA = sum(ic["estimated_mA"] for ic in ics)

        rail_info: dict = {
            "ic_count": len(ics),
            "ics": ics,
            "estimated_load_mA": total_ic_mA,
        }

        reg = reg_by_rail.get(rail)
        if reg:
            rail_info["regulator"] = {
                "ref": reg["ref"],
                "value": reg["value"],
                "topology": reg.get("topology", "unknown"),
            }
            v_out = reg.get("estimated_vout")
            v_in_rail = reg.get("input_rail")
            if v_out:
                rail_info["regulator"]["output_voltage"] = v_out

            # LDO thermal dissipation
            if reg.get("topology") == "LDO" and v_in_rail and v_out:
                v_in = _estimate_rail_voltage(v_in_rail)
                if v_in and v_in > v_out:
                    v_drop = v_in - v_out
                    power_w = v_drop * (total_ic_mA / 1000.0)
                    rail_info["ldo_dissipation"] = {
                        "input_voltage": v_in,
                        "dropout": round(v_drop, 2),
                        "power_mW": round(power_w * 1000, 1),
                    }
                    if power_w > 0.5:
                        observations.append(
                            f"{rail}: LDO {reg['ref']} dissipates ~{power_w * 1000:.0f} mW "
                            f"({v_drop:.1f}V drop x {total_ic_mA} mA) — verify thermal rating"
                        )

        rails_result[rail] = rail_info

    result: dict = {"rails": rails_result}
    if observations:
        result["observations"] = observations
    return result


def analyze_power_sequencing(components: list[dict], nets: dict,
                             signal_analysis: dict, pin_net: dict) -> dict:
    """Power sequencing dependency analysis.

    For each regulator, finds what drives its EN pin and PG (power-good) output,
    builds a dependency graph, and flags floating EN pins.
    """
    comp_lookup = {c["reference"]: c for c in components}
    regulators = signal_analysis.get("power_regulators", [])
    if not regulators:
        return {}

    dependencies = []
    floating_en = []
    pg_connections = []

    for reg in regulators:
        ref = reg["ref"]
        out_rail = reg.get("output_rail", "")
        ic = comp_lookup.get(ref)
        if not ic:
            continue

        # Gather all pins for this IC
        ic_pins: dict[str, tuple[str, str]] = {}  # pin_name -> (net_name, pin_number)
        for pkey, (net_name, _) in pin_net.items():
            if pkey[0] == ref:
                pin_num = pkey[1]
                pin_name = ""
                if net_name in nets:
                    for p in nets[net_name]["pins"]:
                        if p["component"] == ref and p["pin_number"] == pin_num:
                            pin_name = p.get("pin_name", "").upper()
                            break
                ic_pins[pin_name] = (net_name, pin_num)

        # Find EN pin
        en_net = None
        en_pin_name = None
        for pname, (net, pnum) in ic_pins.items():
            pn_base = pname.rstrip("0123456789")
            if pname in ("EN", "ENABLE", "ON", "~{SHDN}", "SHDN", "~{EN}",
                         "CE", "CHIP_ENABLE") or pn_base == "EN":
                en_net = net
                en_pin_name = pname
                break

        if en_net:
            dep_entry: dict = {
                "regulator": ref,
                "output_rail": out_rail,
                "en_pin": en_pin_name,
                "en_net": en_net,
            }

            # Determine what drives EN
            if _is_power_net_name(en_net):
                dep_entry["en_source"] = "always_on"
                dep_entry["en_driven_by"] = en_net
            elif _is_ground_name(en_net):
                dep_entry["en_source"] = "disabled"
                dep_entry["en_driven_by"] = en_net
            elif en_net in nets:
                en_pins = nets[en_net]["pins"]
                # Filter out power symbols and the regulator itself
                drivers = [
                    p for p in en_pins
                    if p["component"] != ref
                    and not p["component"].startswith("#")
                ]
                if not drivers:
                    dep_entry["en_source"] = "floating"
                    floating_en.append({
                        "regulator": ref,
                        "output_rail": out_rail,
                        "en_pin": en_pin_name,
                        "warning": f"{ref} EN pin ({en_pin_name}) appears unconnected",
                    })
                else:
                    # Check if driven by another regulator's output rail or PG
                    driver_refs = [d["component"] for d in drivers]
                    driver_types = []
                    for dr in drivers:
                        dc = comp_lookup.get(dr["component"])
                        if dc:
                            driver_types.append(dc["type"])
                    # Check if EN is connected to a power rail via resistor
                    has_pull_up = any(
                        comp_lookup.get(d["component"], {}).get("type") == "resistor"
                        for d in drivers
                    )
                    dep_entry["en_source"] = "controlled"
                    dep_entry["en_driven_by"] = driver_refs
                    if has_pull_up:
                        dep_entry["has_pull_up"] = True

            dependencies.append(dep_entry)

        # Find PG/PGOOD pin (exclude ground pads like PGND, AGND, EP/GND)
        for pname, (net, pnum) in ic_pins.items():
            pn_upper = pname.upper()
            # Skip ground/pad pins that false-match on "PG" substring
            if any(g in pn_upper for g in ("GND", "GROUND", "PAD", "EP")):
                continue
            if any(k in pn_upper for k in ("PG", "PGOOD", "POWER_GOOD", "POK")):
                pg_entry: dict = {
                    "regulator": ref,
                    "output_rail": out_rail,
                    "pg_pin": pname,
                    "pg_net": net,
                }
                # Find what PG connects to
                if net in nets:
                    pg_targets = [
                        p["component"] for p in nets[net]["pins"]
                        if p["component"] != ref
                        and not p["component"].startswith("#")
                    ]
                    if pg_targets:
                        pg_entry["connected_to"] = pg_targets
                        # Check if PG drives another regulator's EN
                        for dep in dependencies:
                            if dep.get("en_net") == net:
                                dep["sequenced_after"] = ref
                                dep["sequence_signal"] = "power_good"
                pg_connections.append(pg_entry)
                break

    if not dependencies and not floating_en and not pg_connections:
        return {}

    result: dict = {}
    if dependencies:
        result["dependencies"] = dependencies
    if floating_en:
        result["floating_en_warnings"] = floating_en
    if pg_connections:
        result["power_good_signals"] = pg_connections

    observations = []
    always_on = [d for d in dependencies if d.get("en_source") == "always_on"]
    controlled = [d for d in dependencies if d.get("en_source") == "controlled"]
    if always_on:
        observations.append(
            f"{len(always_on)} regulator(s) always enabled: "
            + ", ".join(d["regulator"] for d in always_on)
        )
    if controlled:
        observations.append(
            f"{len(controlled)} regulator(s) with controlled enable"
        )
    if floating_en:
        observations.append(
            f"{len(floating_en)} regulator(s) with floating EN pin — may not start"
        )
    if observations:
        result["observations"] = observations

    return result


def analyze_bom_optimization(components: list[dict]) -> dict:
    """BOM consolidation and optimization suggestions.

    Groups components by type, finds near-value resistors that could be
    consolidated, identifies capacitors with same value but different footprints,
    and flags single-use values.
    """
    # Filter to real components
    real = [c for c in components
            if c["type"] not in ("power_symbol", "power_flag", "flag")]

    if not real:
        return {}

    # Group by type
    by_type: dict[str, list[dict]] = {}
    for c in real:
        by_type.setdefault(c["type"], []).append(c)

    unique_counts: dict[str, int] = {}
    consolidation_suggestions = []

    # --- Resistors: find values within 5% of each other ---
    resistors = by_type.get("resistor", [])
    r_values: dict[float, list[str]] = {}  # parsed_value -> [refs]
    for r in resistors:
        val = parse_value(r.get("value", ""))
        if val and val > 0:
            r_values.setdefault(val, []).append(r["reference"])

    unique_counts["resistor"] = len(r_values)

    sorted_r_vals = sorted(r_values.keys())
    for i, v1 in enumerate(sorted_r_vals):
        for v2 in sorted_r_vals[i + 1:]:
            if v2 > v1 * 1.06:
                break  # sorted, so no more within 5%
            pct_diff = abs(v2 - v1) / v1 * 100
            if pct_diff <= 5.0 and pct_diff > 0:
                # Only suggest if consolidating saves a unique value
                refs1 = r_values[v1]
                refs2 = r_values[v2]
                # Suggest the more commonly used value
                keep_val = v1 if len(refs1) >= len(refs2) else v2
                replace_val = v2 if keep_val == v1 else v1
                replace_refs = refs2 if keep_val == v1 else refs1
                consolidation_suggestions.append({
                    "type": "resistor",
                    "suggestion": f"Consolidate {len(replace_refs)} resistor(s) "
                                  f"({', '.join(replace_refs)}) from "
                                  f"{replace_val:.4g} to {keep_val:.4g} ohm "
                                  f"({pct_diff:.1f}% difference)",
                    "current_values": [v1, v2],
                    "refs_to_change": replace_refs,
                })

    # --- Capacitors: same value, different footprints ---
    capacitors = by_type.get("capacitor", [])
    cap_by_value: dict[str, dict[str, list[str]]] = {}  # value_str -> {footprint -> [refs]}
    c_values: dict[float, list[str]] = {}
    for c in capacitors:
        val_str = c.get("value", "")
        fp = c.get("footprint", "") or "unknown"
        cap_by_value.setdefault(val_str, {}).setdefault(fp, []).append(c["reference"])
        val = parse_value(val_str)
        if val and val > 0:
            c_values.setdefault(val, []).append(c["reference"])

    unique_counts["capacitor"] = len(c_values)

    for val_str, fp_map in cap_by_value.items():
        if len(fp_map) > 1:
            total_refs = sum(len(refs) for refs in fp_map.values())
            if total_refs > 1:
                fp_summary = {fp: len(refs) for fp, refs in fp_map.items()}
                consolidation_suggestions.append({
                    "type": "capacitor",
                    "suggestion": f"Capacitor value '{val_str}' used with "
                                  f"{len(fp_map)} different footprints — consider standardizing",
                    "value": val_str,
                    "footprint_breakdown": fp_summary,
                })

    # --- Single-use values (across all passive types) ---
    single_use_values = []
    for comp_type in ("resistor", "capacitor", "inductor"):
        vals: dict[str, int] = {}
        for c in by_type.get(comp_type, []):
            v = c.get("value", "")
            if v:
                vals[v] = vals.get(v, 0) + 1
        for v, count in vals.items():
            if count == 1:
                single_use_values.append({"type": comp_type, "value": v})

    # --- Count unique footprints ---
    all_footprints = set()
    for c in real:
        fp = c.get("footprint", "")
        if fp:
            all_footprints.add(fp)

    result: dict = {
        "unique_value_counts": unique_counts,
        "total_unique_footprints": len(all_footprints),
        "single_use_passive_values": len(single_use_values),
    }
    if consolidation_suggestions:
        result["consolidation_suggestions"] = consolidation_suggestions
    observations = []
    if len(single_use_values) > 5:
        observations.append(
            f"{len(single_use_values)} single-use passive values — "
            f"consider standardizing to reduce BOM line items"
        )
    if consolidation_suggestions:
        observations.append(
            f"{len(consolidation_suggestions)} potential consolidation(s) identified"
        )
    if observations:
        result["observations"] = observations
    return result


def analyze_test_coverage(components: list[dict], nets: dict, pin_net: dict) -> dict:
    """Test point and debug interface coverage analysis.

    Finds test points, checks which key nets have them, and identifies
    debug connectors (SWD, JTAG, UART headers).
    """
    # Find test points
    test_points = []
    tp_nets = set()
    for comp in components:
        ref = comp["reference"]
        fp = comp.get("footprint", "").lower()
        is_tp = (ref.startswith("TP") or
                 "testpoint" in fp or "test_point" in fp or
                 comp.get("value", "").lower() in ("testpoint", "test_point", "tp"))
        if is_tp:
            # Find what net it's on
            for pkey, (net_name, _) in pin_net.items():
                if pkey[0] == ref and net_name:
                    test_points.append({
                        "ref": ref,
                        "net": net_name,
                        "value": comp.get("value", ""),
                    })
                    tp_nets.add(net_name)
                    break

    # Find debug connectors
    debug_connectors = []
    debug_keywords = {
        "swd": ["SWDIO", "SWCLK", "SWO", "NRST"],
        "jtag": ["TDI", "TDO", "TCK", "TMS", "TRST"],
        "uart": ["TX", "RX", "TXD", "RXD"],
    }
    for comp in components:
        if comp["type"] != "connector":
            continue
        ref = comp["reference"]
        val = comp.get("value", "").lower()
        fp = comp.get("footprint", "").lower()
        lib = comp.get("lib_id", "").lower()
        combined = val + " " + fp + " " + lib

        # Collect connected net names for this connector (try pin_net first, fall back to nets dict)
        conn_nets = []
        for pkey, (net_name, _) in pin_net.items():
            if pkey[0] == ref and net_name:
                conn_nets.append(net_name)
        # Fallback: if pin_net gave few results (e.g., pin number collisions with "?"),
        # also scan the nets dict for this connector's connections
        if len(conn_nets) < 3:
            for net_name, net_info in nets.items():
                for p in net_info.get("pins", []):
                    if p["component"] == ref and net_name not in conn_nets:
                        conn_nets.append(net_name)
        conn_nets_upper = [n.upper() for n in conn_nets]

        for iface, expected_pins in debug_keywords.items():
            # Match on connector name/footprint/lib_id OR on connected net names
            name_match = iface in combined or any(p.lower() in combined for p in expected_pins)
            net_match = sum(1 for p in expected_pins if any(p in n for n in conn_nets_upper)) >= 2
            if name_match or net_match:
                debug_connectors.append({
                    "ref": ref,
                    "value": comp.get("value", ""),
                    "interface": iface,
                    "connected_nets": conn_nets,
                })
                break

    # Check key nets without test points
    key_net_patterns = {
        "power_rails": [],
        "i2c": ["SDA", "SCL"],
        "spi": ["MOSI", "MISO", "SCK", "SCLK", "SDI", "SDO"],
        "uart": ["TX", "RX", "TXD", "RXD", "UART"],
        "reset": ["RESET", "RST", "NRST", "~{RST}", "~{RESET}"],
    }

    uncovered_key_nets = []
    for net_name in nets:
        if net_name.startswith("__unnamed_"):
            continue
        if net_name in tp_nets:
            continue

        is_key = False
        category = ""

        # Power rails
        if _is_power_net_name(net_name) and not _is_ground_name(net_name):
            is_key = True
            category = "power_rail"

        # Signal patterns
        if not is_key:
            nu = net_name.upper()
            for cat, patterns in key_net_patterns.items():
                if cat == "power_rails":
                    continue
                for pat in patterns:
                    if pat in nu:
                        is_key = True
                        category = cat
                        break
                if is_key:
                    break

        if is_key:
            uncovered_key_nets.append({
                "net": net_name,
                "category": category,
            })

    result: dict = {
        "test_points": test_points,
        "test_point_count": len(test_points),
        "covered_nets": sorted(tp_nets),
    }
    if debug_connectors:
        result["debug_connectors"] = debug_connectors
    if uncovered_key_nets:
        result["uncovered_key_nets"] = uncovered_key_nets

    observations = []
    if not test_points:
        observations.append("No test points found in design")
    else:
        observations.append(f"{len(test_points)} test point(s) covering {len(tp_nets)} net(s)")
    if not debug_connectors:
        observations.append("No debug connectors (SWD/JTAG/UART) identified")
    if uncovered_key_nets:
        by_cat: dict[str, int] = {}
        for u in uncovered_key_nets:
            by_cat[u["category"]] = by_cat.get(u["category"], 0) + 1
        parts = [f"{count} {cat}" for cat, count in sorted(by_cat.items())]
        observations.append(f"Key nets without test points: {', '.join(parts)}")
    if observations:
        result["observations"] = observations
    return result


def analyze_assembly_complexity(components: list[dict]) -> dict:
    """Assembly complexity scoring.

    Counts components by package type, scores difficulty, and flags
    fine-pitch components.
    """
    real = [c for c in components
            if c["type"] not in ("power_symbol", "power_flag", "flag")]
    if not real:
        return {}

    # Package classification
    hard_smd = {"0201", "01005"}
    medium_hard_smd = {"0402"}
    medium_smd = {"0603", "0805"}
    easy_smd = {"1206", "1210", "1812", "2220", "2512"}

    # IC package patterns
    hard_ic_patterns = ["bga", "wlcsp", "ucsp", "flip_chip"]
    medium_hard_ic_patterns = ["qfn", "dfn", "mlp", "son", "vson", "wson", "udfn"]
    medium_ic_patterns = ["tssop", "msop", "ssop", "lqfp", "tqfp", "qfp"]
    easy_ic_patterns = ["soic", "sop", "sot-23", "sot23", "sot-223", "sot223",
                        "sot-89", "sot89", "sc-70", "sc70", "to-252", "dpak",
                        "to-263", "d2pak", "to-220", "to-92"]

    difficulty_counts: dict[str, int] = {"hard": 0, "medium": 0, "easy": 0}
    package_breakdown: dict[str, int] = {}
    fine_pitch_components = []
    tht_count = 0
    smd_count = 0

    def _extract_package_info(footprint: str) -> tuple[str, str]:
        """Returns (package_name, difficulty)."""
        if not footprint:
            return ("unknown", "medium")
        fp_lower = footprint.lower()

        # Check for THT
        if any(k in fp_lower for k in ("tht", "through_hole", "dip", "to-220", "to-92")):
            return ("THT", "easy")

        # Check SMD passive packages
        m = re.search(r'(\d{4,5})_\d{4,5}Metric', footprint)
        if m:
            pkg = m.group(1)
            if pkg in hard_smd:
                return (pkg, "hard")
            elif pkg in medium_hard_smd:
                return (pkg, "hard")
            elif pkg in medium_smd:
                return (pkg, "medium")
            elif pkg in easy_smd:
                return (pkg, "easy")
            return (pkg, "medium")

        # Check IC packages
        for pat in hard_ic_patterns:
            if pat in fp_lower:
                return (pat.upper(), "hard")
        for pat in medium_hard_ic_patterns:
            if pat in fp_lower:
                return (pat.upper(), "hard")
        for pat in medium_ic_patterns:
            if pat in fp_lower:
                return (pat.upper(), "medium")
        for pat in easy_ic_patterns:
            if pat in fp_lower:
                return (pat.upper(), "easy")

        return ("other_SMD", "medium")

    for comp in real:
        fp = comp.get("footprint", "")
        pkg_name, difficulty = _extract_package_info(fp)

        package_breakdown[pkg_name] = package_breakdown.get(pkg_name, 0) + 1
        difficulty_counts[difficulty] = difficulty_counts.get(difficulty, 0) + 1

        if pkg_name == "THT":
            tht_count += 1
        else:
            smd_count += 1

        # Check for fine pitch (<= 0.5mm)
        if fp:
            fp_lower = fp.lower()
            m = re.search(r'pitch[_\-]?(\d+\.?\d*)', fp_lower)
            if m:
                pitch = float(m.group(1))
                if pitch <= 0.5:
                    fine_pitch_components.append({
                        "ref": comp["reference"],
                        "value": comp.get("value", ""),
                        "footprint": fp,
                        "pitch_mm": pitch,
                    })
            # BGA/QFN often fine pitch
            elif any(k in fp_lower for k in ("bga", "wlcsp")):
                fine_pitch_components.append({
                    "ref": comp["reference"],
                    "value": comp.get("value", ""),
                    "footprint": fp,
                    "pitch_mm": None,
                    "note": "BGA/WLCSP — likely fine pitch",
                })

    # Compute complexity score (0-100)
    total = len(real)
    if total == 0:
        return {}
    score = 0
    score += (difficulty_counts["hard"] / total) * 80
    score += (difficulty_counts["medium"] / total) * 40
    score += (difficulty_counts["easy"] / total) * 10
    # Unique footprint penalty
    unique_fps = len(package_breakdown)
    if unique_fps > 15:
        score += min(20, (unique_fps - 15) * 2)
    score = min(100, round(score))

    result: dict = {
        "total_components": total,
        "smd_count": smd_count,
        "tht_count": tht_count,
        "complexity_score": score,
        "difficulty_breakdown": difficulty_counts,
        "package_breakdown": dict(sorted(package_breakdown.items(), key=lambda x: -x[1])),
        "unique_footprints": unique_fps,
    }
    if fine_pitch_components:
        result["fine_pitch_components"] = fine_pitch_components

    observations = []
    if score >= 70:
        observations.append(f"High assembly complexity (score {score}/100) — professional assembly recommended")
    elif score >= 40:
        observations.append(f"Moderate assembly complexity (score {score}/100)")
    else:
        observations.append(f"Low assembly complexity (score {score}/100) — hand assembly feasible")
    if difficulty_counts["hard"] > 0:
        observations.append(f"{difficulty_counts['hard']} hard-to-solder component(s) (0201/0402/BGA/QFN)")
    if fine_pitch_components:
        observations.append(f"{len(fine_pitch_components)} fine-pitch component(s) requiring stencil/reflow")
    if observations:
        result["observations"] = observations
    return result


def analyze_usb_compliance(components: list[dict], nets: dict,
                           signal_analysis: dict, pin_net: dict) -> dict:
    """USB spec compliance checks.

    Checks USB-C CC pull-downs, D+/D- series resistors, VBUS protection
    and decoupling, and ESD protection ICs.
    """
    comp_lookup = {c["reference"]: c for c in components}

    # Find USB connectors
    usb_connectors = []
    for comp in components:
        if comp["type"] != "connector":
            continue
        val = comp.get("value", "").upper()
        fp = comp.get("footprint", "").upper()
        lib = comp.get("lib_id", "").upper()
        combined = val + " " + fp + " " + lib
        if "USB" in combined:
            is_type_c = any(k in combined for k in ("USB_C", "USBC", "TYPE-C", "TYPE_C", "TYPEC"))
            usb_connectors.append({
                "ref": comp["reference"],
                "value": comp.get("value", ""),
                "is_type_c": is_type_c,
            })

    if not usb_connectors:
        return {}

    checklist = []

    for conn in usb_connectors:
        ref = conn["ref"]
        conn_checks: dict = {
            "connector": ref,
            "value": conn["value"],
            "is_type_c": conn["is_type_c"],
            "checks": {},
        }

        # Gather connector pin nets
        conn_pin_nets: dict[str, str] = {}  # pin_name -> net_name
        for pkey, (net_name, _) in pin_net.items():
            if pkey[0] == ref and net_name:
                # Find pin name
                if net_name in nets:
                    for p in nets[net_name]["pins"]:
                        if p["component"] == ref and p["pin_number"] == pkey[1]:
                            pname = p.get("pin_name", "").upper()
                            conn_pin_nets[pname] = net_name
                            break

        # --- CC1/CC2 pull-down check (Type-C only) ---
        if conn["is_type_c"]:
            cc1_ok = False
            cc2_ok = False
            for pname, net_name in conn_pin_nets.items():
                if "CC1" not in pname and "CC2" not in pname:
                    continue
                is_cc1 = "CC1" in pname
                # Check for 5.1k pull-down to GND on this net
                if net_name in nets:
                    for p in nets[net_name]["pins"]:
                        rc = comp_lookup.get(p["component"])
                        if not rc or rc["type"] != "resistor":
                            continue
                        r_val = parse_value(rc.get("value", ""))
                        if r_val and 4800 <= r_val <= 5600:
                            # Check other side goes to GND
                            rn1, _ = pin_net.get((rc["reference"], "1"), (None, None))
                            rn2, _ = pin_net.get((rc["reference"], "2"), (None, None))
                            other = rn2 if rn1 == net_name else rn1
                            if _is_ground_name(other):
                                if is_cc1:
                                    cc1_ok = True
                                else:
                                    cc2_ok = True
            conn_checks["checks"]["cc1_pulldown_5k1"] = "pass" if cc1_ok else "fail"
            conn_checks["checks"]["cc2_pulldown_5k1"] = "pass" if cc2_ok else "fail"

        # --- D+/D- series resistors ---
        dp_net = None
        dm_net = None
        for pname, net_name in conn_pin_nets.items():
            if pname in ("D+", "DP", "D_P", "USB_DP"):
                dp_net = net_name
            elif pname in ("D-", "DM", "D_M", "USB_DM", "DN", "D_N"):
                dm_net = net_name

        dp_series_r = False
        dm_series_r = False
        for data_net, is_dp in [(dp_net, True), (dm_net, False)]:
            if not data_net or data_net not in nets:
                continue
            for p in nets[data_net]["pins"]:
                rc = comp_lookup.get(p["component"])
                if not rc or rc["type"] != "resistor":
                    continue
                r_val = parse_value(rc.get("value", ""))
                if r_val and 20 <= r_val <= 33:
                    if is_dp:
                        dp_series_r = True
                    else:
                        dm_series_r = True

        if dp_net or dm_net:
            conn_checks["checks"]["dp_series_resistor"] = "pass" if dp_series_r else "info"
            conn_checks["checks"]["dm_series_resistor"] = "pass" if dm_series_r else "info"

        # --- VBUS protection and decoupling ---
        vbus_net = None
        for pname, net_name in conn_pin_nets.items():
            if pname in ("VBUS", "V+", "VCC", "VUSB"):
                vbus_net = net_name
                break

        if vbus_net and vbus_net in nets:
            # ESD/TVS on VBUS
            has_esd = False
            has_decoupling = False
            for p in nets[vbus_net]["pins"]:
                pc = comp_lookup.get(p["component"])
                if not pc:
                    continue
                if pc["type"] == "diode":
                    val_lower = pc.get("value", "").lower()
                    lib_lower = pc.get("lib_id", "").lower()
                    if any(k in val_lower or k in lib_lower
                           for k in ("tvs", "esd", "smaj", "smbj", "p6ke")):
                        has_esd = True
                if pc["type"] == "capacitor":
                    has_decoupling = True

            conn_checks["checks"]["vbus_esd_protection"] = "pass" if has_esd else "fail"
            conn_checks["checks"]["vbus_decoupling"] = "pass" if has_decoupling else "fail"

        # --- USB ESD protection ICs ---
        esd_ic_found = False
        esd_keywords = ("usblc", "prtr5v", "ip4", "sp0", "tpd", "esd", "pesd",
                        "rclamp", "nup", "lesd")
        for comp_c in components:
            if comp_c["type"] not in ("ic", "diode"):
                continue
            combined_lower = (comp_c.get("value", "") + " " + comp_c.get("lib_id", "")).lower()
            if any(k in combined_lower for k in esd_keywords):
                # Check if it's connected to a USB data net
                for pkey, (net_name, _) in pin_net.items():
                    if pkey[0] == comp_c["reference"]:
                        if net_name in (dp_net, dm_net, vbus_net):
                            esd_ic_found = True
                            break
                if esd_ic_found:
                    break

        conn_checks["checks"]["usb_esd_ic"] = "pass" if esd_ic_found else "info"

        checklist.append(conn_checks)

    # Summarize
    all_checks: dict[str, int] = {"pass": 0, "fail": 0, "info": 0}
    for conn_c in checklist:
        for status in conn_c["checks"].values():
            all_checks[status] = all_checks.get(status, 0) + 1

    observations = []
    if all_checks["fail"] > 0:
        observations.append(f"{all_checks['fail']} USB compliance check(s) failed")
    if all_checks["pass"] > 0:
        observations.append(f"{all_checks['pass']} USB compliance check(s) passed")
    if all_checks["info"] > 0:
        observations.append(f"{all_checks['info']} USB check(s) informational (optional)")

    result: dict = {
        "connectors": checklist,
        "summary": all_checks,
    }
    if observations:
        result["observations"] = observations
    return result


def analyze_inrush_current(components: list[dict], nets: dict,
                           signal_analysis: dict, pin_net: dict) -> dict:
    """Inrush current estimation.

    For each regulator, finds total output capacitance and estimates inrush
    current. Flags rails where output capacitance may cause startup issues.
    """
    comp_lookup = {c["reference"]: c for c in components}
    regulators = signal_analysis.get("power_regulators", [])
    if not regulators:
        return {}

    rails_result = []
    observations = []

    for reg in regulators:
        out_rail = reg.get("output_rail")
        if not out_rail or out_rail not in nets:
            continue

        # Find total output capacitance on this rail
        total_cap_f = 0.0
        output_caps = []
        for p in nets[out_rail]["pins"]:
            comp = comp_lookup.get(p["component"])
            if not comp or comp["type"] != "capacitor":
                continue
            cap_val = parse_value(comp.get("value", ""))
            if not cap_val or cap_val <= 0:
                continue
            # Check other pin goes to ground
            n1, _ = pin_net.get((comp["reference"], "1"), (None, None))
            n2, _ = pin_net.get((comp["reference"], "2"), (None, None))
            other = n2 if n1 == out_rail else n1
            if _is_ground_name(other):
                total_cap_f += cap_val
                output_caps.append({
                    "ref": comp["reference"],
                    "value": comp["value"],
                    "farads": cap_val,
                })

        if not output_caps:
            continue

        v_out = reg.get("estimated_vout") or _estimate_rail_voltage(out_rail)
        if not v_out or v_out <= 0:
            continue

        rail_entry: dict = {
            "regulator": reg["ref"],
            "output_rail": out_rail,
            "output_voltage": v_out,
            "topology": reg.get("topology", "unknown"),
            "output_caps": output_caps,
            "total_output_capacitance_uF": round(total_cap_f * 1e6, 2),
        }

        # Estimate inrush: I = C * dV/dt
        # For a typical soft-start time of ~1ms for switching regs, ~0.5ms for LDOs
        if reg.get("topology") == "LDO":
            soft_start_s = 0.5e-3
        else:
            soft_start_s = 1.0e-3

        inrush_a = total_cap_f * v_out / soft_start_s
        rail_entry["estimated_inrush_A"] = round(inrush_a, 3)
        rail_entry["assumed_soft_start_ms"] = round(soft_start_s * 1e3, 1)

        # Flag if inrush is high
        if inrush_a > 1.0:
            rail_entry["concern"] = "high_inrush"
            observations.append(
                f"{out_rail}: estimated inrush {inrush_a:.2f}A with "
                f"{total_cap_f * 1e6:.0f}uF output capacitance — "
                f"verify regulator can handle, consider soft-start"
            )
        elif inrush_a > 0.5:
            rail_entry["concern"] = "moderate_inrush"
            observations.append(
                f"{out_rail}: moderate inrush {inrush_a:.2f}A with "
                f"{total_cap_f * 1e6:.0f}uF output capacitance"
            )

        rails_result.append(rail_entry)

    if not rails_result:
        return {}

    result: dict = {"rails": rails_result}
    if observations:
        result["observations"] = observations
    return result


def analyze_schematic(path: str) -> dict:
    """Main analysis function. Returns complete structured data.

    For hierarchical designs (multi-sheet), recursively parses all sub-sheets
    and merges connectivity. Global and hierarchical labels connect nets across sheets.
    """
    # Detect legacy format
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        first_line = f.readline().strip()

    if first_line.startswith("EESchema"):
        return parse_legacy_schematic(path)

    # Parse root sheet and all sub-sheets recursively.
    # Multi-instance support: a sub-sheet file can be referenced multiple times
    # by the parent (e.g., 3 identical half-bridge phases). Each instance has a
    # unique UUID in the parent's (sheet) block. Component references are
    # remapped per instance via the (instances) block in each symbol, OR via
    # the centralized (symbol_instances) section in the root schematic.
    all_components = []
    all_wires = []
    all_labels = []
    all_junctions = []
    all_no_connects = []
    all_lib_symbols = {}
    all_text_annotations = []
    all_bus_elements = []
    root_title_block = {}
    sheets_parsed = []

    # Pre-parse root schematic's (symbol_instances) for fallback remapping.
    # Some KiCad projects (especially migrated ones) store instance-to-reference
    # mappings only here, not inline in each symbol's (instances) block.
    root_tree = parse_file(path)
    root_symbol_instances = extract_symbol_instances(root_tree)

    # Extract version info early so it's available during parsing.
    generator_version = get_value(root_tree, "generator_version") or "unknown"
    file_version = get_value(root_tree, "version") or "unknown"

    # Queue items are (file_path, instance_path). instance_path is the
    # hierarchical path prefix matching (symbol_instances) format:
    #   "" for root sheet (symbols have path "/<sym_uuid>")
    #   "/<sheet_uuid>" for direct child sheets
    #   "/<sheet_uuid>/<child_uuid>" for nested sheets
    to_parse = [(str(Path(path).resolve()), "")]
    parsed = set()  # Track (file_path, instance_path) pairs

    while to_parse:
        sheet_path, inst_path = to_parse.pop(0)
        parse_key = (sheet_path, inst_path)
        if parse_key in parsed:
            continue
        parsed.add(parse_key)

        (root, components, wires, labels, junctions, no_connects,
         sub_sheets, lib_symbols, text_annotations, bus_elements, title_block) = \
            parse_single_sheet(sheet_path, instance_uuid=inst_path,
                               symbol_instances=root_symbol_instances)

        # Tag elements with sheet index so coordinate-based net building
        # keeps each sheet's coordinate space separate (prevents false merges
        # when different sheets have wires at the same coordinates).
        sheet_idx = len(sheets_parsed)
        for c in components:
            c["_sheet"] = sheet_idx
        for w in wires:
            w["_sheet"] = sheet_idx
        for l in labels:
            l["_sheet"] = sheet_idx
        for j in junctions:
            j["_sheet"] = sheet_idx

        # KH-026: Namespace hierarchical labels per instance to prevent
        # multi-instance sub-sheets from merging unrelated nets.
        # Pin stubs (parent-side, tagged with _sheet_uuid) get prefixed with
        # inst_path + "/" + _sheet_uuid so they match the child instance's
        # labels.  Actual hierarchical_labels (child-side, no _sheet_uuid)
        # get prefixed with inst_path so they match their parent's pin stub.
        # Global labels are left unchanged; local labels are already scoped
        # by _sheet index.
        if inst_path:  # not root sheet
            for lbl in labels:
                if lbl["type"] == "hierarchical_label":
                    suuid = lbl.pop("_sheet_uuid", None)
                    if suuid:
                        # Pin stub on parent side — prefix with child instance path
                        lbl["name"] = inst_path + "/" + suuid + "/" + lbl["name"]
                    else:
                        # Actual hierarchical label inside the child sheet
                        lbl["name"] = inst_path + "/" + lbl["name"]
        else:
            # Root sheet — only pin stubs need prefixing (with child UUID)
            for lbl in labels:
                if lbl["type"] == "hierarchical_label":
                    suuid = lbl.pop("_sheet_uuid", None)
                    if suuid:
                        lbl["name"] = "/" + suuid + "/" + lbl["name"]

        all_components.extend(components)
        all_wires.extend(wires)
        all_labels.extend(labels)
        all_junctions.extend(junctions)
        all_no_connects.extend(no_connects)
        all_lib_symbols.update(lib_symbols)
        all_text_annotations.extend(text_annotations)
        all_bus_elements.append(bus_elements)
        if sheet_idx == 0:
            root_title_block = title_block
        sheets_parsed.append(sheet_path)

        for sub_path, sub_uuid in sub_sheets:
            sub_resolved = str(Path(sub_path).resolve())
            # Build full hierarchical path for the child sheet
            child_path = inst_path + "/" + sub_uuid if sub_uuid else inst_path
            if (sub_resolved, child_path) not in parsed:
                to_parse.append((sub_resolved, child_path))

    # Merge bus elements across sheets
    merged_bus = {"bus_wires": [], "bus_entries": [], "bus_aliases": []}
    for be in all_bus_elements:
        merged_bus["bus_wires"].extend(be.get("bus_wires", []))
        merged_bus["bus_entries"].extend(be.get("bus_entries", []))
        merged_bus["bus_aliases"].extend(be.get("bus_aliases", []))

    power_symbols = extract_power_symbols(all_components)

    # Build net map across all sheets
    nets = build_net_map(all_components, all_wires, all_labels, power_symbols, all_junctions)

    # Generate BOM
    bom = generate_bom(all_components)

    # Build pin-to-net map once, shared by all analysis functions
    pin_net = build_pin_to_net_map(nets)

    # Build shared analysis context (replaces repeated comp_lookup / parsed_values / known_power_rails)
    ctx = AnalysisContext(
        components=all_components,
        nets=nets,
        lib_symbols=all_lib_symbols,
        pin_net=pin_net,
        generator_version=generator_version,
    )

    # Identify subcircuits
    subcircuits = identify_subcircuits(all_components, nets, pin_net=pin_net)

    # Detailed IC pinout analysis for datasheet cross-referencing
    ic_analysis = analyze_ic_pinouts(all_components, nets, all_no_connects, pin_net=pin_net)

    # Analyze connectivity for issues
    connectivity_issues = analyze_connectivity(all_components, nets, all_no_connects)

    # Signal path and filter analysis
    signal_analysis = analyze_signal_paths(all_components, nets, all_lib_symbols, pin_net=pin_net)

    # Deep EE analysis: power domains, buses, differential pairs, ERC
    design_analysis = analyze_design_rules(all_components, nets, all_no_connects, signal_analysis, pin_net=pin_net)

    # ---- New Tier 1 + Tier 2 analyses ----

    # Reuse known_power_rails from context for PWR_FLAG audit
    known_power_rails = ctx.known_power_rails

    annotation_issues = check_annotation_completeness(all_components)
    label_shape_warnings = validate_label_shapes(all_labels, nets)
    pwr_flag_warnings = audit_pwr_flags(all_components, nets, known_power_rails)
    fp_filter_warnings = validate_footprint_filters(all_components, all_lib_symbols)
    sourcing_audit = audit_sourcing_fields(all_components)
    alternate_pins = summarize_alternate_pins(all_lib_symbols)
    ground_domains = classify_ground_domains(nets, all_components)
    bus_topology = analyze_bus_topology(merged_bus, all_labels, nets)
    wire_geometry = analyze_wire_geometry(all_wires)
    sim_readiness = check_simulation_readiness(all_components, all_lib_symbols)
    property_issues = audit_property_patterns(all_components)
    placement = spatial_clustering(all_components)
    pin_coverage = verify_pin_coverage(all_components, all_lib_symbols)
    instance_issues = check_instance_consistency(all_components)
    hier_label_analysis = validate_hierarchical_labels(all_labels, nets)
    generic_sym_warnings = check_generic_transistor_symbols(all_components, str(path))

    # ---- Tier 3: High-level design analyses ----
    pdn_analysis = analyze_pdn_impedance(all_components, nets, pin_net)
    sleep_current = analyze_sleep_current(all_components, nets, pin_net, signal_analysis)
    voltage_derating = analyze_voltage_derating(all_components, nets, signal_analysis, pin_net)
    power_budget = analyze_power_budget(all_components, nets, signal_analysis, pin_net)
    power_sequencing = analyze_power_sequencing(all_components, nets, signal_analysis, pin_net)
    bom_optimization = analyze_bom_optimization(all_components)
    test_coverage = analyze_test_coverage(all_components, nets, pin_net)
    assembly_complexity = analyze_assembly_complexity(all_components)
    usb_compliance = analyze_usb_compliance(all_components, nets, signal_analysis, pin_net)
    inrush_analysis = analyze_inrush_current(all_components, nets, signal_analysis, pin_net)

    # Add parsed numeric values to all passive components and category field
    for comp in all_components:
        comp["category"] = comp.get("type")
        if comp["type"] in ("resistor", "capacitor", "inductor", "ferrite_bead", "crystal"):
            pv = parse_value(comp.get("value", ""))
            if pv is not None:
                comp["parsed_value"] = pv

    # Statistics
    stats = compute_statistics(all_components, nets, bom, all_wires, all_no_connects)

    result = {
        "file": str(path),
        "kicad_version": generator_version,
        "file_version": file_version,
        "title_block": root_title_block,
        "statistics": stats,
        "bom": bom,
        "components": [
            {k: v for k, v in c.items() if k != "pins"}
            for c in all_components
            if c["type"] not in ("power_symbol", "power_flag", "flag")
        ],
        "nets": nets,
        "subcircuits": subcircuits,
        "ic_pin_analysis": ic_analysis,
        "signal_analysis": signal_analysis,
        "design_analysis": design_analysis,
        "connectivity_issues": connectivity_issues,
        "labels": all_labels,
        "no_connects": all_no_connects,
        "power_symbols": power_symbols,
        "annotation_issues": annotation_issues,
        "label_shape_warnings": label_shape_warnings,
        "pwr_flag_warnings": pwr_flag_warnings,
        "footprint_filter_warnings": fp_filter_warnings,
        "sourcing_audit": sourcing_audit,
        "ground_domains": ground_domains,
        "bus_topology": bus_topology,
        "wire_geometry": wire_geometry,
        "simulation_readiness": sim_readiness,
        "property_issues": property_issues,
        "placement_analysis": placement,
        "hierarchical_labels": hier_label_analysis,
    }

    # Only include non-empty optional sections
    if all_text_annotations:
        result["text_annotations"] = all_text_annotations
    if alternate_pins:
        result["alternate_pin_summary"] = alternate_pins
    if pin_coverage:
        result["pin_coverage_warnings"] = pin_coverage
    if instance_issues:
        result["instance_consistency_warnings"] = instance_issues
    if generic_sym_warnings:
        result["generic_symbol_warnings"] = generic_sym_warnings
    if pdn_analysis:
        result["pdn_impedance"] = pdn_analysis
    if sleep_current:
        result["sleep_current_audit"] = sleep_current
    if voltage_derating:
        result["voltage_derating"] = voltage_derating
    if power_budget:
        result["power_budget"] = power_budget
    if power_sequencing:
        result["power_sequencing"] = power_sequencing
    if bom_optimization:
        result["bom_optimization"] = bom_optimization
    if test_coverage:
        result["test_coverage"] = test_coverage
    if assembly_complexity:
        result["assembly_complexity"] = assembly_complexity
    if usb_compliance:
        result["usb_compliance"] = usb_compliance
    if inrush_analysis:
        result["inrush_analysis"] = inrush_analysis

    if len(sheets_parsed) > 1:
        result["sheets"] = sheets_parsed

    return result


def _get_schema():
    """Return JSON output schema description for --schema flag."""
    return {
        "file": "string — input file path",
        "kicad_version": "string — generator version",
        "file_version": "string",
        "title_block": {"title": "string", "date": "string", "rev": "string",
                        "company": "string", "comments": "{number: string}"},
        "statistics": {
            "total_components": "int", "unique_parts": "int", "dnp_parts": "int",
            "total_nets": "int", "total_wires": "int", "total_no_connects": "int",
            "component_types": "{type_name: count}", "power_rails": "[string]",
            "missing_mpn": "[reference_string]", "missing_footprint": "[reference_string]",
        },
        "bom": "[{value, footprint, mpn, manufacturer, digikey, mouser, lcsc, element14, datasheet, description, references: [string], quantity: int, dnp: bool, type}]",
        "components": "[{reference, value, lib_id, footprint, datasheet, description, mpn, manufacturer, digikey, mouser, lcsc, element14, x: float, y: float, angle: float, mirror_x: bool, mirror_y: bool, unit: int|null, uuid, in_bom: bool, dnp: bool, on_board: bool, type, keywords, pins: [{number, name, type}], parsed_value: {value: float, unit: string}}]",
        "nets": "{net_name: {name, pins: [{component, pin_number, pin_name, pin_type}], point_count: int}}",
        "subcircuits": "[{reference, path, sheet_name, sheet_file, instances: int}]",
        "ic_pin_analysis": "{ic_ref: {reference, value, pin_summary: {pin_number: {name, type, connected: bool, net}}, function, notes: [string]}}",
        "signal_analysis": {
            "voltage_dividers": "[{top_ref, bottom_ref, ratio, vout_estimated, input_net, output_net}]",
            "rc_filters": "[{resistor, capacitor, cutoff_frequency_hz, type: lowpass|highpass}]",
            "lc_filters": "[{inductor, capacitors, resonant_formatted}]",
            "power_regulators": "[{ref, value, lib_id, topology: ldo|buck|boost|buck_boost|inverting|..., input_rail, output_rail, vout_estimated, vref_source: lookup|heuristic}]",
            "crystal_circuits": "[{reference, value, frequency, type: passive|active_oscillator, load_caps}]",
            "opamp_circuits": "[{reference, configuration, gain}]",
            "transistor_circuits": "[{reference, type, load_classification}]",
            "bridge_circuits": "[{topology, fet_refs}]",
            "protection_devices": "[{type: tvs|esd|fuse|..., reference, protected_net}]",
            "current_sense": "[{shunt: {ref, value, ohms}, sense_ic: {ref, value, type}, high_net, low_net, max_current_50mV_A, max_current_100mV_A}]",
            "decoupling": "[{capacitor_ref, ic_ref, distance}]",
            "key_matrices": "[{rows, cols, diodes}]",
            "isolation_barriers": "[{isolator_ref, side_a_nets, side_b_nets}]",
            "ethernet_interfaces": "[{phy_ref, magnetics_ref, connector_ref}]",
            "memory_interfaces": "[{type, bus_signals}]",
            "rf_chains": "[{components_in_chain}]",
            "rf_matching": "[{antenna, antenna_value, topology: pi_match|L_match|T_match|matching_network, components: [{ref, type, value}], target_ic, target_value}]",
            "bms_systems": "[{ic_ref, cell_count}]",
        },
        "design_analysis": {
            "buses": "{i2c|spi|uart|can|sdio|differential_pairs: [bus_instances]}",
            "power_domains": "{ic_ref: domain_info}",
            "cross_domain_signals": "[signals crossing voltage domains]",
            "erc_warnings": "[string]",
        },
        "connectivity_issues": {"single_pin_nets": "[net_name]", "multi_driver_nets": "[net_name]", "floating_nets": "[net_name]"},
        "_optional_sections": "power_budget, power_sequencing, pdn_impedance, sleep_current_audit, usb_compliance, inrush_analysis, bom_optimization, test_coverage, assembly_complexity, sheets (multi-sheet only)",
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="KiCad Schematic Analyzer")
    parser.add_argument("schematic", nargs="?", help="Path to .kicad_sch file")
    parser.add_argument("--output", "-o", help="Output JSON file (default: stdout)")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output")
    parser.add_argument("--schema", action="store_true",
                        help="Print JSON output schema and exit")
    args = parser.parse_args()

    if args.schema:
        print(json.dumps(_get_schema(), indent=2))
        sys.exit(0)

    if not args.schematic:
        parser.error("the following arguments are required: schematic")

    result = analyze_schematic(args.schematic)

    indent = None if args.compact else 2
    output = json.dumps(result, indent=indent, default=str)

    if args.output:
        Path(args.output).write_text(output)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
