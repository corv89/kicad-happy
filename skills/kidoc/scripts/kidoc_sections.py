"""Section content generators for the markdown scaffold.

Each function generates markdown for one document section, including
AUTO-START/AUTO-END markers for auto-generated content and NARRATIVE
placeholders for prose sections.

Zero external dependencies — Python stdlib only.
"""

from __future__ import annotations

from kidoc_tables import (
    markdown_table, format_voltage, format_frequency,
    format_current, format_capacitance, format_resistance,
)


def _auto(section_id: str, content: str) -> str:
    """Wrap content in AUTO-START/AUTO-END markers."""
    return (f"<!-- AUTO-START: {section_id} -->\n"
            f"{content}\n"
            f"<!-- AUTO-END: {section_id} -->")


def _narrative(section_id: str, hint: str = "") -> str:
    """Generate a NARRATIVE placeholder."""
    return (f"<!-- NARRATIVE: {section_id} -->\n"
            f"*[{hint or 'Describe the design decisions and rationale for this section.'}]*\n"
            f"<!-- END-NARRATIVE: {section_id} -->")


# ======================================================================
# Front matter
# ======================================================================

def section_front_matter(config: dict, doc_type: str) -> str:
    """Generate title page and revision history."""
    project = config.get('project', {})
    name = project.get('name', 'Untitled Project')
    number = project.get('number', '')
    revision = project.get('revision', '')
    company = project.get('company', '')
    author = project.get('author', '')

    doc_titles = {
        'hdd': 'Hardware Design Description',
        'ce_technical_file': 'CE Technical File',
        'design_review': 'Design Review Package',
        'icd': 'Interface Control Document',
        'manufacturing': 'Manufacturing Transfer Package',
    }
    doc_title = doc_titles.get(doc_type, 'Engineering Document')

    lines = [f"# {doc_title}"]
    lines.append("")
    lines.append(_auto("front_matter_info", "\n".join(filter(None, [
        f"**Project:** {name}" if name else None,
        f"**Document Number:** {number}" if number else None,
        f"**Revision:** {revision}" if revision else None,
        f"**Company:** {company}" if company else None,
        f"**Author:** {author}" if author else None,
    ]))))
    lines.append("")

    # Revision history
    rev_history = config.get('reports', {}).get('revision_history', [])
    if rev_history:
        rows = [[r.get('rev', ''), r.get('date', ''), r.get('author', ''),
                 r.get('description', '')] for r in rev_history]
        lines.append("## Revision History")
        lines.append("")
        lines.append(_auto("revision_history",
                           markdown_table(['Rev', 'Date', 'Author', 'Description'], rows)))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# System overview
# ======================================================================

def section_system_overview(analysis: dict, diagrams_dir: str) -> str:
    """Generate system overview section."""
    lines = ["## 2. System Overview"]
    lines.append("")

    # Architecture diagram
    lines.append(_auto("architecture_diagram",
                       f"![System Architecture]({diagrams_dir}/architecture.svg)"))
    lines.append("")
    lines.append(_narrative("system_overview_description",
                            "Describe the system's purpose, key functions, and "
                            "high-level architecture. Reference the block diagram above."))
    lines.append("")

    # Statistics table
    stats = analysis.get('statistics', {})
    if stats:
        rows = [
            ['Total components', str(stats.get('total_components', 0))],
            ['Unique parts', str(stats.get('unique_parts', 0))],
            ['Nets', str(stats.get('total_nets', 0))],
            ['Sheets', str(stats.get('sheets', 1))],
        ]
        lines.append(_auto("statistics_table",
                           markdown_table(['Metric', 'Value'], rows)))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Power design
# ======================================================================

