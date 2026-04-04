#!/usr/bin/env python3
"""Markdown scaffold generator for engineering documentation.

Reads analysis JSONs and .kicad-happy.json config to produce a structured
markdown document with AUTO-START/AUTO-END markers for regeneratable content
and NARRATIVE placeholders for Claude/user prose.

Usage:
    python3 kidoc_scaffold.py --project-dir . --type hdd --output reports/HDD.md
    python3 kidoc_scaffold.py --project-dir . --type design_review --output reports/DR.md
    python3 kidoc_scaffold.py --project-dir . --config .kicad-happy.json --output reports/

Zero external dependencies — Python 3.8+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_kicad_scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '..', '..', 'kicad', 'scripts')
if os.path.isdir(_kicad_scripts):
    sys.path.insert(0, os.path.abspath(_kicad_scripts))

from kidoc_sections import (
    section_front_matter, section_executive_summary,
    section_system_overview, section_power_design,
    section_signal_interfaces, section_analog_design, section_thermal,
    section_emc, section_pcb_design, section_bom_summary,
    section_test_debug, section_compliance, section_appendix_schematics,
    section_mechanical_environmental,
    # CE Technical File
    section_ce_product_identification, section_ce_essential_requirements,
    section_ce_harmonized_standards, section_ce_risk_assessment,
    section_ce_declaration_of_conformity,
    # Design Review
    section_review_summary, section_review_action_items,
    # ICD
    section_icd_interface_list, section_icd_connector_details,
    section_icd_electrical_characteristics,
    # Manufacturing
    section_mfg_assembly_overview, section_mfg_pcb_fab_notes,
    section_mfg_assembly_instructions, section_mfg_test_procedures,
)
from kidoc_templates import get_section_list, get_document_title

# Try to import project_config for cascading config loading
try:
    from project_config import load_config, load_config_from_path
except ImportError:
    def load_config(search_dir):
        return {'version': 1, 'project': {}, 'suppressions': []}
    def load_config_from_path(path):
        return {'version': 1, 'project': {}, 'suppressions': []}


# ======================================================================
# Analysis cache loading
# ======================================================================

def load_analysis_cache(project_dir: str,
                        cache_dir: str | None = None) -> dict:
    """Load all analysis JSONs from the cache directory.

    Searches in order:
    1. cache_dir if specified
    2. reports/cache/analysis/ under project_dir
    3. project_dir itself (for analysis JSONs placed alongside schematics)

    Returns dict with keys: schematic, pcb, emc, thermal, spice, gate.
    """
    search_dirs = []
    if cache_dir:
        search_dirs.append(cache_dir)
    search_dirs.append(os.path.join(project_dir, 'reports', 'cache', 'analysis'))
    search_dirs.append(project_dir)

    cache = {}
    file_patterns = {
        'schematic': ['*schematic*.json', '*_sch*.json', 'schematic.json'],
        'pcb': ['*pcb*.json', '*_pcb*.json', 'pcb.json'],
        'emc': ['*emc*.json', 'emc.json'],
        'thermal': ['*thermal*.json', 'thermal.json'],
        'spice': ['*spice*.json', '*simulation*.json', 'spice.json'],
        'gate': ['*gate*.json', '*fab_release*.json', 'gate.json'],
    }

    for analysis_type, patterns in file_patterns.items():
        if analysis_type in cache:
            continue
        for search_dir in search_dirs:
            if not os.path.isdir(search_dir):
                continue
            for fname in os.listdir(search_dir):
                if not fname.endswith('.json'):
                    continue
                fname_lower = fname.lower()
                for pattern in patterns:
                    # Simple glob matching
                    pat = pattern.replace('*', '')
                    if pat in fname_lower:
                        fpath = os.path.join(search_dir, fname)
                        try:
                            with open(fpath) as f:
                                data = json.load(f)
                            # Verify it's the right type
                            if analysis_type == 'schematic' and 'components' in data:
                                cache['schematic'] = data
                            elif analysis_type == 'pcb' and 'footprints' in data:
                                cache['pcb'] = data
                            elif analysis_type == 'emc' and 'findings' in data and 'emc_risk_score' in str(data.get('summary', {})):
                                cache['emc'] = data
                            elif analysis_type == 'thermal' and 'thermal_assessments' in data:
                                cache['thermal'] = data
                            elif analysis_type == 'spice' and 'simulation_results' in data:
                                cache['spice'] = data
                            elif analysis_type == 'gate' and 'overall_status' in data:
                                cache['gate'] = data
                            if analysis_type in cache:
                                break
                        except (json.JSONDecodeError, OSError):
                            pass
                if analysis_type in cache:
                    break

    return cache




# ======================================================================
# Template variable resolution
# ======================================================================

def resolve_template_vars(text: str, config: dict) -> str:
    """Replace {project}, {rev}, etc. placeholders."""
    project = config.get('project', {})
    replacements = {
        '{project}': project.get('name', ''),
        '{rev}': project.get('revision', ''),
        '{company}': project.get('company', ''),
        '{number}': project.get('number', ''),
        '{classification}': config.get('reports', {}).get('classification', ''),
        '{author}': project.get('author', ''),
    }
    for key, val in replacements.items():
        text = text.replace(key, val)
    return text


# ======================================================================
# Scaffold generation
# ======================================================================

def scaffold_document(project_dir: str, doc_type: str, output_path: str,
                      config: dict,
                      analysis_cache: dict | None = None) -> str:
    """Generate a markdown scaffold for the specified document type.

    Returns the markdown content (also writes to output_path).
    """
    if analysis_cache is None:
        analysis_cache = load_analysis_cache(project_dir)

    analysis = analysis_cache.get('schematic', {})
    pcb_data = analysis_cache.get('pcb')
    emc_data = analysis_cache.get('emc')
    thermal_data = analysis_cache.get('thermal')

    # Determine paths for diagrams and schematic SVGs
    reports_dir = os.path.join(project_dir, 'reports')
    diagrams_dir = os.path.join(reports_dir, 'cache', 'diagrams')
    sch_cache_dir = os.path.join(reports_dir, 'cache', 'schematic')

    # Use relative paths from the output file's directory
    output_dir = os.path.dirname(os.path.abspath(output_path))
    try:
        diagrams_rel = os.path.relpath(diagrams_dir, output_dir)
        sch_cache_rel = os.path.relpath(sch_cache_dir, output_dir)
    except ValueError:
        diagrams_rel = diagrams_dir
        sch_cache_rel = sch_cache_dir

    # Get sections for this document type (with config overrides)
    sections = get_section_list(doc_type, config)

    gate_data = analysis_cache.get('gate')

    # Build markdown
    parts = []

    section_map = {
        # Core sections (HDD)
        'front_matter': lambda: section_front_matter(config, doc_type),
        'executive_summary': lambda: section_executive_summary(analysis, emc_data, thermal_data, pcb_data),
        'system_overview': lambda: section_system_overview(analysis, diagrams_rel),
        'power_design': lambda: section_power_design(analysis, diagrams_rel),
        'signal_interfaces': lambda: section_signal_interfaces(analysis),
        'analog_design': lambda: section_analog_design(analysis, diagrams_rel),
        'thermal_analysis': lambda: section_thermal(thermal_data),
        'emc_analysis': lambda: section_emc(emc_data),
        'pcb_design': lambda: section_pcb_design(pcb_data),
        'mechanical_environmental': lambda: section_mechanical_environmental(analysis, pcb_data),
        'bom_summary': lambda: section_bom_summary(analysis),
        'test_debug': lambda: section_test_debug(analysis),
        'compliance': lambda: section_compliance(analysis, emc_data, config),
        'appendix_schematics': lambda: section_appendix_schematics(sch_cache_rel, analysis),
        # CE Technical File
        'ce_product_identification': lambda: section_ce_product_identification(analysis, config),
        'ce_essential_requirements': lambda: section_ce_essential_requirements(analysis, config),
        'ce_harmonized_standards': lambda: section_ce_harmonized_standards(config),
        'ce_risk_assessment': lambda: section_ce_risk_assessment(analysis, emc_data, thermal_data),
        'ce_declaration_of_conformity': lambda: section_ce_declaration_of_conformity(config),
        # Design Review
        'review_summary': lambda: section_review_summary(analysis, emc_data, thermal_data, gate_data),
        'review_action_items': lambda: section_review_action_items(config),
        # ICD
        'icd_interface_list': lambda: section_icd_interface_list(analysis),
        'icd_connector_details': lambda: section_icd_connector_details(analysis, config),
        'icd_electrical_characteristics': lambda: section_icd_electrical_characteristics(analysis),
        # Manufacturing
        'mfg_assembly_overview': lambda: section_mfg_assembly_overview(analysis),
        'mfg_pcb_fab_notes': lambda: section_mfg_pcb_fab_notes(pcb_data),
        'mfg_assembly_instructions': lambda: section_mfg_assembly_instructions(analysis),
        'mfg_test_procedures': lambda: section_mfg_test_procedures(analysis),
    }

    for section_name in sections:
        generator = section_map.get(section_name)
        if generator:
            parts.append(generator())

    markdown = "\n".join(parts)

    # Resolve template variables
    markdown = resolve_template_vars(markdown, config)

    # Write output (overwrites — use git to track/merge user edits)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(markdown)

    return markdown


# ======================================================================
# Main
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Generate markdown scaffold for engineering documentation')
    parser.add_argument('--project-dir', '-p', default='.',
                        help='Path to KiCad project directory')
    parser.add_argument('--type', '-t', default='hdd',
                        choices=['hdd', 'ce_technical_file', 'design_review',
                                 'icd', 'manufacturing'],
                        help='Document type (default: hdd)')
    parser.add_argument('--output', '-o', required=True,
                        help='Output markdown file path')
    parser.add_argument('--config', default=None,
                        help='Path to .kicad-happy.json config')
    parser.add_argument('--analysis-dir', default=None,
                        help='Directory containing analysis JSONs')
    args = parser.parse_args()

    # Load config
    if args.config:
        config = load_config_from_path(args.config)
    else:
        config = load_config(args.project_dir)

    # Load analysis cache
    cache = load_analysis_cache(args.project_dir, args.analysis_dir)

    if not cache:
        print("Warning: no analysis JSONs found. Scaffold will have placeholder content.",
              file=sys.stderr)

    # Generate scaffold
    scaffold_document(
        project_dir=args.project_dir,
        doc_type=args.type,
        output_path=args.output,
        config=config,
        analysis_cache=cache,
    )

    print(args.output, file=sys.stderr)


if __name__ == '__main__':
    main()
