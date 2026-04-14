#!/usr/bin/env python3
"""Migrate harness assertion paths from signal_analysis wrapper to flat findings format.

Rewrites assertion check.path fields in FND-*, BUGFIX-*, and other curated
assertion files to match the harmonized output format where signal_analysis
is removed and all findings are in a top-level findings[] list.

Usage:
    python3 migrate_harness_assertions.py <harness_dir> [--dry-run]

The script:
1. Finds all assertion JSON files (FND-*, BUGFIX-* only — SEED/STRUCT are re-seeded)
2. Rewrites signal_analysis.X paths to findings-compatible paths
3. Rewrites PCB section paths (dfm.violations, tombstoning_risk, etc.)
4. Preserves all other assertion data unchanged
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Path rewriting rules
# ---------------------------------------------------------------------------

# Schematic: signal_analysis.<section> → findings (filter by detector or section name)
# The flat findings list can't be indexed by old section name directly.
# Strategy: rewrite signal_analysis.X[N].field → findings.X[N].field
# where X is kept as a hint for the validator to filter by detector name.
# The harness validator would need to understand that findings.X means
# "filter findings by detector/legacy key X, then index".

# Simpler approach: just strip "signal_analysis." prefix since the section
# arrays no longer exist inside a wrapper.

_SCHEMATIC_REWRITES = [
    # signal_analysis.X → X (section promoted to top level as part of findings)
    # But the sections no longer exist as separate keys — they're in findings[].
    # Rewrite to: findings[?detector=detect_X] or just findings with a note.
    #
    # Actually, the harness assertions use paths like:
    #   signal_analysis.power_regulators  (count check)
    #   signal_analysis.voltage_dividers[0].ratio  (value check)
    #
    # For count checks: signal_analysis.power_regulators → findings (with filter)
    # For value checks: signal_analysis.voltage_dividers[0].ratio → findings[0].ratio (with filter)
    #
    # The cleanest migration: rewrite path and add a "detector_filter" field
    # to the check dict so the harness knows to pre-filter findings[].
]

# PCB: nested section paths that moved into findings[]
_PCB_SECTION_REMOVALS = [
    'dfm.violations',
    'dfm.ipc_class_compliance.violations',
    'placement_analysis.courtyard_overlaps',
    'placement_analysis.edge_clearance_warnings',
    'thermal_analysis.zone_stitching',
    'thermal_analysis.thermal_pads',
    'current_capacity.power_ground_nets',
    'current_capacity.narrow_signal_nets',
    'connectivity.unrouted',
    'copper_presence.same_layer_foreign_zones',
    'copper_presence.no_opposite_layer_copper_findings',
    'copper_presence.touch_pad_gnd_clearance',
    'tombstoning_risk',
    'thermal_pad_vias',
    'orientation_consistency',
    'silkscreen_pad_overlaps',
    'via_in_pad_issues',
    'board_edge_via_clearance',
    'keepout_violations',
    'fiducial_check.findings',
]

# Thermal: thermal_assessments → findings
# Lifecycle: lifecycle_findings → findings, temperature_findings → findings

# Legacy section name → detector name mapping (for adding detector_filter)
_SECTION_TO_DETECTOR = {
    'voltage_dividers': 'detect_voltage_dividers',
    'rc_filters': 'detect_rc_filters',
    'lc_filters': 'detect_lc_filters',
    'crystal_circuits': 'detect_crystal_circuits',
    'opamp_circuits': 'detect_opamp_circuits',
    'transistor_circuits': 'detect_transistor_circuits',
    'bridge_circuits': 'detect_bridge_circuits',
    'power_regulators': 'detect_power_regulators',
    'integrated_ldos': 'detect_integrated_ldos',
    'decoupling_analysis': 'detect_decoupling',
    'current_sense': 'detect_current_sense',
    'protection_devices': 'detect_protection_devices',
    'design_observations': 'detect_design_observations',
    'feedback_networks': 'detect_voltage_dividers',
    'ethernet_interfaces': 'detect_ethernet_interfaces',
    'hdmi_dvi_interfaces': 'detect_hdmi_dvi_interfaces',
    'lvds_interfaces': 'detect_lvds_interfaces',
    'memory_interfaces': 'detect_memory_interfaces',
    'rf_chains': 'detect_rf_chains',
    'rf_matching': 'detect_rf_matching',
    'bms_systems': 'detect_bms_systems',
    'battery_chargers': 'detect_battery_chargers',
    'motor_drivers': 'detect_motor_drivers',
    'addressable_led_chains': 'detect_addressable_leds',
    'debug_interfaces': 'detect_debug_interfaces',
    'power_path': 'detect_power_path',
    'adc_circuits': 'detect_adc_circuits',
    'reset_supervisors': 'detect_reset_supervisors',
    'clock_distribution': 'detect_clock_distribution',
    'display_interfaces': 'detect_display_interfaces',
    'sensor_interfaces': 'detect_sensor_interfaces',
    'level_shifters': 'detect_level_shifters',
    'audio_circuits': 'detect_audio_circuits',
    'led_driver_ics': 'detect_led_driver_ics',
    'rtc_circuits': 'detect_rtc_circuits',
    'thermocouple_rtd': 'detect_thermocouple_rtd',
    'wireless_modules': 'detect_wireless_modules',
    'transformer_feedback': 'detect_transformer_feedback',
    'i2c_address_conflicts': 'detect_i2c_address_conflicts',
    'energy_harvesting': 'detect_energy_harvesting',
    'pwm_led_dimming': 'detect_pwm_led_dimming',
    'headphone_jacks': 'detect_headphone_jack',
    'buzzer_speaker_circuits': 'detect_buzzer_speakers',
    'key_matrices': 'detect_key_matrices',
    'isolation_barriers': 'detect_isolation_barriers',
    'esd_coverage_audit': 'audit_esd_protection',
    'led_audit': 'audit_led_circuits',
    'connector_ground_audit': 'audit_connector_ground_distribution',
    'power_sequencing_validation': 'validate_power_sequencing',
    'validation_findings': None,  # Mixed detectors — can't filter by one
    # PCB sections
    'tombstoning_risk': 'analyze_tombstoning_risk',
    'thermal_pad_vias': 'analyze_thermal_pad_vias',
    # Thermal
    'thermal_assessments': 'analyze_thermal',
    # Lifecycle
    'lifecycle_findings': 'audit_bom',
    'temperature_findings': 'audit_bom',
}


def rewrite_path(path: str, analyzer_type: str) -> tuple[str, str | None]:
    """Rewrite an assertion path from old format to new.

    Returns (new_path, detector_filter) where detector_filter is the
    detector name to pre-filter findings[] by, or None if no filtering needed.
    """
    # Schematic: signal_analysis.X.rest → findings.rest (with detector filter)
    if path.startswith('signal_analysis.'):
        rest = path[len('signal_analysis.'):]
        # Split into section name and remainder
        parts = rest.split('.', 1)
        section = parts[0]
        # Handle array index: section[N] → strip index, keep for findings path
        idx_match = re.match(r'^(\w+)(\[\d+\])(.*)', section)
        if idx_match:
            section_name = idx_match.group(1)
            index = idx_match.group(2)
            after = idx_match.group(3)
            remainder = parts[1] if len(parts) > 1 else ''
            if after:
                remainder = after.lstrip('.') + ('.' + remainder if remainder else '')
            detector = _SECTION_TO_DETECTOR.get(section_name)
            new_path = f'findings{index}'
            if remainder:
                new_path += f'.{remainder}'
            return new_path, detector
        else:
            section_name = section
            remainder = parts[1] if len(parts) > 1 else ''
            detector = _SECTION_TO_DETECTOR.get(section_name)
            # Count check: signal_analysis.power_regulators → findings
            # (with detector filter, check applies to filtered count)
            new_path = 'findings'
            if remainder:
                new_path += f'.{remainder}'
            return new_path, detector

    # Schematic: rail_voltages was inside signal_analysis, now top-level
    # But assertions probably already use the full path signal_analysis.rail_voltages
    # which is handled above.

    # PCB: nested section paths
    for old_prefix in _PCB_SECTION_REMOVALS:
        if path.startswith(old_prefix):
            rest = path[len(old_prefix):]
            section_base = old_prefix.split('.')[0]
            if old_prefix.count('.') > 0:
                # e.g., dfm.violations[0].message → findings[0].message
                new_path = f'findings{rest}'
            else:
                # e.g., tombstoning_risk[0].risk_level → findings[0].risk_level
                new_path = f'findings{rest}'
            detector = _SECTION_TO_DETECTOR.get(section_base)
            return new_path, detector

    # Thermal: thermal_assessments → findings
    if path.startswith('thermal_assessments'):
        rest = path[len('thermal_assessments'):]
        return f'findings{rest}', 'analyze_thermal'

    # Lifecycle: lifecycle_findings/temperature_findings → findings
    if path.startswith('lifecycle_findings'):
        rest = path[len('lifecycle_findings'):]
        return f'findings{rest}', 'audit_bom'
    if path.startswith('temperature_findings'):
        rest = path[len('temperature_findings'):]
        return f'findings{rest}', 'audit_bom'

    # No rewrite needed
    return path, None


def migrate_assertion_file(filepath: Path, dry_run: bool = False) -> dict:
    """Migrate a single assertion file. Returns stats dict."""
    stats = {'total': 0, 'rewritten': 0, 'unchanged': 0, 'errors': 0}

    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        stats['errors'] = 1
        return stats

    assertions = data.get('assertions', [])
    analyzer_type = data.get('analyzer_type', '')
    modified = False

    for assertion in assertions:
        stats['total'] += 1
        check = assertion.get('check', {})
        path = check.get('path', '')

        if not path:
            stats['unchanged'] += 1
            continue

        new_path, detector_filter = rewrite_path(path, analyzer_type)

        if new_path != path:
            check['path'] = new_path
            if detector_filter:
                check['detector_filter'] = detector_filter
            if 'migration_note' not in assertion:
                assertion['migration_note'] = f'Path migrated from: {path}'
            stats['rewritten'] += 1
            modified = True
        else:
            stats['unchanged'] += 1

    if modified and not dry_run:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Migrate harness assertion paths for analyzer harmonization')
    parser.add_argument('harness_dir', help='Path to kicad-happy-testharness/')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would change without writing files')
    parser.add_argument('--all', action='store_true',
                        help='Migrate ALL assertion files (not just FND/BUGFIX)')

    args = parser.parse_args()

    reference_dir = Path(args.harness_dir) / 'reference'
    if not reference_dir.exists():
        print(f'Error: {reference_dir} not found', file=sys.stderr)
        return 1

    # Find assertion files
    assertion_files = []
    for filepath in reference_dir.rglob('*.json'):
        if '/assertions/' not in str(filepath):
            continue
        if args.all:
            assertion_files.append(filepath)
        else:
            # Only FND and BUGFIX files (curated, not re-seedable)
            name = filepath.name
            if '_finding' in name or '_bugfix' in name or 'finding' in name:
                assertion_files.append(filepath)

    print(f'Found {len(assertion_files)} assertion files to process')
    if args.dry_run:
        print('DRY RUN — no files will be modified')

    totals = {'total': 0, 'rewritten': 0, 'unchanged': 0, 'errors': 0}

    for filepath in sorted(assertion_files):
        stats = migrate_assertion_file(filepath, dry_run=args.dry_run)
        for k in totals:
            totals[k] += stats[k]
        if stats['rewritten'] > 0:
            rel = filepath.relative_to(reference_dir)
            print(f'  {rel}: {stats["rewritten"]} rewritten, {stats["unchanged"]} unchanged')

    print(f'\nSummary: {totals["total"]} assertions processed')
    print(f'  Rewritten: {totals["rewritten"]}')
    print(f'  Unchanged: {totals["unchanged"]}')
    print(f'  Errors: {totals["errors"]}')

    if args.dry_run and totals['rewritten'] > 0:
        print(f'\nRun without --dry-run to apply changes.')

    return 0


if __name__ == '__main__':
    sys.exit(main())
