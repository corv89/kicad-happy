#!/usr/bin/env python3
"""Render orchestrator for kidoc document generation.

Coordinates all figure generation for a report based on the document
spec: kicad-cli overview renders, custom renderer subsystem crops,
block diagrams, connector pinouts, PCB layer views.

Usage:
    python3 kidoc_orchestrator.py --spec spec.json --project-dir . --output reports/figures/
    python3 kidoc_orchestrator.py --analysis schematic.json --project-dir . --output reports/figures/

Zero external dependencies -- Python 3.8+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Ensure this script's directory is on sys.path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kidoc_spec import load_spec, expand_type_to_spec, SECTION_DEFAULTS
from kicad_cli import find_kicad_cli, export_sch_svg, export_pcb_svg
from kidoc_render import render_schematic
from pcb_render import render_pcb
from figures import generate_all, generate_pinouts, FigureTheme


# ======================================================================
# Constants
# ======================================================================

# Section types that get overview (full-sheet) renders
_OVERVIEW_SECTION_TYPES = frozenset({
    'system_overview',
    'appendix_schematics',
})

# Section types that trigger PCB renders
_PCB_SECTION_TYPES = frozenset({
    'pcb_design',
})

# Section types that trigger pinout generation
_PINOUT_SECTION_TYPES = frozenset({
    'icd_connector_details',
    'icd_interface_list',
})

# PCB presets to generate for pcb_design sections
_PCB_PRESETS = ['assembly-front', 'routing-front']


# ======================================================================
# File auto-detection
# ======================================================================

def _find_file(project_dir: str, suffix: str) -> Optional[str]:
    """Find the first file with *suffix* under *project_dir*."""
    for f in Path(project_dir).rglob(f'*{suffix}'):
        return str(f)
    return None


# ======================================================================
# Pin nets builder
# ======================================================================

def _build_pin_nets(analysis: dict) -> Dict[str, Dict[str, str]]:
    """Build pin_nets dict from analysis['nets'].

    Returns ``{component_ref: {pin_number: net_name}}``.
    """
    pin_nets: Dict[str, Dict[str, str]] = {}
    for net_name, net_info in analysis.get('nets', {}).items():
        if net_name.startswith('__unnamed_'):
            continue
        for p in net_info.get('pins', []):
            comp = p.get('component', '')
            pin = p.get('pin_number', '')
            if comp and pin:
                pin_nets.setdefault(comp, {})[pin] = net_name
    return pin_nets


# ======================================================================
# Render mode resolution
# ======================================================================

def _resolve_render_mode(section: dict, kicad_cli_cmd: Optional[str]) -> str:
    """Resolve a section's render mode to a concrete action.

    Returns one of: 'kicad-cli', 'crop', 'skip'.
    """
    mode = section.get('render', 'auto')
    section_type = section.get('type', '')
    has_focus = bool(section.get('focus_refs'))

    if mode == 'annotated':
        # Phase 9 placeholder -- falls back to crop
        mode = 'crop'

    if mode == 'kicad-cli':
        if kicad_cli_cmd:
            return 'kicad-cli'
        # Fall back to custom renderer
        return 'crop'

    if mode == 'crop':
        return 'crop'

    # mode == 'auto'
    if section_type in _OVERVIEW_SECTION_TYPES:
        if kicad_cli_cmd:
            return 'kicad-cli'
        return 'crop'

    if has_focus:
        return 'crop'

    # Default for non-overview, non-focused sections: skip render
    return 'skip'


# ======================================================================
# Individual render steps
# ======================================================================

def _render_kicad_cli(kicad_cli_cmd: str, sch_path: str,
                      output_dir: str) -> List[str]:
    """Run kicad-cli schematic export. Returns list of generated paths."""
    os.makedirs(output_dir, exist_ok=True)
    ok = export_sch_svg(kicad_cli_cmd, sch_path, output_dir)
    if not ok:
        return []
    # Collect all SVGs produced
    return sorted(str(p) for p in Path(output_dir).glob('*.svg'))


def _render_custom_overview(sch_path: str, output_dir: str,
                            pin_nets: Optional[Dict] = None) -> List[str]:
    """Full-sheet custom render (no cropping). Returns list of SVG paths."""
    os.makedirs(output_dir, exist_ok=True)
    try:
        return render_schematic(sch_path, output_dir, pin_nets=pin_nets)
    except Exception as exc:
        print(f"  Warning: custom overview render failed: {exc}",
              file=sys.stderr)
        return []


def _render_crop(sch_path: str, section: dict, output_dir: str,
                 pin_nets: Optional[Dict] = None) -> List[str]:
    """Subsystem crop render. Returns list of SVG paths."""
    focus_refs = section.get('focus_refs', [])
    highlight_nets = section.get('highlight_nets', [])
    section_id = section.get('id', 'crop')

    if not focus_refs:
        # Nothing to crop around -- skip
        return []

    os.makedirs(output_dir, exist_ok=True)
    try:
        paths = render_schematic(
            sch_path, output_dir,
            crop_refs=focus_refs,
            focus_refs=focus_refs,
            highlight_nets=highlight_nets if highlight_nets else None,
            pin_nets=pin_nets,
        )
        # Rename to section_id-based name for clarity
        renamed = []
        for p in paths:
            target = os.path.join(output_dir, f'{section_id}.svg')
            if p != target:
                try:
                    os.replace(p, target)
                    renamed.append(target)
                except OSError:
                    renamed.append(p)
            else:
                renamed.append(p)
        return renamed
    except Exception as exc:
        print(f"  Warning: crop render for {section_id!r} failed: {exc}",
              file=sys.stderr)
        return []


def _render_pcb_views(pcb_path: str, output_dir: str,
                      highlight_nets: Optional[List[str]] = None
                      ) -> List[str]:
    """Generate PCB preset renders. Returns list of SVG paths."""
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for preset in _PCB_PRESETS:
        try:
            out = render_pcb(pcb_path, output_dir, preset_name=preset,
                             highlight_nets=highlight_nets)
            paths.append(out)
        except Exception as exc:
            print(f"  Warning: PCB render preset={preset!r} failed: {exc}",
                  file=sys.stderr)
    return paths


def _generate_diagrams(analysis: dict, output_dir: str,
                       theme: Optional[FigureTheme] = None) -> List[str]:
    """Generate all block diagrams. Returns list of SVG paths."""
    os.makedirs(output_dir, exist_ok=True)
    try:
        return generate_all(analysis, output_dir, theme=theme)
    except Exception as exc:
        print(f"  Warning: diagram generation failed: {exc}",
              file=sys.stderr)
        return []


def _generate_pinout_figures(analysis: dict,
                             output_dir: str,
                             theme: Optional[FigureTheme] = None) -> List[str]:
    """Generate connector pinout SVGs. Returns list of SVG paths."""
    os.makedirs(output_dir, exist_ok=True)
    try:
        return generate_pinouts(analysis, output_dir, theme=theme)
    except Exception as exc:
        print(f"  Warning: pinout generation failed: {exc}",
              file=sys.stderr)
        return []


# ======================================================================
# Main orchestrator
# ======================================================================

def orchestrate_renders(spec: dict, project_dir: str,
                        analysis: dict,
                        figures_dir: str,
                        sch_path: Optional[str] = None,
                        pcb_path: Optional[str] = None,
                        config: Optional[dict] = None
                        ) -> Dict[str, List[str]]:
    """Generate all figures for a report based on the document spec.

    Args:
        spec: document spec dict (from kidoc_spec.py)
        project_dir: KiCad project directory
        analysis: loaded schematic analysis JSON
        figures_dir: base output directory (e.g., reports/figures/)
        sch_path: path to .kicad_sch (auto-detected if None)
        pcb_path: path to .kicad_pcb (auto-detected if None)

    Returns:
        dict mapping section_id -> list of generated figure paths
    """
    result: Dict[str, List[str]] = {}

    # Build figure theme from branding config
    theme = FigureTheme.from_config(config) if config else FigureTheme()

    # Auto-detect project files
    if not sch_path:
        sch_path = _find_file(project_dir, '.kicad_sch')
    if not pcb_path:
        pcb_path = _find_file(project_dir, '.kicad_pcb')

    # Detect kicad-cli availability
    kicad_cli_cmd = find_kicad_cli()
    if kicad_cli_cmd:
        print(f"  kicad-cli found: {kicad_cli_cmd}", file=sys.stderr)
    else:
        print("  kicad-cli not found, using custom renderer",
              file=sys.stderr)

    # Build pin_nets for annotation
    pin_nets = _build_pin_nets(analysis) if analysis else None

    # Output subdirectories
    schematics_dir = os.path.join(figures_dir, 'schematics')
    crops_dir = os.path.join(figures_dir, 'crops')
    diagrams_dir = os.path.join(figures_dir, 'diagrams')
    pinouts_dir = os.path.join(figures_dir, 'pinouts')
    pcb_dir = os.path.join(figures_dir, 'pcb')

    sections = spec.get('sections', [])

    # Track which global steps we've done to avoid duplicates
    overview_done = False
    pcb_done = False
    pinouts_done = False

    # ---- Per-section rendering ----
    for section in sections:
        section_id = section.get('id', '')
        section_type = section.get('type', '')
        mode = _resolve_render_mode(section, kicad_cli_cmd)

        if mode == 'kicad-cli' and sch_path and not overview_done:
            print(f"  [{section_id}] kicad-cli overview render",
                  file=sys.stderr)
            paths = _render_kicad_cli(kicad_cli_cmd, sch_path,
                                      schematics_dir)
            if paths:
                result[section_id] = paths
                overview_done = True
            else:
                # Fallback to custom
                print(f"  [{section_id}] kicad-cli failed, "
                      f"falling back to custom renderer", file=sys.stderr)
                paths = _render_custom_overview(sch_path, schematics_dir,
                                                pin_nets=pin_nets)
                if paths:
                    result[section_id] = paths
                    overview_done = True

        elif mode == 'kicad-cli' and sch_path and overview_done:
            # Re-use already generated overview renders
            existing = sorted(str(p)
                              for p in Path(schematics_dir).glob('*.svg'))
            if existing:
                result[section_id] = existing

        elif mode == 'crop' and sch_path:
            focus_refs = section.get('focus_refs', [])
            if focus_refs:
                print(f"  [{section_id}] crop render: "
                      f"{', '.join(focus_refs)}", file=sys.stderr)
                paths = _render_crop(sch_path, section, crops_dir,
                                     pin_nets=pin_nets)
                if paths:
                    result[section_id] = paths
            elif section_type in _OVERVIEW_SECTION_TYPES and not overview_done:
                # Auto mode resolved to crop for overview (no kicad-cli)
                print(f"  [{section_id}] custom overview render",
                      file=sys.stderr)
                paths = _render_custom_overview(sch_path, schematics_dir,
                                                pin_nets=pin_nets)
                if paths:
                    result[section_id] = paths
                    overview_done = True

        # PCB renders
        if section_type in _PCB_SECTION_TYPES and pcb_path and not pcb_done:
            print(f"  [{section_id}] PCB renders", file=sys.stderr)
            paths = _render_pcb_views(pcb_path, pcb_dir)
            if paths:
                result.setdefault(section_id, []).extend(paths)
                pcb_done = True

        # Pinout figures
        if (section_type in _PINOUT_SECTION_TYPES and analysis
                and not pinouts_done):
            print(f"  [{section_id}] pinout figures", file=sys.stderr)
            paths = _generate_pinout_figures(analysis, pinouts_dir,
                                                     theme=theme)
            if paths:
                result.setdefault(section_id, []).extend(paths)
                pinouts_done = True

    # ---- Always generate diagrams from analysis data ----
    if analysis:
        print("  [diagrams] generating block diagrams", file=sys.stderr)
        diagram_paths = _generate_diagrams(analysis, diagrams_dir,
                                                  theme=theme)
        if diagram_paths:
            result['_diagrams'] = diagram_paths

    return result


# ======================================================================
# CLI
# ======================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Render orchestrator for kidoc document generation')
    parser.add_argument('--spec', '-s', default=None,
                        help='Path to document spec JSON '
                             '(default: auto-generate from analysis)')
    parser.add_argument('--analysis', '-a', default=None,
                        help='Path to schematic analysis JSON')
    parser.add_argument('--project-dir', '-p', required=True,
                        help='KiCad project directory')
    parser.add_argument('--output', '-o', required=True,
                        help='Output directory for figures')
    parser.add_argument('--sch', default=None,
                        help='Path to .kicad_sch (auto-detected if omitted)')
    parser.add_argument('--pcb', default=None,
                        help='Path to .kicad_pcb (auto-detected if omitted)')
    parser.add_argument('--config', default=None,
                        help='Path to .kicad-happy.json config '
                             '(for branding/theme)')
    args = parser.parse_args()

    # Load or generate spec
    if args.spec:
        spec = load_spec(args.spec)
    else:
        # Default: HDD spec covers all section types
        spec = expand_type_to_spec('hdd')

    # Load analysis
    analysis = {}
    if args.analysis:
        with open(args.analysis) as f:
            analysis = json.load(f)

    figures_dir = os.path.abspath(args.output)
    project_dir = os.path.abspath(args.project_dir)

    print(f"Orchestrating renders into {figures_dir}", file=sys.stderr)
    # Load config if provided
    config = None
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    result = orchestrate_renders(
        spec, project_dir, analysis, figures_dir,
        sch_path=args.sch, pcb_path=args.pcb, config=config,
    )

    # Report
    total = sum(len(v) for v in result.values())
    print(f"\nGenerated {total} figure(s) across "
          f"{len(result)} section(s):", file=sys.stderr)
    for section_id, paths in sorted(result.items()):
        print(f"  {section_id}:", file=sys.stderr)
        for p in paths:
            print(f"    {p}", file=sys.stderr)

    # Output JSON manifest to stdout for downstream consumption
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