def section_power_design(analysis: dict, diagrams_dir: str) -> str:
    """Generate power system design section."""
    lines = ["## 3. Power System Design"]
    lines.append("")

    # Power tree diagram
    lines.append(_auto("power_tree_diagram",
                       f"![Power Tree]({diagrams_dir}/power_tree.svg)"))
    lines.append("")
    lines.append(_narrative("power_design_rationale",
                            "Describe the power architecture: input voltage range, "
                            "why this topology was chosen, efficiency targets, thermal constraints."))
    lines.append("")

    # Power regulators table
    regulators = analysis.get('signal_analysis', {}).get('power_regulators', [])
    if regulators:
        rows = []
        for reg in regulators:
            vout = reg.get('estimated_vout') or reg.get('output_voltage')
            rows.append([
                reg.get('ref', '?'),
                reg.get('value', ''),
                reg.get('topology', ''),
                reg.get('input_rail', '?'),
                reg.get('output_rail', '?'),
                format_voltage(vout),
            ])
        lines.append(_auto("power_rail_table",
                           markdown_table(
                               ['Ref', 'Part', 'Topology', 'Input Rail', 'Output Rail', 'Vout'],
                               rows)))
    lines.append("")

    # Decoupling analysis
    decoupling = analysis.get('signal_analysis', {}).get('decoupling_analysis', [])
    if decoupling:
        rows = []
        for d in decoupling:
            refs = d.get('capacitors', [])
            cap_refs = ', '.join(c.get('ref', '') for c in refs) if isinstance(refs, list) else ''
            rows.append([
                d.get('ic_ref', '?'),
                d.get('rail', '?'),
                cap_refs,
                d.get('assessment', ''),
            ])
        lines.append("### Decoupling")
        lines.append("")
        lines.append(_auto("decoupling_table",
                           markdown_table(['IC', 'Rail', 'Capacitors', 'Assessment'], rows)))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Signal interfaces
# ======================================================================

