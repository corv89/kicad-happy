#!/usr/bin/env python3
"""Cross-domain analysis — checks requiring both schematic and PCB data.

Consumes schematic and PCB analyzer JSON outputs. Produces rich findings
for checks that span the schematic-PCB boundary: connector current capacity,
ESD coverage gaps, decoupling adequacy, and schematic/PCB cross-validation.

Usage:
    python3 cross_analysis.py --schematic sch.json --pcb pcb.json [--output cross.json]
    python3 cross_analysis.py --schematic sch.json  # PCB-less mode (limited checks)
    python3 cross_analysis.py --schema               # Print output schema
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from finding_schema import make_finding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_ground_net(name: str) -> bool:
    if not name:
        return False
    n = name.upper().replace('/', '').replace('-', '').replace('_', '')
    return n in ('GND', 'VSS', 'DGND', 'AGND', 'PGND', 'GNDD', 'GNDA',
                 'GND_D', 'GND_A', 'EARTH', 'CHASSIS', '0V')


def _is_power_net(name: str) -> bool:
    if not name:
        return False
    n = name.upper()
    if _is_ground_net(name):
        return True
    if n.startswith(('+', 'VCC', 'VDD', 'VBUS', 'VIN', 'VBAT', 'VSYS')):
        return True
    if re.match(r'^\+?\d+V\d*', n):
        return True
    return False


def _parse_voltage_from_name(name: str) -> float | None:
    if not name:
        return None
    m = re.search(r'(\d+)V(\d+)', name.upper())
    if m:
        return float(m.group(1)) + float(m.group(2)) / (10 ** len(m.group(2)))
    m = re.search(r'(\d+\.?\d*)V', name.upper())
    if m:
        return float(m.group(1))
    return None


def _build_net_id_map(pcb: dict) -> dict[int, str]:
    result = {}
    for ni in pcb.get('nets', {}).get('net_info', []):
        result[ni.get('id', -1)] = ni.get('name', '')
    return result


# ---------------------------------------------------------------------------
# CC-001: Connector current capacity
# ---------------------------------------------------------------------------

_IPC2152_1OZ_10C = {
    0.5: 0.25, 1.0: 0.50, 2.0: 1.10, 3.0: 1.80,
    5.0: 3.50, 7.0: 5.50, 10.0: 9.0,
}


def _min_trace_width_for_current(current_a: float) -> float:
    prev_i, prev_w = 0.0, 0.0
    for i, w in sorted(_IPC2152_1OZ_10C.items()):
        if current_a <= i:
            if prev_i == 0:
                return w
            frac = (current_a - prev_i) / (i - prev_i)
            return prev_w + frac * (w - prev_w)
        prev_i, prev_w = i, w
    return prev_w


def check_connector_current(schematic: dict, pcb: dict | None) -> list[dict]:
    """CC-001: Check connector pin current capacity vs trace width."""
    findings: list[dict] = []
    if not pcb:
        return findings

    footprints = pcb.get('footprints', [])
    fp_map = {fp.get('reference', ''): fp for fp in footprints}

    segments = pcb.get('tracks', {}).get('segments', [])
    net_id_map = _build_net_id_map(pcb)

    net_min_width: dict[str, float] = {}
    for seg in segments:
        net_id = seg.get('net', 0)
        net_name = net_id_map.get(net_id, '') if isinstance(net_id, int) else str(net_id)
        w = seg.get('width', 0) or 0
        if net_name and w > 0:
            if net_name not in net_min_width or w < net_min_width[net_name]:
                net_min_width[net_name] = w

    components = schematic.get('components', [])
    connectors = [c for c in components if c.get('type') == 'connector']
    regulators = schematic.get('signal_analysis', {}).get('power_regulators', [])

    for conn in connectors:
        ref = conn['reference']
        fp = fp_map.get(ref)
        if not fp:
            continue
        for pad in fp.get('pads', []):
            net_name = pad.get('net_name', '')
            if not net_name or _is_ground_net(net_name) or not _is_power_net(net_name):
                continue
            voltage = _parse_voltage_from_name(net_name)
            if voltage is None:
                continue
            total_current = sum(
                reg.get('estimated_iout_A', 0) or 0
                for reg in regulators
                if reg.get('input_rail') == net_name
            )
            if total_current <= 0:
                continue
            trace_w = net_min_width.get(net_name)
            if trace_w is None:
                continue
            min_w = _min_trace_width_for_current(total_current)
            if trace_w < min_w * 0.8:
                findings.append(make_finding(
                    detector='check_connector_current', rule_id='CC-001',
                    category='current_capacity',
                    summary=f'Connector {ref}: trace on {net_name} too narrow for ~{total_current:.1f}A',
                    description=(
                        f'Power net {net_name} at connector {ref} carries estimated '
                        f'{total_current:.1f}A but narrowest trace is {trace_w:.2f}mm. '
                        f'IPC-2152 recommends >= {min_w:.2f}mm (1oz Cu, 10C rise).'
                    ),
                    severity='warning', confidence='heuristic', evidence_source='topology',
                    components=[ref], nets=[net_name],
                    recommendation=f'Widen trace on {net_name} to >= {min_w:.1f}mm or use copper pour.',
                    standard_ref='IPC-2152', impact='Trace overheating and voltage drop',
                ))
    return findings


# ---------------------------------------------------------------------------
# EG-001: ESD coverage gap analysis
# ---------------------------------------------------------------------------

_EXTERNAL_CONNECTOR_KEYWORDS = (
    'usb', 'rj45', 'rj11', 'ethernet', 'hdmi', 'displayport',
    'barrel', 'dc_jack', 'bnc', 'sma', 'din', 'dsub', 'db9', 'db25',
    'screw_terminal', 'phoenix', 'molex',
)


def check_esd_coverage_gaps(schematic: dict, pcb: dict | None) -> list[dict]:
    """EG-001: Check for external connector pins missing ESD protection."""
    findings: list[dict] = []
    sa = schematic.get('signal_analysis', {})
    protection = sa.get('protection_devices', [])

    protected_nets: set[str] = set()
    for pd in protection:
        pnet = pd.get('protected_net', '')
        if pnet:
            protected_nets.add(pnet)
        for pn in pd.get('protected_nets', []):
            protected_nets.add(pn)

    components = schematic.get('components', [])
    connectors = [c for c in components if c.get('type') == 'connector']

    # Build pin_net lookup from schematic components
    pin_net_data = schematic.get('pin_net', {})

    for conn in connectors:
        val_lib = (conn.get('value', '') + ' ' + conn.get('lib_id', '')).lower()
        if not any(k in val_lib for k in _EXTERNAL_CONNECTOR_KEYWORDS):
            continue
        ref = conn['reference']

        unprotected_nets = []
        # Check via pin_net data (keys are "ref:pin_number" strings or tuples)
        if isinstance(pin_net_data, dict):
            for key, val in pin_net_data.items():
                key_str = str(key)
                if not key_str.startswith(ref + ':') and not key_str.startswith(f"('{ref}'"):
                    continue
                net = val[0] if isinstance(val, (list, tuple)) else val
                if not net or _is_power_net(net) or _is_ground_net(net):
                    continue
                if net not in protected_nets:
                    unprotected_nets.append(net)

        # Deduplicate
        unprotected_nets = list(dict.fromkeys(unprotected_nets))

        if unprotected_nets:
            findings.append(make_finding(
                detector='check_esd_coverage_gaps', rule_id='EG-001',
                category='esd_protection',
                summary=f'Connector {ref}: {len(unprotected_nets)} unprotected signal pin(s)',
                description=(
                    f'External connector {ref} ({conn.get("value", "")}) has '
                    f'{len(unprotected_nets)} unprotected signal net(s): '
                    f'{", ".join(unprotected_nets[:5])}{"..." if len(unprotected_nets) > 5 else ""}.'
                ),
                severity='warning', confidence='heuristic', evidence_source='topology',
                components=[ref], nets=unprotected_nets[:10],
                recommendation='Add TVS or ESD clamp diodes on unprotected external nets.',
                fix_params={
                    'type': 'add_protection',
                    'components': [{'type': 'tvs_diode', 'nets': unprotected_nets[:5]}],
                    'basis': 'IEC 61000-4-2 requires ESD protection on accessible pins',
                },
                standard_ref='IEC 61000-4-2', impact='ESD damage on unprotected pins',
            ))
    return findings


# ---------------------------------------------------------------------------
# DA-001: Decoupling strategy adequacy
# ---------------------------------------------------------------------------

def check_decoupling_adequacy(schematic: dict, pcb: dict | None) -> list[dict]:
    """DA-001: Per-IC decoupling assessment — count, value, and placement."""
    findings: list[dict] = []
    sa = schematic.get('signal_analysis', {})
    decoupling = sa.get('decoupling_analysis', {})
    if not decoupling:
        return findings

    rails = decoupling.get('per_rail', decoupling.get('rails', []))
    if isinstance(rails, dict):
        rails = list(rails.values())

    for rail in rails:
        rail_name = rail.get('rail', rail.get('name', ''))
        caps = rail.get('capacitors', [])
        ics = rail.get('ics', rail.get('ic_count', 0))
        ic_count = ics if isinstance(ics, int) else len(ics) if isinstance(ics, list) else 0
        if ic_count == 0:
            continue
        cap_count = len(caps)
        if cap_count < ic_count:
            findings.append(make_finding(
                detector='check_decoupling_adequacy', rule_id='DA-001',
                category='power_integrity',
                summary=f'Rail {rail_name}: {cap_count} caps for {ic_count} ICs',
                description=(
                    f'Power rail {rail_name} has {cap_count} decoupling cap(s) for '
                    f'{ic_count} IC(s). Best practice: at least one 100nF per IC.'
                ),
                severity='warning' if cap_count == 0 else 'info',
                confidence='heuristic', evidence_source='topology',
                nets=[rail_name],
                recommendation=f'Add {ic_count - cap_count} more 100nF caps on {rail_name}.',
                fix_params={
                    'type': 'add_component',
                    'components': [{'type': 'capacitor', 'value': '100n',
                                    'net_from': rail_name, 'net_to': 'GND'}] * min(ic_count - cap_count, 5),
                    'basis': 'One 100nF per IC power pin pair minimum',
                },
                impact='Increased power supply noise and EMI',
            ))
    return findings


# ---------------------------------------------------------------------------
# XV-001..003: Schematic/PCB cross-validation
# ---------------------------------------------------------------------------

def check_cross_validation(schematic: dict, pcb: dict | None) -> list[dict]:
    """XV-001..003: Cross-validate schematic and PCB data consistency."""
    findings: list[dict] = []
    if not pcb:
        return findings

    sch_refs = {c.get('reference', '') for c in schematic.get('components', [])
                if c.get('reference', '') and not c['reference'].startswith('#')}
    pcb_refs = {fp.get('reference', '') for fp in pcb.get('footprints', [])
                if fp.get('reference', '') and not fp['reference'].startswith('#')}

    in_sch_not_pcb = sch_refs - pcb_refs
    in_pcb_not_sch = pcb_refs - sch_refs

    # XV-001: Components in schematic but not PCB
    real_missing = {r for r in in_sch_not_pcb if not r.startswith(('TP', 'MH', 'NT', 'FID'))}
    if real_missing:
        findings.append(make_finding(
            detector='check_cross_validation', rule_id='XV-001', category='design_sync',
            summary=f'{len(real_missing)} component(s) in schematic but not PCB',
            description=f'Missing from PCB: {", ".join(sorted(real_missing)[:20])}{"..." if len(real_missing) > 20 else ""}.',
            severity='warning', confidence='deterministic', evidence_source='topology',
            components=sorted(real_missing)[:20],
            recommendation='Update PCB from schematic (Tools > Update PCB from Schematic).',
            impact='Missing components on manufactured board',
        ))

    # XV-001: Components in PCB but not schematic
    real_extra = {r for r in in_pcb_not_sch if not r.startswith(('TP', 'MH', 'NT', 'FID', 'H', 'G'))}
    if real_extra:
        findings.append(make_finding(
            detector='check_cross_validation', rule_id='XV-001', category='design_sync',
            summary=f'{len(real_extra)} component(s) in PCB but not schematic',
            description=f'Extra in PCB: {", ".join(sorted(real_extra)[:20])}{"..." if len(real_extra) > 20 else ""}.',
            severity='info', confidence='deterministic', evidence_source='topology',
            components=sorted(real_extra)[:20],
            recommendation='Verify these are intentional (mounting holes, test points, fiducials).',
        ))

    # XV-002: Value consistency
    pcb_fp_map = {fp.get('reference', ''): fp for fp in pcb.get('footprints', [])}
    sch_comp_map = {c.get('reference', ''): c for c in schematic.get('components', [])}
    for ref in sch_refs & pcb_refs:
        sch_val = sch_comp_map.get(ref, {}).get('value', '')
        pcb_val = pcb_fp_map.get(ref, {}).get('value', '')
        if sch_val and pcb_val and sch_val != pcb_val:
            if sch_val.replace(' ', '') == pcb_val.replace(' ', ''):
                continue
            findings.append(make_finding(
                detector='check_cross_validation', rule_id='XV-002', category='design_sync',
                summary=f'{ref}: value mismatch — "{sch_val}" vs "{pcb_val}"',
                description=f'{ref} has "{sch_val}" in schematic but "{pcb_val}" in PCB.',
                severity='warning', confidence='deterministic', evidence_source='topology',
                components=[ref],
                recommendation='Sync PCB with schematic to resolve value differences.',
                impact='Wrong component may be placed during assembly',
            ))

    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all_checks(schematic: dict, pcb: dict | None) -> list[dict]:
    findings: list[dict] = []
    findings.extend(check_connector_current(schematic, pcb))
    findings.extend(check_esd_coverage_gaps(schematic, pcb))
    findings.extend(check_decoupling_adequacy(schematic, pcb))
    findings.extend(check_cross_validation(schematic, pcb))
    return findings


def main():
    parser = argparse.ArgumentParser(
        description='Cross-domain analysis — schematic + PCB combined checks')
    parser.add_argument('--schematic', '-s', default=None, help='Schematic analyzer JSON')
    parser.add_argument('--pcb', '-p', default=None, help='PCB analyzer JSON (optional)')
    parser.add_argument('--output', '-o', default=None, help='Output JSON file path')
    parser.add_argument('--schema', action='store_true', help='Print output schema and exit')
    parser.add_argument('--analysis-dir', default=None, help='Write into analysis cache directory')

    args = parser.parse_args()

    if args.schema:
        schema = {
            'analyzer_type': 'cross_analysis',
            'analysis_time_s': 'float',
            'summary': {'total_findings': 'int', 'by_severity': '{error, warning, info}'},
            'findings': '[{detector, rule_id, category, severity, confidence, summary, description, components, nets, recommendation, fix_params, report_context}]',
        }
        print(json.dumps(schema, indent=2))
        sys.exit(0)

    if not args.schematic:
        parser.error('--schematic is required')

    t0 = time.time()

    with open(args.schematic, 'r') as f:
        schematic = json.load(f)

    pcb = None
    if args.pcb:
        with open(args.pcb, 'r') as f:
            pcb = json.load(f)

    findings = run_all_checks(schematic, pcb)
    elapsed = time.time() - t0

    sev_counts = {'error': 0, 'warning': 0, 'info': 0}
    for f_item in findings:
        sev = f_item.get('severity', 'info')
        sev_counts[sev] = sev_counts.get(sev, 0) + 1

    result = {
        'analyzer_type': 'cross_analysis',
        'analysis_time_s': round(elapsed, 3),
        'summary': {'total_findings': len(findings), 'by_severity': sev_counts},
        'findings': findings,
    }

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'Cross-analysis: {len(findings)} findings -> {args.output}', file=sys.stderr)
    elif args.analysis_dir:
        out_path = os.path.join(args.analysis_dir, 'cross_analysis.json')
        with open(out_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'Cross-analysis: {len(findings)} findings -> {out_path}', file=sys.stderr)
    else:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == '__main__':
    sys.exit(main())
