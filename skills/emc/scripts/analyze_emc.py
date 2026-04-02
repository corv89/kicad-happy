#!/usr/bin/env python3
"""EMC pre-compliance risk analyzer for KiCad designs.

Consumes schematic and/or PCB analyzer JSON output and produces a structured
EMC risk report. Operates entirely on geometric rule checks and analytical
formulas — no full-wave simulation required.

Usage:
    python3 analyze_emc.py --schematic schematic.json --pcb pcb.json
    python3 analyze_emc.py --pcb pcb.json --output emc.json
    python3 analyze_emc.py --schematic schematic.json --pcb pcb.json --severity high
    python3 analyze_emc.py --schematic schematic.json --pcb pcb.json --standard cispr-class-b

Zero external dependencies beyond Python 3.8+ stdlib.
"""

import argparse
import json
import os
import sys
import time

# Add this script's directory to path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emc_rules import run_all_checks, generate_test_plan, analyze_regulatory_coverage
from emc_formulas import STANDARDS, MARKET_STANDARDS

# Shared severity weights — used by both risk score and per-net scoring
SEVERITY_WEIGHTS = {'CRITICAL': 15, 'HIGH': 8, 'MEDIUM': 3, 'LOW': 1, 'INFO': 0}


def compute_risk_score(findings: list) -> int:
    """Compute overall EMC risk score from 0 (worst) to 100 (best).

    score = 100 - (critical × 15) - (high × 8) - (medium × 3) - (low × 1)
    """
    counts = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0, 'INFO': 0}
    for f in findings:
        sev = f.get('severity', 'INFO')
        counts[sev] = counts.get(sev, 0) + 1

    score = 100 - sum(SEVERITY_WEIGHTS[sev] * cnt for sev, cnt in counts.items())
    return max(0, min(100, score))


def compute_per_net_scores(findings: list) -> list:
    """Group findings by net name and compute per-net EMC risk scores.

    Returns a list of {net, score, finding_count, rules} sorted worst-first.
    """
    net_findings = {}
    for f in findings:
        for net in f.get('nets', []):
            if net:
                net_findings.setdefault(net, []).append(f)

    scores = []
    for net, net_f in net_findings.items():
        penalty = 0
        for f in net_f:
            w = SEVERITY_WEIGHTS.get(f['severity'], 0)
            # SPICE-verified findings are more trustworthy — weight 1.5×
            if f.get('spice_verified'):
                w = int(w * 1.5)
            penalty += w
        score = max(0, min(100, 100 - penalty))
        rules = sorted(set(f['rule_id'] for f in net_f))
        scores.append({
            'net': net,
            'score': score,
            'finding_count': len(net_f),
            'rules': rules,
        })
    scores.sort(key=lambda s: s['score'])
    return scores


def extract_board_info(schematic: dict = None, pcb: dict = None) -> dict:
    """Extract board-level info for the report."""
    info = {}

    if pcb:
        stats = pcb.get('statistics', {})
        info['board_width_mm'] = stats.get('board_width_mm')
        info['board_height_mm'] = stats.get('board_height_mm')
        info['layer_count'] = stats.get('copper_layers_used', 0)
        info['footprint_count'] = stats.get('footprint_count', 0)
        info['via_count'] = stats.get('via_count', 0)

        setup = pcb.get('setup', {})
        stackup = setup.get('stackup', [])
        if stackup:
            info['has_stackup'] = True
            info['board_thickness_mm'] = setup.get('board_thickness_mm')
        else:
            info['has_stackup'] = False

    if schematic:
        stats = schematic.get('statistics', {})
        info['total_components'] = stats.get('total_components', 0)
        info['total_nets'] = stats.get('total_nets', 0)

        # Extract highest frequencies
        freqs = []
        for xtal in schematic.get('signal_analysis', {}).get('crystal_circuits', []):
            f = xtal.get('frequency') or 0
            if isinstance(f, (int, float)) and f > 0:
                freqs.append(f)
        if freqs:
            info['highest_frequency_hz'] = max(freqs)
            info['crystal_frequencies_hz'] = sorted(set(freqs))

        # Switching frequencies
        sw_freqs = []
        for reg in schematic.get('signal_analysis', {}).get('power_regulators', []):
            if reg.get('topology') not in ('ldo', 'linear'):
                # Infer from part number via emc_rules helper
                from emc_rules import _estimate_switching_freq
                f = _estimate_switching_freq(reg.get('value', ''))
                if f:
                    sw_freqs.append(f)
        if sw_freqs:
            info['switching_frequencies_hz'] = sorted(set(sw_freqs))

    return info