def section_signal_interfaces(analysis: dict) -> str:
    """Generate signal interfaces section."""
    lines = ["## 4. Signal Interfaces"]
    lines.append("")

    bus_analysis = analysis.get('design_analysis', {}).get('bus_analysis', {})
    any_bus = False

    for bus_type in ('i2c', 'spi', 'uart', 'can'):
        buses = bus_analysis.get(bus_type, [])
        if not buses:
            continue
        any_bus = True
        lines.append(f"### {bus_type.upper()}")
        lines.append("")
        for i, bus in enumerate(buses):
            bus_id = bus.get('bus_id', f'{bus_type}_{i}')
            signals = bus.get('signals', [])
            sig_names = [s.get('name', str(s)) if isinstance(s, dict) else str(s)
                         for s in signals]
            lines.append(_auto(f"bus_{bus_type}_{i}",
                               f"**{bus_id}**: {', '.join(sig_names[:10])}"))
            lines.append("")

    if not any_bus:
        lines.append("*No formal buses detected.*")
        lines.append("")

    # Level shifters
    shifters = analysis.get('signal_analysis', {}).get('level_shifters', [])
    if shifters:
        lines.append("### Level Shifting")
        lines.append("")
        rows = [[s.get('ref', '?'), s.get('value', ''),
                 s.get('low_side_rail', '?'), s.get('high_side_rail', '?')]
                for s in shifters]
        lines.append(_auto("level_shifters",
                           markdown_table(['Ref', 'Part', 'Low Side', 'High Side'], rows)))
        lines.append("")

    lines.append(_narrative("signal_interfaces_notes",
                            "Describe interface design decisions: "
                            "pull-up values, termination, protection, signal integrity."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Analog design
# ======================================================================

def section_analog_design(analysis: dict, diagrams_dir: str) -> str:
    """Generate analog design section."""
    lines = ["## 5. Analog Design"]
    lines.append("")

    sa = analysis.get('signal_analysis', {})

    # Voltage dividers
    dividers = sa.get('voltage_dividers', [])
    if dividers:
        lines.append("### Voltage Dividers")
        lines.append("")
        rows = []
        for d in dividers:
            rows.append([
                d.get('r_top', {}).get('ref', '?'),
                d.get('r_bottom', {}).get('ref', '?'),
                f"{d.get('ratio', 0):.3f}",
                d.get('mid_net', '?'),
            ])
        lines.append(_auto("voltage_dividers",
                           markdown_table(['R_top', 'R_bottom', 'Ratio', 'Output Net'], rows)))
        lines.append("")

    # Filters
    for ftype, label in [('rc_filters', 'RC Filters'), ('lc_filters', 'LC Filters')]:
        filters = sa.get(ftype, [])
        if filters:
            lines.append(f"### {label}")
            lines.append("")
            rows = []
            for f in filters:
                fc = f.get('cutoff_hz') or f.get('fc_hz')
                rows.append([
                    f.get('type', '?'),
                    f.get('resistor', {}).get('ref', '?') if isinstance(f.get('resistor'), dict) else str(f.get('resistor', '?')),
                    f.get('capacitor', {}).get('ref', '?') if isinstance(f.get('capacitor'), dict) else str(f.get('capacitor', '?')),
                    format_frequency(fc),
                ])
            lines.append(_auto(f"{ftype}_table",
                               markdown_table(['Type', 'R', 'C', 'Cutoff'], rows)))
            lines.append("")

    # Crystal circuits
    crystals = sa.get('crystal_circuits', [])
    if crystals:
        lines.append("### Crystal / Oscillator")
        lines.append("")
        for c in crystals:
            freq = c.get('frequency_hz')
            lines.append(_auto(f"crystal_{c.get('ref', 'X')}",
                               f"**{c.get('ref', '?')}**: {format_frequency(freq)}"))
        lines.append("")

    # Op-amp circuits
    opamps = sa.get('opamp_circuits', [])
    if opamps:
        lines.append("### Op-Amp Circuits")
        lines.append("")
        rows = []
        for o in opamps:
            rows.append([
                o.get('ref', '?'),
                o.get('value', ''),
                o.get('topology', '?'),
                str(o.get('gain', '—')),
            ])
        lines.append(_auto("opamp_table",
                           markdown_table(['Ref', 'Part', 'Topology', 'Gain'], rows)))
        lines.append("")

    if not any([dividers, sa.get('rc_filters'), sa.get('lc_filters'),
                crystals, opamps]):
        lines.append("*No analog subcircuits detected.*")
        lines.append("")

    lines.append(_narrative("analog_design_notes",
                            "Describe analog design decisions: component selection rationale, "
                            "SPICE verification results, tolerance analysis."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Thermal analysis
# ======================================================================

def section_thermal(thermal_data: dict | None) -> str:
    """Generate thermal analysis section from analyze_thermal output."""
    lines = ["## 6. Thermal Analysis"]
    lines.append("")

    if not thermal_data:
        lines.append("*Thermal analysis not available. Run analyze_thermal.py.*")
        lines.append("")
        return "\n".join(lines)

    summary = thermal_data.get('summary', {})
    lines.append(_auto("thermal_summary",
                       f"**Thermal Score:** {summary.get('thermal_score', '—')}/100 | "
                       f"**Hottest Component:** {summary.get('hottest_component', '—')} | "
                       f"**Components >85°C:** {summary.get('components_above_85c', 0)}"))
    lines.append("")

    assessments = thermal_data.get('thermal_assessments', [])
    if assessments:
        rows = []
        for a in assessments:
            rows.append([
                a.get('ref', '?'),
                a.get('value', ''),
                a.get('package', ''),
                f"{a.get('pdiss_w', 0):.2f}W",
                f"{a.get('tj_estimated_c', 0):.0f}°C",
                f"{a.get('margin_c', 0):.0f}°C",
            ])
        lines.append(_auto("thermal_table",
                           markdown_table(
                               ['Ref', 'Part', 'Package', 'Pdiss', 'Tj Est', 'Margin'],
                               rows, ['left', 'left', 'left', 'right', 'right', 'right'])))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# EMC considerations
# ======================================================================

def section_emc(emc_data: dict | None) -> str:
    """Generate EMC section from analyze_emc output."""
    lines = ["## 7. EMC Considerations"]
    lines.append("")

    if not emc_data:
        lines.append("*EMC analysis not available. Run analyze_emc.py.*")
        lines.append("")
        return "\n".join(lines)

    summary = emc_data.get('summary', {})
    lines.append(_auto("emc_summary",
                       f"**EMC Risk Score:** {summary.get('emc_risk_score', '—')}/100 | "
                       f"**Critical:** {summary.get('critical', 0)} | "
                       f"**High:** {summary.get('high', 0)} | "
                       f"**Medium:** {summary.get('medium', 0)}"))
    lines.append("")

    findings = emc_data.get('findings', [])
    if findings:
        # Group by severity
        rows = []
        for f in sorted(findings, key=lambda x: {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2,
                                                    'LOW': 3, 'INFO': 4}.get(x.get('severity', 'INFO'), 5)):
            if f.get('suppressed'):
                continue
            rows.append([
                f.get('severity', '?'),
                f.get('rule_id', '?'),
                f.get('title', '')[:60],
                f.get('category', ''),
            ])
        if rows:
            lines.append(_auto("emc_findings",
                               markdown_table(['Severity', 'Rule', 'Finding', 'Category'],
                                              rows[:30])))  # limit to 30
            if len(rows) > 30:
                lines.append(f"*... and {len(rows) - 30} more findings.*")
    lines.append("")
    lines.append(_narrative("emc_notes",
                            "Describe EMC design strategy: shielding, filtering, "
                            "layout decisions for emissions compliance."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# PCB design
# ======================================================================

def section_pcb_design(pcb_data: dict | None) -> str:
    """Generate PCB design section from analyze_pcb output."""
    lines = ["## 8. PCB Design Details"]
    lines.append("")

    if not pcb_data:
        lines.append("*PCB analysis not available. Run analyze_pcb.py.*")
        lines.append("")
        return "\n".join(lines)

    stats = pcb_data.get('statistics', {})
    if stats:
        rows = [
            ['Copper layers', str(stats.get('copper_layers', '?'))],
            ['Footprints (front/back)', f"{stats.get('front_footprints', '?')}/{stats.get('back_footprints', '?')}"],
            ['Track segments', str(stats.get('track_segments', '?'))],
            ['Vias', str(stats.get('via_count', '?'))],
            ['Routing completion', f"{stats.get('routing_completion', '?')}%"],
        ]
        lines.append(_auto("pcb_stats",
                           markdown_table(['Metric', 'Value'], rows)))
    lines.append("")

    # Board outline
    outline = pcb_data.get('board_outline', {})
    if outline:
        w = outline.get('width_mm', '?')
        h = outline.get('height_mm', '?')
        lines.append(_auto("board_dimensions",
                           f"**Board Dimensions:** {w}mm × {h}mm"))
    lines.append("")

    lines.append(_narrative("pcb_design_notes",
                            "Describe PCB layout decisions: stackup, impedance control, "
                            "routing strategy, DFM considerations."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# BOM summary
# ======================================================================

def section_bom_summary(analysis: dict) -> str:
    """Generate BOM summary section."""
    lines = ["## 10. BOM Summary"]
    lines.append("")

    bom = analysis.get('bom', [])
    if not bom:
        lines.append("*No BOM data available.*")
        lines.append("")
        return "\n".join(lines)

    rows = []
    for item in bom:
        refs = item.get('references', [])
        ref_str = ', '.join(refs[:5])
        if len(refs) > 5:
            ref_str += f" +{len(refs) - 5}"
        rows.append([
            ref_str,
            item.get('value', ''),
            item.get('footprint', '').split(':')[-1] if item.get('footprint') else '',
            item.get('mpn', ''),
            str(item.get('quantity', len(refs))),
        ])

    lines.append(_auto("bom_table",
                       markdown_table(['References', 'Value', 'Footprint', 'MPN', 'Qty'],
                                      rows[:50],
                                      ['left', 'left', 'left', 'left', 'right'])))
    if len(rows) > 50:
        lines.append(f"*... and {len(rows) - 50} more line items.*")
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Test and debug
# ======================================================================

def section_test_debug(analysis: dict) -> str:
    """Generate test and debug section."""
    lines = ["## 11. Test and Debug"]
    lines.append("")

    # Debug interfaces
    debug = analysis.get('signal_analysis', {}).get('debug_interfaces', [])
    if debug:
        rows = [[d.get('ref', '?'), d.get('type', ''), d.get('protocol', '')]
                for d in debug]
        lines.append(_auto("debug_interfaces",
                           markdown_table(['Ref', 'Type', 'Protocol'], rows)))
        lines.append("")

    lines.append(_narrative("test_strategy",
                            "Describe the testing approach: test points, production test "
                            "procedures, debug access, programming interface."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Compliance
# ======================================================================

def section_compliance(analysis: dict, emc_data: dict | None,
                       config: dict) -> str:
    """Generate compliance and standards section."""
    lines = ["## 12. Compliance and Standards"]
    lines.append("")

    market = config.get('project', {}).get('market', '')
    if market:
        lines.append(_auto("target_market", f"**Target Market:** {market.upper()}"))
        lines.append("")

    # EMC test plan
    if emc_data:
        test_plan = emc_data.get('test_plan', {})
        if test_plan:
            lines.append("### EMC Test Plan")
            lines.append("")
            lines.append(_auto("emc_test_plan",
                               f"*See EMC analysis output for detailed test plan.*"))
            lines.append("")

    lines.append(_narrative("compliance_notes",
                            "List applicable standards (FCC, CE, UL), "
                            "certification strategy, and pre-compliance test results."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Appendices
# ======================================================================

def section_appendix_schematics(sch_cache_dir: str,
                                analysis: dict) -> str:
    """Generate appendix with full schematic sheet images."""
    lines = ["## Appendix A: Schematic Drawings"]
    lines.append("")

    # Reference any SVGs in the schematic cache directory
    import os
    if os.path.isdir(sch_cache_dir):
        svgs = sorted(f for f in os.listdir(sch_cache_dir) if f.endswith('.svg'))
        if svgs:
            for svg_file in svgs:
                name = svg_file.replace('.svg', '').replace('_', ' ')
                lines.append(f"### {name}")
                lines.append("")
                lines.append(f"![{name}]({sch_cache_dir}/{svg_file})")
                lines.append("")
        else:
            lines.append("*No schematic SVGs found. Run kidoc_render.py first.*")
    else:
        lines.append(f"*Schematic cache directory not found: {sch_cache_dir}*")
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# CE Technical File — specialized sections
# ======================================================================

def section_ce_product_identification(analysis: dict, config: dict) -> str:
    """CE Technical File: product identification."""
    lines = ["## Product Identification"]
    lines.append("")

    project = config.get('project', {})
    rows = [
        ['Product Name', project.get('name', '—')],
        ['Model / Part Number', project.get('number', '—')],
        ['Revision', project.get('revision', '—')],
        ['Manufacturer', project.get('company', '—')],
        ['Intended Use', ''],
    ]
    lines.append(_auto("ce_product_id", markdown_table(['Field', 'Value'], rows)))
    lines.append("")
    lines.append(_narrative("ce_intended_use",
                            "Describe the product's intended use, target environment "
                            "(indoor/outdoor, industrial/consumer), and user profile."))
    lines.append("")
    return "\n".join(lines)


def section_ce_essential_requirements(analysis: dict, config: dict) -> str:
    """CE Technical File: essential requirements mapping."""
    lines = ["## Essential Requirements"]
    lines.append("")
    lines.append("Mapping of EU directive essential requirements to design evidence.")
    lines.append("")

    # Determine applicable directives based on market
    rows = [
        ['LVD 2014/35/EU', 'Electrical safety', 'EN 62368-1', ''],
        ['EMC 2014/30/EU', 'Electromagnetic compatibility', 'EN 55032, EN 55035', ''],
        ['RoHS 2011/65/EU', 'Hazardous substance restriction', 'EN IEC 63000', ''],
    ]

    # Add radio if applicable
    sa = analysis.get('signal_analysis', {})
    if sa.get('rf_chains') or sa.get('rf_matching'):
        rows.append(['RED 2014/53/EU', 'Radio equipment', 'EN 300 328, EN 301 489', ''])

    lines.append(_auto("ce_essential_reqs",
                       markdown_table(
                           ['Directive', 'Requirement', 'Harmonized Standard', 'Evidence'],
                           rows)))
    lines.append("")
    lines.append(_narrative("ce_essential_req_notes",
                            "For each directive, describe how the design meets the "
                            "essential requirements. Reference test reports and analysis data."))
    lines.append("")
    return "\n".join(lines)


def section_ce_harmonized_standards(config: dict) -> str:
    """CE Technical File: harmonized standards list."""
    lines = ["## Harmonized Standards Applied"]
    lines.append("")

    # Get standards from config or use defaults for EU market
    reports = config.get('reports', {})
    standards = []
    for doc_def in reports.get('documents', []):
        if doc_def.get('type') == 'ce_technical_file':
            standards = doc_def.get('standards', [])
            break

    if not standards:
        standards = ['EN 55032', 'EN 55035', 'EN 62368-1', 'EN IEC 63000']

    rows = []
    standard_descriptions = {
        'EN 55032': ('EMC emissions', 'Limits for electromagnetic disturbances'),
        'EN 55035': ('EMC immunity', 'Immunity requirements for multimedia equipment'),
        'EN 62368-1': ('Safety', 'Audio/video, IT and communication technology equipment'),
        'EN IEC 63000': ('RoHS', 'Technical documentation for hazardous substance assessment'),
        'EN 300 328': ('Radio', 'Wideband data transmission (2.4 GHz)'),
        'EN 301 489': ('Radio EMC', 'EMC standard for radio equipment'),
        'EN 61000-4-2': ('ESD immunity', 'Electrostatic discharge immunity test'),
        'EN 61000-4-3': ('Radiated immunity', 'Radiated RF electromagnetic field immunity'),
    }
    for std in standards:
        category, desc = standard_descriptions.get(std, ('', std))
        rows.append([std, category, desc])

    lines.append(_auto("ce_standards",
                       markdown_table(['Standard', 'Category', 'Description'], rows)))
    lines.append("")
    return "\n".join(lines)


def section_ce_risk_assessment(analysis: dict, emc_data: dict | None,
                               thermal_data: dict | None) -> str:
    """CE Technical File: risk assessment."""
    lines = ["## Risk Assessment"]
    lines.append("")

    rows = [
        ['Electrical shock', 'Low voltage (<50V DC)', '', ''],
        ['Overheating / fire', '', '', ''],
        ['EMI emissions', '', '', ''],
        ['ESD susceptibility', '', '', ''],
        ['Mechanical hazard', '', '', ''],
    ]

    # Populate from analysis data
    if thermal_data:
        summary = thermal_data.get('summary', {})
        hottest = summary.get('hottest_component', '—')
        above_85 = summary.get('components_above_85c', 0)
        rows[1][1] = f"Hottest: {hottest}"
        rows[1][2] = 'HIGH' if above_85 > 0 else 'LOW'
        rows[1][3] = f"{above_85} components above 85°C" if above_85 else 'Within limits'

    if emc_data:
        summary = emc_data.get('summary', {})
        score = summary.get('emc_risk_score', '—')
        critical = summary.get('critical', 0)
        rows[2][1] = f"EMC risk score: {score}/100"
        rows[2][2] = 'HIGH' if critical > 0 else ('MEDIUM' if score and score > 50 else 'LOW')
        rows[2][3] = f"{critical} critical findings" if critical else 'Pre-compliance assessment'

    # ESD from analysis
    esd_audit = analysis.get('signal_analysis', {}).get('esd_coverage_audit', [])
    unprotected = 0
    for e in esd_audit:
        if isinstance(e, dict):
            try:
                cov = float(e.get('coverage', 1.0))
                if cov < 1.0:
                    unprotected += 1
            except (TypeError, ValueError):
                pass
    if unprotected:
        rows[3][1] = f"{unprotected} connectors with gaps"
        rows[3][2] = 'MEDIUM'
        rows[3][3] = 'Partial ESD protection coverage'
    else:
        rows[3][1] = 'All connectors protected'
        rows[3][2] = 'LOW'

    lines.append(_auto("ce_risk_assessment",
                       markdown_table(['Hazard', 'Details', 'Risk Level', 'Mitigation'], rows)))
    lines.append("")
    lines.append(_narrative("ce_risk_notes",
                            "Describe risk mitigation measures for each identified hazard. "
                            "Reference specific design features and test results."))
    lines.append("")
    return "\n".join(lines)


def section_ce_declaration_of_conformity(config: dict) -> str:
    """CE Technical File: Declaration of Conformity template."""
    lines = ["## EU Declaration of Conformity"]
    lines.append("")

    project = config.get('project', {})
    lines.append(_auto("ce_doc_template", "\n".join([
        "**EU DECLARATION OF CONFORMITY**",
        "",
        f"**Manufacturer:** {project.get('company', '________________')}",
        f"**Product:** {project.get('name', '________________')}",
        f"**Model:** {project.get('number', '________________')}",
        "",
        "We declare under our sole responsibility that the product described above "
        "is in conformity with the relevant Union harmonisation legislation:",
        "",
        "- Directive 2014/35/EU (Low Voltage Directive)",
        "- Directive 2014/30/EU (EMC Directive)",
        "- Directive 2011/65/EU (RoHS Directive)",
        "",
        "Harmonized standards applied: *(see Harmonized Standards section)*",
        "",
        "Signed: ________________  Date: ________________",
        "",
        "Name: ________________  Position: ________________",
    ])))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Design Review — specialized sections
# ======================================================================

def section_review_summary(analysis: dict, emc_data: dict | None,
                           thermal_data: dict | None,
                           gate_data: dict | None) -> str:
    """Design Review: summary of findings across all analyzers."""
    lines = ["## Review Summary"]
    lines.append("")

    rows = []

    # Fab gate
    if gate_data:
        status = gate_data.get('overall_status', '?')
        summary = gate_data.get('summary', {})
        rows.append(['Fab Release Gate', status,
                     f"{summary.get('pass', 0)} pass, {summary.get('fail', 0)} fail"])

    # EMC
    if emc_data:
        summary = emc_data.get('summary', {})
        score = summary.get('emc_risk_score', '—')
        rows.append(['EMC Risk Score', f"{score}/100",
                     f"C:{summary.get('critical', 0)} H:{summary.get('high', 0)} "
                     f"M:{summary.get('medium', 0)}"])

    # Thermal
    if thermal_data:
        summary = thermal_data.get('summary', {})
        score = summary.get('thermal_score', '—')
        rows.append(['Thermal Score', f"{score}/100",
                     f"{summary.get('components_above_85c', 0)} above 85°C"])

    # BOM completeness
    stats = analysis.get('statistics', {})
    missing_mpn = stats.get('missing_mpns', 0)
    total = stats.get('total_components', 0)
    if total:
        rows.append(['BOM Completeness',
                     f"{total - missing_mpn}/{total} MPNs",
                     f"{missing_mpn} missing" if missing_mpn else 'Complete'])

    if rows:
        lines.append(_auto("review_summary_table",
                           markdown_table(['Check', 'Status', 'Details'], rows)))
    lines.append("")
    lines.append(_narrative("review_overall_assessment",
                            "Provide an overall assessment of design readiness. "
                            "Highlight critical risks and recommend go/no-go."))
    lines.append("")
    return "\n".join(lines)


def section_review_action_items(config: dict) -> str:
    """Design Review: action items table."""
    lines = ["## Action Items"]
    lines.append("")
    lines.append(_auto("review_actions",
                       markdown_table(
                           ['#', 'Finding', 'Severity', 'Owner', 'Due Date', 'Status'],
                           [['1', '', '', '', '', 'OPEN']],
                           ['right', 'left', 'left', 'left', 'left', 'left'])))
    lines.append("")
    lines.append(_narrative("review_action_notes",
                            "List action items from the review. Assign owners and due dates. "
                            "Track resolution status."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# ICD — specialized sections
# ======================================================================

def section_icd_interface_list(analysis: dict) -> str:
    """ICD: summary table of all external interfaces."""
    lines = ["## Interface List"]
    lines.append("")

    rows = []
    # Connectors from ESD audit (they enumerate all external connectors)
    esd = analysis.get('signal_analysis', {}).get('esd_coverage_audit', [])
    for e in esd:
        if isinstance(e, dict):
            rows.append([
                e.get('connector_ref', '?'),
                e.get('connector_value', ''),
                e.get('interface_type', ''),
                str(len(e.get('signal_nets', []))),
                e.get('risk_level', ''),
            ])

    if rows:
        lines.append(_auto("icd_interface_list",
                           markdown_table(
                               ['Connector', 'Type', 'Interface', 'Signals', 'ESD Risk'],
                               rows)))
    else:
        lines.append("*No external interfaces detected. Add connector analysis data.*")
    lines.append("")
    return "\n".join(lines)


def section_icd_connector_details(analysis: dict, config: dict) -> str:
    """ICD: per-connector pinout and signal details."""
    lines = ["## Connector Details"]
    lines.append("")

    # Get specific connectors from config if specified
    target_connectors = []
    for doc_def in config.get('reports', {}).get('documents', []):
        if doc_def.get('type') == 'icd':
            target_connectors = doc_def.get('connectors', [])

    esd = analysis.get('signal_analysis', {}).get('esd_coverage_audit', [])
    ic_pins = analysis.get('ic_pin_analysis', {})

    for e in esd:
        if not isinstance(e, dict):
            continue
        ref = e.get('connector_ref', '')
        if target_connectors and ref not in target_connectors:
            continue

        lines.append(f"### {ref} — {e.get('connector_value', '')}")
        lines.append("")
        lines.append(f"**Interface:** {e.get('interface_type', 'General')}")
        lines.append("")

        # Signal list
        signals = e.get('signal_nets', [])
        protected = set(e.get('protected_nets', []))
        if signals:
            rows = []
            for sig in signals:
                prot = 'Yes' if sig in protected else 'No'
                rows.append([sig, '', '', prot])
            lines.append(_auto(f"icd_connector_{ref}",
                               markdown_table(
                                   ['Signal', 'Direction', 'Voltage Level', 'ESD Protected'],
                                   rows)))
        lines.append("")
        lines.append(_narrative(f"icd_connector_{ref}_notes",
                                f"Describe the {ref} interface: protocol, signal levels, "
                                f"timing requirements, mating connector specification."))
        lines.append("")

    if not esd:
        lines.append("*No connector data available. Run schematic analysis with ESD audit.*")
        lines.append("")

    return "\n".join(lines)


def section_icd_electrical_characteristics(analysis: dict) -> str:
    """ICD: electrical characteristics summary."""
    lines = ["## Electrical Characteristics"]
    lines.append("")

    # Power domains give voltage levels
    domains = analysis.get('design_analysis', {}).get('power_domains', {})
    domain_groups = domains.get('domain_groups', {})

    if domain_groups:
        rows = []
        for domain_name in sorted(domain_groups.keys()) if isinstance(domain_groups, dict) else []:
            rows.append([domain_name, '', '', ''])
        if rows:
            lines.append(_auto("icd_voltage_levels",
                               markdown_table(
                                   ['Voltage Domain', 'Nominal', 'Min', 'Max'],
                                   rows, ['left', 'right', 'right', 'right'])))
    lines.append("")
    lines.append(_narrative("icd_electrical_notes",
                            "Specify voltage levels, impedance, current limits, "
                            "and timing requirements for each interface."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Manufacturing — specialized sections
# ======================================================================

def section_mfg_assembly_overview(analysis: dict) -> str:
    """Manufacturing: assembly overview."""
    lines = ["## Assembly Overview"]
    lines.append("")

    stats = analysis.get('statistics', {})
    rows = [
        ['Total components', str(stats.get('total_components', '?'))],
        ['SMD components', str(stats.get('smd_count', '?'))],
        ['THT components', str(stats.get('tht_count', '?'))],
        ['DNP components', str(stats.get('dnp_count', 0))],
        ['Unique parts', str(stats.get('unique_parts', '?'))],
    ]
    lines.append(_auto("mfg_overview",
                       markdown_table(['Metric', 'Count'], rows)))
    lines.append("")
    lines.append(_narrative("mfg_overview_notes",
                            "Describe assembly requirements: lead-free/leaded, "
                            "reflow profile, hand-solder requirements, special handling."))
    lines.append("")
    return "\n".join(lines)


def section_mfg_pcb_fab_notes(pcb_data: dict | None) -> str:
    """Manufacturing: PCB fabrication notes."""
    lines = ["## PCB Fabrication Notes"]
    lines.append("")

    if not pcb_data:
        lines.append("*PCB analysis not available.*")
        lines.append("")
        return "\n".join(lines)

    stats = pcb_data.get('statistics', {})
    outline = pcb_data.get('board_outline', {})

    rows = [
        ['Board dimensions', f"{outline.get('width_mm', '?')}mm × {outline.get('height_mm', '?')}mm"],
        ['Copper layers', str(stats.get('copper_layers', '?'))],
        ['Board thickness', '1.6mm'],
        ['Copper weight', '1 oz'],
        ['Surface finish', 'HASL / ENIG'],
        ['Solder mask', 'Green'],
        ['Silkscreen', 'White'],
        ['Min trace/space', ''],
        ['Min drill', ''],
        ['IPC class', 'Class 2'],
    ]
    lines.append(_auto("mfg_fab_notes",
                       markdown_table(['Parameter', 'Value'], rows)))
    lines.append("")
    lines.append(_narrative("mfg_fab_notes_detail",
                            "Specify impedance control requirements, stackup details, "
                            "material (FR-4/Rogers), and any special fabrication instructions."))
    lines.append("")
    return "\n".join(lines)


def section_mfg_assembly_instructions(analysis: dict) -> str:
    """Manufacturing: assembly instructions."""
    lines = ["## Assembly Instructions"]
    lines.append("")
    lines.append(_narrative("mfg_assembly_instructions",
                            "Describe the assembly sequence: paste application, component "
                            "placement, reflow profile, hand-solder steps, conformal coating, "
                            "cleaning requirements, and special handling for sensitive components."))
    lines.append("")
    return "\n".join(lines)


def section_mfg_test_procedures(analysis: dict) -> str:
    """Manufacturing: production test procedures."""
    lines = ["## Production Test Procedures"]
    lines.append("")

    lines.append(_auto("mfg_test_checklist", "\n".join([
        "1. Visual inspection (IPC-A-610 Class 2)",
        "2. Power-on test: verify all voltage rails",
        "3. Functional test: verify communication interfaces",
        "4. Programming: flash firmware",
        "5. Final inspection and labeling",
    ])))
    lines.append("")
    lines.append(_narrative("mfg_test_details",
                            "Describe pass/fail criteria for each test step. "
                            "Include expected voltages, test fixture requirements, "
                            "and failure modes to watch for."))
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# Mechanical / Environmental
# ======================================================================

def section_mechanical_environmental(analysis: dict, pcb_data: dict | None) -> str:
    """Mechanical and environmental specifications."""
    lines = ["## 9. Mechanical / Environmental"]
    lines.append("")

    if pcb_data:
        outline = pcb_data.get('board_outline', {})
        if outline:
            lines.append(_auto("mech_dimensions",
                               f"**Board Dimensions:** "
                               f"{outline.get('width_mm', '?')}mm × "
                               f"{outline.get('height_mm', '?')}mm"))
            lines.append("")

    lines.append(_narrative("mechanical_notes",
                            "Describe: mounting method, enclosure constraints, "
                            "connector accessibility, operating temperature range, "
                            "humidity, vibration requirements."))
    lines.append("")
    return "\n".join(lines)
