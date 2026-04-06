#!/usr/bin/env python3
"""Narrative context builder for kidoc engineering documentation.

Assembles focused context packages for each narrative section in a report.
The LLM (Claude, in the skill interaction) reads this context and writes
engineering prose.  This module does NOT generate prose — it prepares the
data the LLM needs.

Usage:
    # Context for one section
    python3 kidoc_narrative.py --analysis schematic.json --section power_design

    # Contexts for all NARRATIVE sections in a report
    python3 kidoc_narrative.py --analysis schematic.json --report reports/HDD.md

    # With additional data sources
    python3 kidoc_narrative.py --analysis schematic.json --section power_design \
        --spec spec.json --emc emc.json --thermal thermal.json --pcb pcb.json

Zero external dependencies — Python 3.8+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


# ======================================================================
# Section title mapping
# ======================================================================

SECTION_TITLES = {
    'system_overview': 'System Overview',
    'power_design': 'Power System Design',
    'signal_interfaces': 'Signal Interfaces',
    'analog_design': 'Analog Design',
    'thermal_analysis': 'Thermal Analysis',
    'emc_analysis': 'EMC Considerations',
    'pcb_design': 'PCB Design Details',
    'bom_summary': 'BOM Summary',
    'test_debug': 'Test and Debug',
    'executive_summary': 'Executive Summary',
    'compliance': 'Compliance and Standards',
    'mechanical_environmental': 'Mechanical / Environmental',
    # CE Technical File
    'ce_product_identification': 'Product Identification',
    'ce_essential_requirements': 'Essential Requirements',
    'ce_risk_assessment': 'Risk Assessment',
    # Design Review
    'review_summary': 'Review Summary',
    'review_action_items': 'Action Items',
    # ICD
    'icd_interface_list': 'Interface List',
    'icd_connector_details': 'Connector Details',
    'icd_electrical_characteristics': 'Electrical Characteristics',
    # Manufacturing
    'mfg_assembly_overview': 'Assembly Overview',
    'mfg_pcb_fab_notes': 'PCB Fabrication Notes',
    'mfg_assembly_instructions': 'Assembly Instructions',
    'mfg_test_procedures': 'Production Test Procedures',
}


# ======================================================================
# Per-section writing guidance
# ======================================================================

WRITING_GUIDANCE = {
    'system_overview': (
        "Write a concise overview of the system architecture. Explain what the "
        "board does, its key functional blocks, and how they connect. Reference "
        "specific component counts and key ICs by part number. Keep it to 2-3 "
        "paragraphs. Don't repeat the data table — explain what it means."
    ),
    'power_design': (
        "Explain the power distribution architecture. For each regulator, state "
        "the input source, output voltage, topology, and why that topology was "
        "chosen (efficiency for buck, simplicity for LDO). Reference specific "
        "component values and datasheet recommendations. Flag any deviations "
        "from reference designs. Discuss thermal considerations for high-power "
        "regulators."
    ),
    'signal_interfaces': (
        "Describe each communication bus: what devices are connected, what "
        "protocol is used, and any notable configuration (pull-up values, "
        "termination, address assignments). Reference specific component "
        "references and net names."
    ),
    'analog_design': (
        "For each analog subcircuit (filters, dividers, opamp stages), explain "
        "the design intent, calculated performance (cutoff frequency, gain, "
        "output voltage), and any SPICE validation results. Use quantitative "
        "language — 'the RC filter sets a -3dB point at 1.02 kHz' not "
        "'appropriate filtering is provided.'"
    ),
    'thermal_analysis': (
        "Summarize thermal analysis results. Identify components with the "
        "smallest thermal margins. Discuss the adequacy of thermal management "
        "(heat sinking, copper area, airflow). Reference specific junction "
        "temperatures and maximum ratings."
    ),
    'emc_analysis': (
        "Summarize EMC findings by severity. Highlight critical and high-risk "
        "findings with specific mitigation recommendations. Reference rule IDs "
        "and affected components. Discuss the overall EMC risk level and "
        "readiness for pre-compliance testing."
    ),
    'pcb_design': (
        "Describe the PCB stackup, layer usage, and key routing decisions. "
        "Reference board dimensions, layer count, and critical design rules. "
        "Discuss any DFM concerns."
    ),
    'bom_summary': (
        "Summarize the BOM: total unique parts, component types breakdown, "
        "any missing MPNs that need resolution. Note any single-source or "
        "long-lead-time components if known."
    ),
    'test_debug': (
        "Describe the test and debug strategy: available debug interfaces, "
        "test point placement, production test sequence, and programming "
        "access. Reference specific connector references and protocols."
    ),
    'executive_summary': (
        "Write a 1-2 paragraph executive summary. State what the board is, "
        "its key specifications, and the overall assessment (design maturity, "
        "risk level, readiness for next phase). Reference specific numbers "
        "from the analysis. This is the most important section — it's what "
        "decision-makers read."
    ),
    'compliance': (
        "List applicable standards and certification requirements. Discuss "
        "pre-compliance test results and gaps. Reference EMC risk score "
        "and specific findings that affect certification."
    ),
    'mechanical_environmental': (
        "Describe the physical design: board dimensions, mounting method, "
        "enclosure constraints, connector placement. State the operating "
        "temperature range and environmental requirements."
    ),
    # CE Technical File
    'ce_product_identification': (
        "Describe the product's intended use, target environment "
        "(indoor/outdoor, industrial/consumer), and user profile."
    ),
    'ce_essential_requirements': (
        "For each directive, describe how the design meets the essential "
        "requirements. Reference test reports, analysis data, and specific "
        "design features that ensure compliance."
    ),
    'ce_risk_assessment': (
        "Describe risk mitigation measures for each identified hazard. "
        "Reference specific design features, test results, and component "
        "ratings that address each risk."
    ),
    # Design Review
    'review_summary': (
        "Provide an overall assessment of design readiness. Highlight "
        "critical risks, summarize analyzer scores, and recommend go/no-go "
        "for the next design phase."
    ),
    'review_action_items': (
        "List action items from the review. Assign severity, owners, and "
        "due dates. Prioritize items that block fabrication."
    ),
    # ICD
    'icd_connector_details': (
        "For each connector, describe the interface protocol, signal levels, "
        "timing requirements, and mating connector specification."
    ),
    'icd_electrical_characteristics': (
        "Specify voltage levels, impedance, current limits, and timing "
        "requirements for each interface."
    ),
    # Manufacturing
    'mfg_assembly_overview': (
        "Describe assembly requirements: lead-free/leaded process, reflow "
        "profile, hand-solder requirements, special handling instructions."
    ),
    'mfg_pcb_fab_notes': (
        "Specify impedance control requirements, stackup details, material "
        "(FR-4/Rogers), and any special fabrication instructions."
    ),
    'mfg_assembly_instructions': (
        "Describe the assembly sequence: paste application, component "
        "placement, reflow, hand-solder steps, cleaning, conformal coating."
    ),
    'mfg_test_procedures': (
        "Describe pass/fail criteria for each test step. Include expected "
        "voltages, test fixture requirements, and failure modes."
    ),
}


# ======================================================================
# Section questions — specific questions to address per section
# ======================================================================

SECTION_QUESTIONS = {
    'system_overview': [
        "What is the primary function of this board?",
        "What are the key functional blocks and how do they interconnect?",
        "What are the main ICs and their roles?",
    ],
    'power_design': [
        "What is the input voltage source and range?",
        "Why was each regulator topology chosen (LDO vs. buck vs. boost)?",
        "Are output capacitor values consistent with datasheet recommendations?",
        "What is the worst-case power dissipation in each regulator?",
        "Is there adequate input decoupling?",
    ],
    'signal_interfaces': [
        "What communication protocols are used and between which devices?",
        "Are pull-up/termination resistor values appropriate for the bus speed?",
        "Is there adequate ESD protection on external interfaces?",
    ],
    'analog_design': [
        "What is the design intent of each analog subcircuit?",
        "Do calculated values (cutoff, gain, ratio) match the design targets?",
        "Have tolerances been analyzed for critical circuits?",
    ],
    'thermal_analysis': [
        "Which components have the smallest thermal margin?",
        "Is the total board dissipation manageable without forced airflow?",
        "Are thermal vias or heatsinks needed for any component?",
    ],
    'emc_analysis': [
        "What is the overall EMC risk level?",
        "Which findings are most likely to cause certification failure?",
        "What are the top mitigation priorities?",
    ],
    'pcb_design': [
        "Is the layer count adequate for the routing complexity?",
        "Are there impedance-controlled traces that need stackup specification?",
        "Are there any DFM violations or concerns?",
    ],
    'bom_summary': [
        "How many unique parts are there and is this reasonable for the design?",
        "Are there missing MPNs that need resolution before ordering?",
        "Are there any single-source or long-lead-time components?",
    ],
    'executive_summary': [
        "What does this board do in one sentence?",
        "What is the design maturity level (prototype, pre-production, production)?",
        "What are the top risks or open items?",
    ],
}


# ======================================================================
# Data extractors — pull focused data from analysis JSON
# ======================================================================

def _extract_overview_data(analysis: dict, **kwargs) -> str:
    """Extract system overview data as concise text summary."""
    parts = []
    stats = analysis.get('statistics', {})
    if stats:
        parts.append(
            f"Components: {stats.get('total_components', 0)} total, "
            f"{stats.get('unique_parts', 0)} unique"
        )
        parts.append(f"Nets: {stats.get('total_nets', 0)}")
        parts.append(f"Sheets: {stats.get('sheets', 1)}")

        types = stats.get('component_types', {})
        if types:
            type_str = ', '.join(f"{v} {k}" for k, v in
                                 sorted(types.items(), key=lambda x: -x[1]))
            parts.append(f"Component types: {type_str}")

        missing = stats.get('missing_mpn', [])
        if missing:
            parts.append(f"Missing MPNs: {len(missing)} components ({', '.join(missing[:5])}"
                         + (f" +{len(missing)-5}" if len(missing) > 5 else "") + ")")

    # Key ICs
    components = analysis.get('components', [])
    ics = [c for c in components if c.get('type') == 'ic']
    if ics:
        ic_list = [f"{c.get('reference', '?')} ({c.get('value', '?')})" for c in ics[:8]]
        parts.append(f"Key ICs: {', '.join(ic_list)}")

    # Power rails
    rails = stats.get('power_rails', [])
    if rails:
        rail_names = [r.get('name', '?') for r in rails if r.get('name')]
        parts.append(f"Power rails: {', '.join(rail_names)}")

    # Title block
    tb = analysis.get('title_block', {})
    if tb.get('title'):
        parts.insert(0, f"Project: {tb['title']}")

    return '\n'.join(parts) if parts else 'No system overview data available.'


def _extract_power_data(analysis: dict, **kwargs) -> str:
    """Extract power design data as concise text summary."""
    parts = []
    regs = analysis.get('signal_analysis', {}).get('power_regulators', [])
    if regs:
        parts.append(f"{len(regs)} voltage regulator(s):")
        for r in regs:
            line = f"  - {r.get('ref', '?')}: {r.get('value', '?')}"
            line += f", topology={r.get('topology', '?')}"
            if r.get('estimated_vout'):
                line += f", Vout={r['estimated_vout']:.3f}V"
            line += f", input={r.get('input_rail', '?')}"
            line += f", output={r.get('output_rail', '?')}"

            # Feedback divider
            fb = r.get('feedback_divider')
            if fb:
                line += (f", feedback R_top={fb.get('r_top', {}).get('ref', '?')}"
                         f"({fb.get('r_top', {}).get('value', '?')})"
                         f" R_bot={fb.get('r_bottom', {}).get('ref', '?')}"
                         f"({fb.get('r_bottom', {}).get('value', '?')})")

            # Input/output caps
            in_caps = r.get('input_capacitors', [])
            out_caps = r.get('output_capacitors', [])
            if in_caps:
                cap_str = ', '.join(f"{c.get('ref','?')}={c.get('value','?')}" for c in in_caps)
                line += f", input_caps=[{cap_str}]"
            if out_caps:
                cap_str = ', '.join(f"{c.get('ref','?')}={c.get('value','?')}" for c in out_caps)
                line += f", output_caps=[{cap_str}]"

            # Power dissipation
            pdiss = r.get('power_dissipation', {})
            if pdiss:
                line += (f", Pdiss={pdiss.get('estimated_pdiss_W', '?')}W"
                         f" (Vin={pdiss.get('vin_estimated_V', '?')}V"
                         f" dropout={pdiss.get('dropout_V', '?')}V)")

            parts.append(line)

    decoupling = analysis.get('signal_analysis', {}).get('decoupling_analysis', [])
    if decoupling:
        if isinstance(decoupling, list) and decoupling:
            total_caps = sum(len(d.get('capacitors', [])) for d in decoupling
                             if isinstance(d, dict))
            parts.append(f"Decoupling: {len(decoupling)} group(s), {total_caps} capacitor(s)")
            for d in decoupling:
                if isinstance(d, dict):
                    ic = d.get('ic_ref') or d.get('ic') or d.get('rail', '?')
                    caps = d.get('capacitors', [])
                    cap_str = ', '.join(f"{c.get('ref','?')}={c.get('value','?')}"
                                        for c in caps if isinstance(c, dict))
                    parts.append(f"  - {ic}: [{cap_str}]")
        elif isinstance(decoupling, dict):
            parts.append(f"Decoupling: {decoupling.get('total_caps', 0)} capacitor(s)")

    # Protection devices
    protection = analysis.get('signal_analysis', {}).get('protection_devices', [])
    if protection:
        parts.append(f"Protection devices: {len(protection)}")
        for p in protection[:5]:
            parts.append(f"  - {p.get('ref', '?')}: {p.get('value', '?')} ({p.get('type', '?')})")

    return '\n'.join(parts) if parts else 'No power design data available.'


def _extract_signal_data(analysis: dict, **kwargs) -> str:
    """Extract signal interface data as concise text summary."""
    parts = []

    bus_analysis = analysis.get('design_analysis', {}).get('bus_analysis', {})
    for bus_type in ('i2c', 'spi', 'uart', 'can'):
        buses = bus_analysis.get(bus_type, [])
        for bus in buses:
            signals = bus.get('signals', [])
            sig_names = [s.get('name', str(s)) if isinstance(s, dict) else str(s)
                         for s in signals]
            if sig_names and any(s for s in sig_names):
                bus_id = bus.get('bus_id', bus_type)
                parts.append(f"{bus_type.upper()} {bus_id}: {', '.join(sig_names[:10])}")

    # Level shifters
    shifters = analysis.get('signal_analysis', {}).get('level_shifters', [])
    if shifters:
        parts.append(f"Level shifters: {len(shifters)}")
        for s in shifters:
            parts.append(f"  - {s.get('ref', '?')}: {s.get('value', '')} "
                         f"({s.get('low_side_rail', '?')} <-> {s.get('high_side_rail', '?')})")

    # ESD coverage
    esd = analysis.get('signal_analysis', {}).get('esd_coverage_audit', [])
    if esd:
        unprotected = [e for e in esd if isinstance(e, dict) and e.get('coverage') == 'none']
        if unprotected:
            refs = [e.get('connector_ref', '?') for e in unprotected]
            parts.append(f"ESD gaps: {len(unprotected)} connector(s) with no protection "
                         f"({', '.join(refs[:5])})")

    # Differential pairs
    diff_pairs = analysis.get('design_analysis', {}).get('differential_pairs', [])
    if diff_pairs:
        parts.append(f"Differential pairs: {len(diff_pairs)}")
        for dp in diff_pairs[:5]:
            parts.append(f"  - {dp.get('name', '?')}: "
                         f"{dp.get('positive_net', '?')} / {dp.get('negative_net', '?')}")

    if not parts:
        parts.append('No formal buses or interfaces detected.')

    return '\n'.join(parts)


def _extract_analog_data(analysis: dict, **kwargs) -> str:
    """Extract analog design data as concise text summary."""
    parts = []
    sa = analysis.get('signal_analysis', {})

    # Voltage dividers
    dividers = sa.get('voltage_dividers', [])
    if dividers:
        parts.append(f"{len(dividers)} voltage divider(s):")
        for d in dividers:
            r_top = d.get('r_top', {})
            r_bot = d.get('r_bottom', {})
            parts.append(
                f"  - {r_top.get('ref', '?')}({r_top.get('value', '?')}) / "
                f"{r_bot.get('ref', '?')}({r_bot.get('value', '?')}), "
                f"ratio={d.get('ratio', '?'):.4f}, "
                f"top_net={d.get('top_net', '?')}, "
                f"mid_net={d.get('mid_net', '?')}, "
                f"bottom_net={d.get('bottom_net', '?')}"
            )
            connections = d.get('mid_point_connections', [])
            if connections:
                conn_str = ', '.join(
                    f"{c.get('component', '?')}.{c.get('pin_name', '?')}"
                    for c in connections if isinstance(c, dict)
                )
                parts.append(f"    connects to: {conn_str}")

    # Filters
    for ftype, label in [('rc_filters', 'RC filter'), ('lc_filters', 'LC filter')]:
        filters = sa.get(ftype, [])
        if filters:
            parts.append(f"{len(filters)} {label}(s):")
            for f in filters:
                r = f.get('resistor', {})
                c = f.get('capacitor', {})
                r_ref = r.get('ref', '?') if isinstance(r, dict) else str(r)
                c_ref = c.get('ref', '?') if isinstance(c, dict) else str(c)
                fc = f.get('cutoff_hz') or f.get('fc_hz')
                fc_str = _format_freq(fc) if fc else '?'
                parts.append(
                    f"  - {f.get('type', '?')}: {r_ref} + {c_ref}, "
                    f"fc={fc_str}, "
                    f"input={f.get('input_net', '?')}, output={f.get('output_net', '?')}"
                )

    # Opamp circuits
    opamps = sa.get('opamp_circuits', [])
    if opamps:
        parts.append(f"{len(opamps)} opamp circuit(s):")
        for o in opamps:
            parts.append(
                f"  - {o.get('ref', '?')} ({o.get('value', '?')}): "
                f"topology={o.get('topology', '?')}, gain={o.get('gain', '?')}"
            )

    # Crystal circuits
    crystals = sa.get('crystal_circuits', [])
    if crystals:
        parts.append(f"{len(crystals)} crystal circuit(s):")
        for c in crystals:
            freq = c.get('frequency_hz')
            parts.append(f"  - {c.get('ref', '?')}: {_format_freq(freq) if freq else '?'}")

    if not parts:
        parts.append('No analog subcircuits detected.')

    return '\n'.join(parts)


def _extract_thermal_data(analysis: dict, **kwargs) -> str:
    """Extract thermal analysis data as concise text summary."""
    thermal_data = kwargs.get('thermal_data')
    if not thermal_data:
        return 'No thermal analysis data available.'

    parts = []
    summary = thermal_data.get('summary', {})
    parts.append(f"Thermal score: {summary.get('thermal_score', '?')}/100")
    parts.append(f"Total board dissipation: {summary.get('total_board_dissipation_w', '?')}W")
    parts.append(f"Ambient: {summary.get('ambient_c', 25.0)}C")

    hottest = summary.get('hottest_component', {})
    if isinstance(hottest, dict):
        parts.append(f"Hottest: {hottest.get('ref', '?')} at {hottest.get('tj_estimated_c', '?')}C")
    elif hottest:
        parts.append(f"Hottest: {hottest}")

    above_85 = summary.get('components_above_85c', 0)
    parts.append(f"Components above 85C: {above_85}")

    assessments = thermal_data.get('thermal_assessments', [])
    if assessments:
        parts.append(f"\n{len(assessments)} thermal assessment(s):")
        for a in assessments:
            parts.append(
                f"  - {a.get('ref', '?')} ({a.get('value', '?')}): "
                f"Pdiss={a.get('pdiss_w', 0):.2f}W, "
                f"package={a.get('package', '?')}, "
                f"Rth_JA={a.get('rtheta_ja_effective', '?')}C/W, "
                f"Tj={a.get('tj_estimated_c', 0):.1f}C, "
                f"Tj_max={a.get('tj_max_c', '?')}C, "
                f"margin={a.get('margin_c', 0):.1f}C"
            )

    findings = thermal_data.get('findings', [])
    if findings:
        parts.append(f"\n{len(findings)} thermal finding(s):")
        for f in findings[:5]:
            parts.append(f"  - [{f.get('severity', '?')}] {f.get('title', '?')}")

    return '\n'.join(parts)


def _extract_emc_data(analysis: dict, **kwargs) -> str:
    """Extract EMC analysis data as concise text summary."""
    emc_data = kwargs.get('emc_data')
    if not emc_data:
        return 'No EMC analysis data available.'

    parts = []
    summary = emc_data.get('summary', {})
    parts.append(f"EMC risk score: {summary.get('emc_risk_score', '?')}/100")
    parts.append(
        f"Findings: {summary.get('critical', 0)} critical, "
        f"{summary.get('high', 0)} high, "
        f"{summary.get('medium', 0)} medium, "
        f"{summary.get('low', 0)} low"
    )
    parts.append(f"Target standard: {emc_data.get('target_standard', '?')}")

    findings = emc_data.get('findings', [])
    active = [f for f in findings if not f.get('suppressed')]
    if active:
        sev_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, 'INFO': 4}
        active.sort(key=lambda x: sev_order.get(x.get('severity', 'INFO'), 5))

        # Group by category
        by_cat: dict[str, int] = {}
        for f in active:
            cat = f.get('category', 'other')
            by_cat[cat] = by_cat.get(cat, 0) + 1
        parts.append(f"\nCategories: {', '.join(f'{c}({n})' for c, n in sorted(by_cat.items()))}")

        # Top findings
        top = [f for f in active if f.get('severity') in ('CRITICAL', 'HIGH')]
        if top:
            parts.append(f"\n{len(top)} critical/high finding(s):")
            for f in top[:10]:
                parts.append(
                    f"  - [{f.get('severity')}] {f.get('rule_id', '?')}: "
                    f"{f.get('title', '?')}"
                )
                if f.get('recommendation'):
                    parts.append(f"    Recommendation: {f['recommendation'][:120]}")

    return '\n'.join(parts)


def _extract_pcb_data(analysis: dict, **kwargs) -> str:
    """Extract PCB design data as concise text summary."""
    pcb_data = kwargs.get('pcb_data')
    if not pcb_data:
        return 'No PCB analysis data available.'

    parts = []
    stats = pcb_data.get('statistics', {})
    if stats:
        parts.append(f"Copper layers: {stats.get('copper_layers_used', '?')}")
        parts.append(f"Footprints: {stats.get('footprint_count', '?')} "
                     f"(front={stats.get('front_side', '?')}, back={stats.get('back_side', '?')})")
        parts.append(f"SMD: {stats.get('smd_count', '?')}, THT: {stats.get('tht_count', '?')}")
        parts.append(f"Tracks: {stats.get('track_segments', '?')} segments, "
                     f"{stats.get('total_track_length_mm', '?')}mm total")
        parts.append(f"Vias: {stats.get('via_count', '?')}")
        parts.append(f"Zones: {stats.get('zone_count', '?')}")
        parts.append(f"Board area: {stats.get('board_width_mm', '?')}mm x "
                     f"{stats.get('board_height_mm', '?')}mm "
                     f"({stats.get('board_area_mm2', '?')}mm2)")
        if stats.get('routing_complete') is not None:
            parts.append(f"Routing: {'complete' if stats['routing_complete'] else 'incomplete'}")

    layers = pcb_data.get('layers', [])
    copper_layers = [l for l in layers if l.get('type') == 'signal']
    if copper_layers:
        parts.append(f"Layer names: {', '.join(l.get('name', '?') for l in copper_layers)}")

    # DFM
    dfm = pcb_data.get('dfm_analysis', {})
    violations = dfm.get('violations', [])
    if violations:
        parts.append(f"\nDFM violations: {len(violations)}")
        for v in violations[:5]:
            parts.append(f"  - {v.get('type', '?')}: {v.get('message', '?')}")

    return '\n'.join(parts)


def _extract_bom_data(analysis: dict, **kwargs) -> str:
    """Extract BOM data as concise text summary."""
    parts = []

    stats = analysis.get('statistics', {})
    parts.append(f"Total components: {stats.get('total_components', '?')}")
    parts.append(f"Unique parts: {stats.get('unique_parts', '?')}")

    types = stats.get('component_types', {})
    if types:
        type_str = ', '.join(f"{k}: {v}" for k, v in
                             sorted(types.items(), key=lambda x: -x[1]))
        parts.append(f"By type: {type_str}")

    missing_mpn = stats.get('missing_mpn', [])
    if missing_mpn:
        parts.append(f"Missing MPNs: {len(missing_mpn)} ({', '.join(missing_mpn[:8])}"
                     + (f" +{len(missing_mpn)-8}" if len(missing_mpn) > 8 else "") + ")")

    dnp = stats.get('dnp_parts', 0)
    if dnp:
        parts.append(f"DNP parts: {dnp}")

    bom = analysis.get('bom', [])
    if bom:
        # Count parts with/without MPN
        with_mpn = sum(1 for b in bom if b.get('mpn'))
        parts.append(f"BOM lines: {len(bom)} ({with_mpn} with MPN)")

    return '\n'.join(parts) if parts else 'No BOM data available.'


def _extract_test_data(analysis: dict, **kwargs) -> str:
    """Extract test and debug data as concise text summary."""
    parts = []
    sa = analysis.get('signal_analysis', {})

    debug = sa.get('debug_interfaces', [])
    if debug:
        parts.append(f"{len(debug)} debug interface(s):")
        for d in debug:
            parts.append(f"  - {d.get('ref', '?')}: {d.get('type', '?')} ({d.get('protocol', '?')})")

    # LED indicators
    leds = sa.get('led_audit', [])
    if leds:
        parts.append(f"{len(leds)} LED indicator(s)")

    if not parts:
        parts.append('No debug interfaces detected.')

    return '\n'.join(parts)


def _extract_executive_data(analysis: dict, **kwargs) -> str:
    """Extract executive summary data combining all sources."""
    parts = []

    # Core stats
    stats = analysis.get('statistics', {})
    tb = analysis.get('title_block', {})
    if tb.get('title'):
        parts.append(f"Project: {tb['title']}")
    parts.append(
        f"Design: {stats.get('total_components', '?')} components, "
        f"{stats.get('unique_parts', '?')} unique, "
        f"{stats.get('total_nets', '?')} nets"
    )

    # Key ICs
    components = analysis.get('components', [])
    ics = [c for c in components if c.get('type') == 'ic']
    if ics:
        parts.append(f"Key ICs: {', '.join(c.get('value', '?') for c in ics[:5])}")

    # Power summary
    regs = analysis.get('signal_analysis', {}).get('power_regulators', [])
    if regs:
        rails = []
        for r in regs:
            vout = r.get('estimated_vout')
            rail = r.get('output_rail', '?')
            if vout:
                rails.append(f"{rail} ({vout:.1f}V)")
            else:
                rails.append(rail)
        parts.append(f"Power rails: {', '.join(rails)}")

    # EMC
    emc_data = kwargs.get('emc_data')
    if emc_data:
        emc_sum = emc_data.get('summary', {})
        parts.append(
            f"EMC risk: {emc_sum.get('emc_risk_score', '?')}/100 "
            f"({emc_sum.get('critical', 0)}C/{emc_sum.get('high', 0)}H/"
            f"{emc_sum.get('medium', 0)}M)"
        )

    # Thermal
    thermal_data = kwargs.get('thermal_data')
    if thermal_data:
        t_sum = thermal_data.get('summary', {})
        parts.append(f"Thermal score: {t_sum.get('thermal_score', '?')}/100")

    # PCB
    pcb_data = kwargs.get('pcb_data')
    if pcb_data:
        pcb_stats = pcb_data.get('statistics', {})
        parts.append(
            f"PCB: {pcb_stats.get('copper_layers_used', '?')} layers, "
            f"{pcb_stats.get('board_width_mm', '?')}x{pcb_stats.get('board_height_mm', '?')}mm"
        )

    # Missing MPNs
    missing = stats.get('missing_mpn', [])
    if missing:
        parts.append(f"Missing MPNs: {len(missing)}")

    return '\n'.join(parts)


def _extract_compliance_data(analysis: dict, **kwargs) -> str:
    """Extract compliance-relevant data."""
    parts = []

    emc_data = kwargs.get('emc_data')
    if emc_data:
        parts.append(f"Target standard: {emc_data.get('target_standard', '?')}")
        summary = emc_data.get('summary', {})
        parts.append(f"EMC risk score: {summary.get('emc_risk_score', '?')}/100")
        parts.append(
            f"Critical: {summary.get('critical', 0)}, High: {summary.get('high', 0)}"
        )

    esd = analysis.get('signal_analysis', {}).get('esd_coverage_audit', [])
    if esd:
        unprotected = [e for e in esd if isinstance(e, dict) and e.get('coverage') == 'none']
        parts.append(f"ESD: {len(esd)} connectors audited, {len(unprotected)} unprotected")

    if not parts:
        parts.append('No compliance data available.')

    return '\n'.join(parts)


def _extract_mechanical_data(analysis: dict, **kwargs) -> str:
    """Extract mechanical/environmental data."""
    parts = []
    pcb_data = kwargs.get('pcb_data')
    if pcb_data:
        outline = pcb_data.get('board_outline', {})
        if outline:
            parts.append(f"Board: {outline.get('width_mm', '?')}mm x "
                         f"{outline.get('height_mm', '?')}mm")
        stats = pcb_data.get('statistics', {})
        parts.append(f"Footprints: front={stats.get('front_side', '?')}, "
                     f"back={stats.get('back_side', '?')}")

    if not parts:
        parts.append('No mechanical data available.')

    return '\n'.join(parts)


# ======================================================================
# Extractor registry
# ======================================================================

SECTION_DATA_EXTRACTORS = {
    'system_overview': _extract_overview_data,
    'power_design': _extract_power_data,
    'signal_interfaces': _extract_signal_data,
    'analog_design': _extract_analog_data,
    'thermal_analysis': _extract_thermal_data,
    'emc_analysis': _extract_emc_data,
    'pcb_design': _extract_pcb_data,
    'bom_summary': _extract_bom_data,
    'test_debug': _extract_test_data,
    'executive_summary': _extract_executive_data,
    'compliance': _extract_compliance_data,
    'mechanical_environmental': _extract_mechanical_data,
}


# ======================================================================
# Datasheet and SPICE notes
# ======================================================================

def _build_datasheet_notes(section_type: str, analysis: dict,
                           extractions: dict | None) -> str:
    """Build datasheet notes relevant to a section."""
    if not extractions:
        return ''

    parts = []

    if section_type == 'power_design':
        regs = analysis.get('signal_analysis', {}).get('power_regulators', [])
        for r in regs:
            value = r.get('value', '')
            ref = r.get('ref', '')
            # Look for extraction by MPN or value
            for key in (r.get('mpn', ''), value):
                if key and key in extractions:
                    ext = extractions[key]
                    parts.append(f"{ref} ({value}): {_summarize_extraction(ext)}")

    elif section_type == 'analog_design':
        opamps = analysis.get('signal_analysis', {}).get('opamp_circuits', [])
        for o in opamps:
            value = o.get('value', '')
            ref = o.get('ref', '')
            for key in (o.get('mpn', ''), value):
                if key and key in extractions:
                    ext = extractions[key]
                    parts.append(f"{ref} ({value}): {_summarize_extraction(ext)}")

    return '\n'.join(parts)


def _summarize_extraction(ext: dict) -> str:
    """One-line summary of a datasheet extraction."""
    parts = []
    if ext.get('voltage_ratings'):
        parts.append(f"Vmax={ext['voltage_ratings']}")
    if ext.get('operating_temp'):
        parts.append(f"Temp={ext['operating_temp']}")
    if ext.get('package'):
        parts.append(f"Package={ext['package']}")
    return '; '.join(parts) if parts else '(extraction available)'


def _build_spice_notes(section_type: str, analysis: dict,
                       spice_data: dict | None) -> str:
    """Build SPICE simulation notes relevant to a section."""
    if not spice_data:
        return ''

    results = spice_data.get('simulation_results', [])
    if not results:
        return ''

    parts = []
    for r in results:
        subcircuit_type = r.get('subcircuit_type', '')
        # Match SPICE results to section type
        relevant = False
        if section_type == 'analog_design' and subcircuit_type in ('filter', 'divider', 'opamp'):
            relevant = True
        elif section_type == 'power_design' and subcircuit_type in ('regulator', 'lc_filter'):
            relevant = True

        if relevant:
            parts.append(
                f"SPICE {r.get('name', '?')}: "
                f"measured={r.get('measured_value', '?')}, "
                f"expected={r.get('expected_value', '?')}, "
                f"{'PASS' if r.get('pass') else 'FAIL'}"
            )

    return '\n'.join(parts)


# ======================================================================
# Cross-reference builder
# ======================================================================

def _build_cross_references(section_type: str, analysis: dict,
                            emc_data: dict | None = None,
                            thermal_data: dict | None = None,
                            pcb_data: dict | None = None) -> str:
    """Brief references to related sections."""
    parts = []

    if section_type == 'power_design':
        if thermal_data:
            s = thermal_data.get('summary', {})
            parts.append(f"See Thermal: score {s.get('thermal_score', '?')}/100, "
                         f"{s.get('components_above_85c', 0)} above 85C")
        if emc_data:
            dc_findings = [f for f in emc_data.get('findings', [])
                           if f.get('category') == 'decoupling' and not f.get('suppressed')]
            if dc_findings:
                parts.append(f"See EMC: {len(dc_findings)} decoupling finding(s)")

    elif section_type == 'emc_analysis':
        regs = analysis.get('signal_analysis', {}).get('power_regulators', [])
        if regs:
            parts.append(f"See Power: {len(regs)} regulator(s)")
        if pcb_data:
            parts.append(f"See PCB: {pcb_data.get('statistics', {}).get('copper_layers_used', '?')} layers")

    elif section_type == 'thermal_analysis':
        regs = analysis.get('signal_analysis', {}).get('power_regulators', [])
        pdiss_regs = [r for r in regs if r.get('power_dissipation')]
        if pdiss_regs:
            parts.append(f"See Power: {len(pdiss_regs)} regulator(s) with dissipation data")

    elif section_type == 'executive_summary':
        if emc_data:
            s = emc_data.get('summary', {})
            parts.append(f"EMC: {s.get('emc_risk_score', '?')}/100")
        if thermal_data:
            s = thermal_data.get('summary', {})
            parts.append(f"Thermal: {s.get('thermal_score', '?')}/100")

    return '\n'.join(parts)


# ======================================================================
# Helper
# ======================================================================

def _format_freq(hz) -> str:
    """Format frequency value for display."""
    if hz is None:
        return '?'
    try:
        hz = float(hz)
    except (TypeError, ValueError):
        return str(hz)
    if hz >= 1e9:
        return f"{hz/1e9:.2f}GHz"
    if hz >= 1e6:
        return f"{hz/1e6:.2f}MHz"
    if hz >= 1e3:
        return f"{hz/1e3:.2f}kHz"
    return f"{hz:.2f}Hz"


# ======================================================================
# Main context builder
# ======================================================================

def build_narrative_context(section_id: str, section_type: str,
                            analysis: dict,
                            spec: dict | None = None,
                            extractions: dict | None = None,
                            spice_data: dict | None = None,
                            existing_narrative: str | None = None,
                            emc_data: dict | None = None,
                            thermal_data: dict | None = None,
                            pcb_data: dict | None = None) -> dict:
    """Build focused context for LLM narrative generation.

    Returns a dict with all the data the LLM needs to write prose for
    one section.  The LLM should NOT see the full analysis JSON — only
    this focused slice.
    """
    # Audience/tone from spec
    audience = ''
    tone = 'technical'
    questions = []
    if spec:
        audience = spec.get('audience', '')
        tone = spec.get('tone', 'technical')
        # Per-section questions from spec override defaults
        for s in spec.get('sections', []):
            if s.get('id') == section_id or s.get('type') == section_type:
                questions = s.get('questions', [])
                break

    if not questions:
        questions = list(SECTION_QUESTIONS.get(section_type, []))

    # Extract focused data
    extractor = SECTION_DATA_EXTRACTORS.get(section_type)
    if extractor:
        data_summary = extractor(
            analysis,
            emc_data=emc_data,
            thermal_data=thermal_data,
            pcb_data=pcb_data,
        )
    else:
        data_summary = 'No data extractor available for this section type.'

    # Datasheet notes
    datasheet_notes = _build_datasheet_notes(section_type, analysis, extractions)

    # SPICE notes
    spice_notes = _build_spice_notes(section_type, analysis, spice_data)

    # Cross-references
    cross_refs = _build_cross_references(
        section_type, analysis,
        emc_data=emc_data,
        thermal_data=thermal_data,
        pcb_data=pcb_data,
    )

    # Writing guidance
    guidance = WRITING_GUIDANCE.get(section_type, '')

    return {
        'section_id': section_id,
        'section_type': section_type,
        'section_title': SECTION_TITLES.get(section_type, section_type),
        'audience': audience,
        'tone': tone,
        'questions': questions,
        'data_summary': data_summary,
        'datasheet_notes': datasheet_notes,
        'spice_notes': spice_notes,
        'existing_text': existing_narrative or '',
        'cross_references': cross_refs,
        'writing_guidance': guidance,
    }


# ======================================================================
# Batch context builder
# ======================================================================

# Pattern matching narrative placeholders in scaffold output.
# The scaffold emits italic placeholder text: *[hint text]*
# Nearby headings identify the section.
_NARRATIVE_PLACEHOLDER_RE = re.compile(r'^\*\[.+?\]\*$')

# Map heading text to section types
_HEADING_TO_SECTION = {
    'executive summary': 'executive_summary',
    'system overview': 'system_overview',
    'power system design': 'power_design',
    'signal interfaces': 'signal_interfaces',
    'analog design': 'analog_design',
    'thermal analysis': 'thermal_analysis',
    'emc considerations': 'emc_analysis',
    'pcb design details': 'pcb_design',
    'mechanical / environmental': 'mechanical_environmental',
    'bom summary': 'bom_summary',
    'test and debug': 'test_debug',
    'compliance and standards': 'compliance',
    # CE
    'product identification': 'ce_product_identification',
    'essential requirements': 'ce_essential_requirements',
    'risk assessment': 'ce_risk_assessment',
    # Design Review
    'review summary': 'review_summary',
    'action items': 'review_action_items',
    # ICD
    'interface list': 'icd_interface_list',
    'connector details': 'icd_connector_details',
    'electrical characteristics': 'icd_electrical_characteristics',
    # Manufacturing
    'assembly overview': 'mfg_assembly_overview',
    'pcb fabrication notes': 'mfg_pcb_fab_notes',
    'assembly instructions': 'mfg_assembly_instructions',
    'production test procedures': 'mfg_test_procedures',
}


def _detect_sections_from_markdown(md_text: str) -> list[dict]:
    """Detect narrative sections from markdown scaffold.

    Returns list of {'section_type': str, 'existing_text': str|None}
    for each section that has a narrative placeholder or where the user
    has already written content.
    """
    lines = md_text.split('\n')
    sections = []
    current_section = None

    for line in lines:
        stripped = line.strip()

        # Track headings to determine current section
        if stripped.startswith('#'):
            heading_text = stripped.lstrip('#').strip()
            # Remove numbering like "2. " or "## 3. "
            heading_clean = re.sub(r'^\d+\.\s*', '', heading_text).lower()
            section_type = _HEADING_TO_SECTION.get(heading_clean)
            if section_type:
                current_section = section_type

        # Detect narrative placeholder
        if current_section and _NARRATIVE_PLACEHOLDER_RE.match(stripped):
            sections.append({
                'section_type': current_section,
                'existing_text': None,
            })

    return sections


def build_all_narrative_contexts(report_md_path: str,
                                 analysis: dict,
                                 spec: dict | None = None,
                                 extractions: dict | None = None,
                                 spice_data: dict | None = None,
                                 emc_data: dict | None = None,
                                 thermal_data: dict | None = None,
                                 pcb_data: dict | None = None) -> list[dict]:
    """Build contexts for all narrative sections in a report.

    Reads the markdown file, finds all narrative placeholder sections,
    and builds context for each.
    """
    with open(report_md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    detected = _detect_sections_from_markdown(md_text)

    contexts = []
    for det in detected:
        section_type = det['section_type']
        ctx = build_narrative_context(
            section_id=section_type,
            section_type=section_type,
            analysis=analysis,
            spec=spec,
            extractions=extractions,
            spice_data=spice_data,
            existing_narrative=det.get('existing_text'),
            emc_data=emc_data,
            thermal_data=thermal_data,
            pcb_data=pcb_data,
        )
        contexts.append(ctx)

    return contexts


# ======================================================================
# Output formatting
# ======================================================================

def format_context(ctx: dict) -> str:
    """Format a narrative context dict as readable text for the LLM."""
    lines = []
    lines.append(f"=== NARRATIVE CONTEXT: {ctx['section_title']} ===")
    lines.append(f"Section: {ctx['section_id']} (type: {ctx['section_type']})")

    if ctx.get('audience'):
        lines.append(f"Audience: {ctx['audience']}")
    if ctx.get('tone'):
        lines.append(f"Tone: {ctx['tone']}")

    lines.append("")
    lines.append("--- DATA SUMMARY ---")
    lines.append(ctx.get('data_summary', '(none)'))

    if ctx.get('datasheet_notes'):
        lines.append("")
        lines.append("--- DATASHEET NOTES ---")
        lines.append(ctx['datasheet_notes'])

    if ctx.get('spice_notes'):
        lines.append("")
        lines.append("--- SPICE VALIDATION ---")
        lines.append(ctx['spice_notes'])

    if ctx.get('cross_references'):
        lines.append("")
        lines.append("--- CROSS-REFERENCES ---")
        lines.append(ctx['cross_references'])

    if ctx.get('existing_text'):
        lines.append("")
        lines.append("--- EXISTING NARRATIVE (rewrite if stale) ---")
        lines.append(ctx['existing_text'])

    if ctx.get('questions'):
        lines.append("")
        lines.append("--- QUESTIONS TO ADDRESS ---")
        for q in ctx['questions']:
            lines.append(f"  - {q}")

    if ctx.get('writing_guidance'):
        lines.append("")
        lines.append("--- WRITING GUIDANCE ---")
        lines.append(ctx['writing_guidance'])

    lines.append("")
    return '\n'.join(lines)


# ======================================================================
# CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Build narrative context for kidoc report sections')
    parser.add_argument('--analysis', required=True,
                        help='Path to schematic analysis JSON')
    parser.add_argument('--section',
                        help='Build context for one section type')
    parser.add_argument('--report',
                        help='Build contexts for all narrative sections in a markdown file')
    parser.add_argument('--spec',
                        help='Document spec JSON (for audience/tone/questions)')
    parser.add_argument('--emc',
                        help='Path to EMC analysis JSON')
    parser.add_argument('--thermal',
                        help='Path to thermal analysis JSON')
    parser.add_argument('--pcb',
                        help='Path to PCB analysis JSON')
    parser.add_argument('--extractions',
                        help='Path to datasheet extractions directory or JSON')
    parser.add_argument('--spice',
                        help='Path to SPICE results JSON')
    args = parser.parse_args()

    # Load analysis
    with open(args.analysis, 'r', encoding='utf-8') as f:
        analysis = json.load(f)

    # Load optional data sources
    spec = None
    if args.spec:
        with open(args.spec, 'r', encoding='utf-8') as f:
            spec = json.load(f)

    emc_data = None
    if args.emc:
        with open(args.emc, 'r', encoding='utf-8') as f:
            emc_data = json.load(f)
    else:
        # Try to find emc.json alongside the analysis
        emc_path = os.path.join(os.path.dirname(args.analysis), 'emc.json')
        if os.path.isfile(emc_path):
            with open(emc_path, 'r', encoding='utf-8') as f:
                emc_data = json.load(f)

    thermal_data = None
    if args.thermal:
        with open(args.thermal, 'r', encoding='utf-8') as f:
            thermal_data = json.load(f)
    else:
        thermal_path = os.path.join(os.path.dirname(args.analysis), 'thermal.json')
        if os.path.isfile(thermal_path):
            with open(thermal_path, 'r', encoding='utf-8') as f:
                thermal_data = json.load(f)

    pcb_data = None
    if args.pcb:
        with open(args.pcb, 'r', encoding='utf-8') as f:
            pcb_data = json.load(f)
    else:
        pcb_path = os.path.join(os.path.dirname(args.analysis), 'pcb.json')
        if os.path.isfile(pcb_path):
            with open(pcb_path, 'r', encoding='utf-8') as f:
                pcb_data = json.load(f)

    spice_data = None
    if args.spice:
        with open(args.spice, 'r', encoding='utf-8') as f:
            spice_data = json.load(f)

    extractions = None
    if args.extractions:
        ext_path = args.extractions
        if os.path.isfile(ext_path):
            with open(ext_path, 'r', encoding='utf-8') as f:
                extractions = json.load(f)
        elif os.path.isdir(ext_path):
            # Load all JSONs from directory keyed by filename stem
            extractions = {}
            for fname in os.listdir(ext_path):
                if fname.endswith('.json'):
                    fpath = os.path.join(ext_path, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            extractions[fname.replace('.json', '')] = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        pass

    # Build and output context
    if args.section:
        ctx = build_narrative_context(
            section_id=args.section,
            section_type=args.section,
            analysis=analysis,
            spec=spec,
            extractions=extractions,
            spice_data=spice_data,
            emc_data=emc_data,
            thermal_data=thermal_data,
            pcb_data=pcb_data,
        )
        print(format_context(ctx))

    elif args.report:
        if not os.path.isfile(args.report):
            print(f"Error: report file not found: {args.report}", file=sys.stderr)
            sys.exit(1)
        contexts = build_all_narrative_contexts(
            report_md_path=args.report,
            analysis=analysis,
            spec=spec,
            extractions=extractions,
            spice_data=spice_data,
            emc_data=emc_data,
            thermal_data=thermal_data,
            pcb_data=pcb_data,
        )
        if not contexts:
            print("No narrative sections found in report.", file=sys.stderr)
            sys.exit(0)
        for ctx in contexts:
            print(format_context(ctx))

    else:
        # No --section or --report: list available section types
        print("Available section types:")
        for stype in sorted(SECTION_DATA_EXTRACTORS.keys()):
            title = SECTION_TITLES.get(stype, stype)
            print(f"  {stype:30s} {title}")
        print(f"\n{len(SECTION_DATA_EXTRACTORS)} extractors available.")
        print("\nUse --section <type> or --report <file.md> to generate context.")


if __name__ == '__main__':
    main()