def format_text_report(result: dict) -> str:
    """Format findings as human-readable text."""
    lines = []
    summary = result.get('summary', {})
    findings = result.get('findings', [])

    lines.append('=' * 60)
    lines.append('EMC PRE-COMPLIANCE RISK ANALYSIS')
    lines.append('=' * 60)
    lines.append('')

    std = result.get('target_standard', 'fcc-class-b')
    lines.append(f'Target standard: {std}')
    score = summary.get('emc_risk_score', 0)
    lines.append(f'EMC risk score:  {score}/100')
    lines.append('')

    lines.append(f'Total checks:  {summary.get("total_checks", 0)}')
    lines.append(f'  CRITICAL:    {summary.get("critical", 0)}')
    lines.append(f'  HIGH:        {summary.get("high", 0)}')
    lines.append(f'  MEDIUM:      {summary.get("medium", 0)}')
    lines.append(f'  LOW:         {summary.get("low", 0)}')
    lines.append(f'  INFO:        {summary.get("info", 0)}')
    lines.append('')

    if not findings:
        lines.append('No EMC findings.')
        return '\n'.join(lines)

    # Group by category
    categories = {}
    for f in findings:
        cat = f.get('category', 'other')
        categories.setdefault(cat, []).append(f)

    cat_labels = {
        'ground_plane': 'Ground Plane Integrity',
        'decoupling': 'Decoupling Effectiveness',
        'io_filtering': 'I/O Interface Filtering',
        'switching_emc': 'Switching Regulator EMC',
        'clock_routing': 'Clock Routing Quality',
        'via_stitching': 'Via Stitching',
        'stackup': 'Stackup Quality',
        'diff_pair': 'Differential Pair EMC',
        'board_edge': 'Board Edge Analysis',
        'crosstalk': 'Crosstalk / Signal Integrity',
        'emi_filter': 'EMI Filter Verification',
        'esd_path': 'ESD Protection Path',
        'thermal_emc': 'Thermal-EMC Interaction',
        'shielding': 'Shielding / Enclosure',
        'pdn': 'PDN Impedance',
        'return_path': 'Return Path Analysis',
        'emission_estimate': 'Emission Estimates',
    }

    for cat, cat_findings in categories.items():
        lines.append('-' * 60)
        lines.append(cat_labels.get(cat, cat.replace('_', ' ').title()))
        lines.append('-' * 60)

        for f in cat_findings:
            sev = f['severity']
            lines.append(f'  [{sev}] {f["rule_id"]}: {f["title"]}')
            # Wrap description
            desc = f.get('description', '')
            for i in range(0, len(desc), 70):
                prefix = '    ' if i == 0 else '      '
                lines.append(prefix + desc[i:i+70])
            if f.get('components'):
                lines.append(f'    Components: {", ".join(f["components"])}')
            if f.get('nets'):
                lines.append(f'    Nets: {", ".join(f["nets"])}')
            if f.get('recommendation'):
                lines.append(f'    → {f["recommendation"]}')
            lines.append('')

    # Per-net scores (top 5 worst)
    per_net = result.get('per_net_scores', [])
    if per_net:
        worst = [n for n in per_net if n['score'] < 100][:5]
        if worst:
            lines.append('-' * 60)
            lines.append('Highest-Risk Nets')
            lines.append('-' * 60)
            for n in worst:
                lines.append(f'  {n["net"]}: score {n["score"]}/100 '
                             f'({n["finding_count"]} findings: {", ".join(n["rules"])})')
            lines.append('')

    # Test plan section
    tp = result.get('test_plan', {})
    if tp.get('frequency_bands'):
        lines.append('=' * 60)
        lines.append('PRE-COMPLIANCE TEST PLAN')
        lines.append('=' * 60)
        lines.append('')
        lines.append('Frequency band priority:')
        for band in tp['frequency_bands']:
            if band['source_count'] > 0:
                lines.append(f'  [{band["risk_level"].upper()}] {band["band"]}: '
                             f'{band["source_count"]} emission source(s)')
                for src in band['sources'][:3]:
                    lines.append(f'    - {src}')
        lines.append('')

    if tp.get('interface_risks'):
        lines.append('Interface risk ranking:')
        for iface in tp['interface_risks'][:5]:
            lines.append(f'  {iface["connector"]} ({iface["protocol"]}): '
                         f'risk {iface["risk_score"]}/10 — '
                         f'{", ".join(iface["reasons"])}')
        lines.append('')

    if tp.get('probe_points'):
        lines.append('Suggested near-field probe points:')
        for pt in tp['probe_points'][:10]:
            lines.append(f'  {pt["ref"]} at ({pt["x"]}, {pt["y"]})mm — {pt["reason"]}')
        lines.append('')

    # Regulatory coverage section
    reg = result.get('regulatory_coverage', {})
    if reg.get('coverage_matrix'):
        lines.append('-' * 60)
        lines.append(f'Regulatory Coverage (market: {reg.get("market", "?")})')
        lines.append('-' * 60)
        for entry in reg['coverage_matrix']:
            lines.append(f'  {entry["standard"]} ({entry["test_type"]}): '
                         f'{entry["coverage"]}')
            if entry.get('note'):
                lines.append(f'    {entry["note"]}')
        lines.append('')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='EMC pre-compliance risk analyzer for KiCad designs')
    parser.add_argument('--schematic', '-s', help='Schematic analyzer JSON')
    parser.add_argument('--pcb', '-p', help='PCB analyzer JSON')
    parser.add_argument('--output', '-o', help='Output JSON file path')
    parser.add_argument('--severity', default='all',
                        choices=['all', 'low', 'medium', 'high', 'critical'],
                        help='Minimum severity to report (default: all)')
    parser.add_argument('--standard', default='fcc-class-b',
                        choices=list(STANDARDS.keys()),
                        help='Target EMC standard (default: fcc-class-b)')
    parser.add_argument('--text', action='store_true',
                        help='Print human-readable text report to stdout')
    parser.add_argument('--compact', action='store_true',
                        help='Omit INFO-level findings from output')
    parser.add_argument('--market', default=None,
                        choices=list(MARKET_STANDARDS.keys()),
                        help='Target market — sets applicable standards (us, eu, automotive, medical, military)')
    parser.add_argument('--spice-enhanced', action='store_true',
                        help='Use SPICE simulation for improved PDN/filter analysis (requires ngspice/LTspice/Xyce)')

    args = parser.parse_args()

    if not args.schematic and not args.pcb:
        parser.error('At least one of --schematic or --pcb is required')

    # Load inputs
    schematic = None
    pcb = None

    if args.schematic:
        with open(args.schematic, 'r') as f:
            schematic = json.load(f)

    if args.pcb:
        with open(args.pcb, 'r') as f:
            pcb = json.load(f)

    # Effective severity threshold
    severity = 'low' if args.compact else args.severity

    # SPICE-enhanced mode (optional)
    spice_backend = None
    if args.spice_enhanced:
        try:
            from emc_spice import detect_spice_simulator
            spice_backend = detect_spice_simulator()
            if spice_backend:
                print(f'SPICE-enhanced mode: {spice_backend.name}', file=sys.stderr)
            else:
                print('Warning: --spice-enhanced requested but no simulator found',
                      file=sys.stderr)
        except ImportError:
            print('Warning: SPICE skill not available for enhanced analysis',
                  file=sys.stderr)

    # Run analysis
    t0 = time.time()
    findings = run_all_checks(schematic, pcb,
                              standard=args.standard,
                              severity_threshold=severity,
                              spice_backend=spice_backend)
    elapsed = time.time() - t0

    # Build summary
    counts = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0, 'INFO': 0}
    for f in findings:
        sev = f.get('severity', 'INFO')
        counts[sev] = counts.get(sev, 0) + 1

    risk_score = compute_risk_score(findings)

    # Generate test plan, per-net scores, and regulatory coverage
    test_plan = generate_test_plan(schematic, pcb, findings,
                                   standard=args.standard)
    per_net = compute_per_net_scores(findings)
    regulatory = analyze_regulatory_coverage(args.standard, args.market,
                                            findings)

    result = {
        'summary': {
            'total_checks': len(findings),
            'critical': counts['CRITICAL'],
            'high': counts['HIGH'],
            'medium': counts['MEDIUM'],
            'low': counts['LOW'],
            'info': counts['INFO'],
            'emc_risk_score': risk_score,
        },
        'target_standard': args.standard,
        'findings': findings,
        'per_net_scores': per_net,
        'test_plan': test_plan,
        'regulatory_coverage': regulatory,
        'board_info': extract_board_info(schematic, pcb),
        'elapsed_s': round(elapsed, 3),
    }

    # Output
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'EMC analysis complete: {len(findings)} findings '
              f'(score {risk_score}/100) → {args.output}', file=sys.stderr)
    elif args.text:
        print(format_text_report(result))
    else:
        json.dump(result, sys.stdout, indent=2)
        print(file=sys.stdout)

    # Exit code: non-zero if critical findings
    if counts['CRITICAL'] > 0:
        sys.exit(1)
    return 0


if __name__ == '__main__':
    main()
