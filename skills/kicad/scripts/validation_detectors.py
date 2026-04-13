"""Validation detectors — correctness checks that emit rich findings.

Separated from domain_detectors.py (which discovers circuit topologies).
These detectors check for design errors: missing components, wrong values,
protocol violations, sequencing issues.

Each validator takes an AnalysisContext (and optional detector results) and
returns a list of rich finding dicts via finding_schema.make_finding().
"""

from __future__ import annotations

import re

from kicad_types import AnalysisContext
from kicad_utils import parse_value, parse_voltage_from_net_name
from detector_helpers import (
    get_components_by_type, get_unique_ics, index_two_pin_components,
    match_ic_keywords,
)
from signal_detectors import _get_net_components
from finding_schema import make_finding


# ---------------------------------------------------------------------------
# Shared pull-up/pull-down detection helpers
# ---------------------------------------------------------------------------

def _find_pullups_on_net(
    ctx: AnalysisContext,
    net_name: str,
    resistor_nets: dict[str, tuple[str, str]],
    net_to_resistors: dict[str, list[str]],
) -> list[dict]:
    """Find pull-up resistors on a net (resistor between net and power rail).

    Returns list of dicts: [{ref, ohms, rail}].
    """
    pullups = []
    for rref in net_to_resistors.get(net_name, []):
        n1, n2 = resistor_nets.get(rref, (None, None))
        if not n1 or not n2:
            continue
        other = n2 if n1 == net_name else n1
        if ctx.is_power_net(other) and not ctx.is_ground(other):
            ohms = ctx.parsed_values.get(rref)
            pullups.append({'ref': rref, 'ohms': ohms, 'rail': other})
    return pullups


def _find_pulldowns_on_net(
    ctx: AnalysisContext,
    net_name: str,
    resistor_nets: dict[str, tuple[str, str]],
    net_to_resistors: dict[str, list[str]],
) -> list[dict]:
    """Find pull-down resistors on a net (resistor between net and ground)."""
    pulldowns = []
    for rref in net_to_resistors.get(net_name, []):
        n1, n2 = resistor_nets.get(rref, (None, None))
        if not n1 or not n2:
            continue
        other = n2 if n1 == net_name else n1
        if ctx.is_ground(other):
            ohms = ctx.parsed_values.get(rref)
            pulldowns.append({'ref': rref, 'ohms': ohms, 'rail': other})
    return pulldowns


def _get_pin_net(ctx: AnalysisContext, ref: str, pin_names: tuple[str, ...]) -> str | None:
    """Find the net connected to a pin matching any of the given names."""
    pins = ctx.ref_pins.get(ref, {})
    for pnum, (net, _) in pins.items():
        comp = ctx.comp_lookup.get(ref)
        if not comp:
            continue
        for p in comp.get('pins', []):
            if p.get('number') == pnum and p.get('name', '').upper() in pin_names:
                return net
    # Fallback: check net pin_name via ctx.nets
    for pnum, (net, _) in pins.items():
        if not net or net not in ctx.nets:
            continue
        for np in ctx.nets[net]['pins']:
            if np['component'] == ref and np.get('pin_name', '').upper() in pin_names:
                return net
    return None


def _net_has_driver(ctx: AnalysisContext, net_name: str, exclude_ref: str) -> bool:
    """Check if a net has at least one push-pull driver (non-OD/OC IC output)."""
    if not net_name or net_name not in ctx.nets:
        return False
    for p in ctx.nets[net_name]['pins']:
        if p['component'] == exclude_ref:
            continue
        comp = ctx.comp_lookup.get(p['component'])
        if not comp:
            continue
        if comp['type'] == 'ic':
            return True  # Conservative: assume IC outputs can drive
    return False


# ---------------------------------------------------------------------------
# PU-001: Missing pull-ups / pull-downs
# ---------------------------------------------------------------------------

# Pin names that typically require pull-up resistors
_PULLUP_PIN_NAMES = (
    'NRST', 'NRESET', 'RESET', 'RST', 'RESET_N', 'RST_N', 'XRES',
    'EN', 'ENABLE', 'CE', 'SHDN', 'SHUTDOWN', 'nSHDN',
    'INT', 'IRQ', 'ALERT', 'DRDY', 'BUSY', 'RDY',
    'INT1', 'INT2', 'IRQ1', 'IRQ2', 'ALERT1', 'ALERT2',
    'SDA', 'SCL', 'I2C_SDA', 'I2C_SCL',
    'MISO',  # SPI open-drain mode
    'OD', 'OPEN_DRAIN',
    'PG', 'PGOOD', 'POWER_GOOD', 'nPG',
    'FAULT', 'nFAULT', 'FLAG', 'nFLAG',
    'STAT', 'STAT1', 'STAT2', 'CHG', 'nCHG',
    'WDI', 'WDO', 'MR', 'nMR',
)

# Pin names that typically require pull-down resistors
_PULLDOWN_PIN_NAMES = (
    'BOOT0',  # STM32 boot mode selection
    'MODE', 'CFG', 'SEL',
)

# ICs known to have open-drain / open-collector outputs
_OPEN_DRAIN_IC_KEYWORDS = (
    'pca9', 'tca9', 'pcf8574', 'pcf8575',  # I2C GPIO expanders
    'mcp23', 'sx1509',                       # GPIO expanders
    'ina219', 'ina226', 'ina228', 'ina260',  # Current monitors (ALERT)
    'tmp1', 'tmp4', 'lm75', 'ds18b20',       # Temp sensors (ALERT/DQ)
    'max3', 'max1',                           # Supervisors
    'tps38', 'tps386',                        # Voltage supervisors
    'stusb', 'fusb',                          # USB PD controllers
)

# Typical pull-up value range (ohms) — flag if outside
_PULLUP_MIN_OHMS = 1000      # 1k — below this is suspicious
_PULLUP_MAX_OHMS = 100000    # 100k — above this is weak


def validate_pullups(ctx: AnalysisContext) -> list[dict]:
    """PU-001: Check for missing pull-up/pull-down resistors on logic pins.

    Scans IC pins with names matching known open-drain, reset, enable, and
    interrupt patterns. For each, checks whether a pull-up or pull-down
    resistor exists on the same net. Emits findings for missing or
    out-of-range resistors.
    """
    findings: list[dict] = []

    resistors = get_components_by_type(ctx, 'resistor')
    resistor_nets, net_to_resistors = index_two_pin_components(ctx, resistors)

    ics = get_unique_ics(ctx)
    checked_nets: set[str] = set()

    for ic in ics:
        ref = ic['reference']
        pins = ctx.ref_pins.get(ref, {})

        for pnum, (net, _) in pins.items():
            if not net or ctx.is_power_net(net) or ctx.is_ground(net):
                continue
            if net in checked_nets:
                continue

            # Get pin name
            pin_name = ''
            for p in ic.get('pins', []):
                if p.get('number') == pnum:
                    pin_name = p.get('name', '')
                    break
            if not pin_name:
                # Try from net info
                for np in ctx.nets.get(net, {}).get('pins', []):
                    if np['component'] == ref and np['pin_number'] == pnum:
                        pin_name = np.get('pin_name', '')
                        break

            pin_upper = pin_name.upper().replace('-', '').replace('_', '')

            # Check if this pin needs a pull-up
            needs_pullup = False
            for pn in _PULLUP_PIN_NAMES:
                if pn.replace('-', '').replace('_', '') == pin_upper or pin_upper.endswith(pn.replace('-', '').replace('_', '')):
                    needs_pullup = True
                    break

            # Also check if IC is a known open-drain type
            if not needs_pullup and match_ic_keywords(ic, _OPEN_DRAIN_IC_KEYWORDS):
                # For OD ICs, check ALERT/INT/OUT pins
                if any(pin_upper.startswith(p) for p in ('INT', 'IRQ', 'ALERT', 'OUT', 'DRDY', 'DQ')):
                    needs_pullup = True

            needs_pulldown = False
            for pn in _PULLDOWN_PIN_NAMES:
                if pn.replace('-', '').replace('_', '') == pin_upper:
                    needs_pulldown = True
                    break

            if not needs_pullup and not needs_pulldown:
                continue

            checked_nets.add(net)

            if needs_pullup:
                pullups = _find_pullups_on_net(ctx, net, resistor_nets, net_to_resistors)
                if not pullups:
                    # Check if another driver exists (push-pull output driving it)
                    if _net_has_driver(ctx, net, ref):
                        continue  # Net is actively driven, pull-up not strictly required

                    findings.append(make_finding(
                        detector='validate_pullups',
                        rule_id='PU-001',
                        category='signal_integrity',
                        summary=f'{ref} pin {pin_name} ({net}) missing pull-up resistor',
                        description=(
                            f'Pin {pin_name} on {ref} ({ic.get("value", "")}) is '
                            f'connected to net {net} but has no pull-up resistor. '
                            f'This pin type typically requires an external pull-up '
                            f'to a power rail for correct operation.'
                        ),
                        severity='warning',
                        confidence='heuristic',
                        evidence_source='topology',
                        components=[ref],
                        nets=[net],
                        pins=[{'ref': ref, 'pin': pin_name, 'function': 'open_drain_or_input'}],
                        recommendation=f'Add a 4.7k-10k pull-up resistor from {net} to the appropriate power rail.',
                        fix_params={
                            'type': 'add_component',
                            'components': [{'type': 'resistor', 'value': '10k',
                                            'net_from': net, 'net_to': '<power_rail>'}],
                            'basis': f'Pin {pin_name} is open-drain/input type requiring pull-up',
                        },
                        report_section='Signal Integrity',
                        impact='Pin may float or bus may not function without pull-up',
                    ))
                else:
                    # Check pull-up value range
                    for pu in pullups:
                        if pu['ohms'] is not None:
                            if pu['ohms'] < _PULLUP_MIN_OHMS:
                                findings.append(make_finding(
                                    detector='validate_pullups',
                                    rule_id='PU-001',
                                    category='signal_integrity',
                                    summary=f'{ref} pin {pin_name}: pull-up {pu["ref"]} value too low ({pu["ohms"]:.0f}R)',
                                    description=(
                                        f'Pull-up {pu["ref"]} on net {net} has value '
                                        f'{pu["ohms"]:.0f} ohms, which is below the typical '
                                        f'minimum of {_PULLUP_MIN_OHMS} ohms. This draws '
                                        f'excessive current when the output is low.'
                                    ),
                                    severity='info',
                                    confidence='heuristic',
                                    evidence_source='topology',
                                    components=[ref, pu['ref']],
                                    nets=[net],
                                    recommendation=f'Consider increasing {pu["ref"]} to 4.7k-10k.',
                                ))
                            elif pu['ohms'] > _PULLUP_MAX_OHMS:
                                findings.append(make_finding(
                                    detector='validate_pullups',
                                    rule_id='PU-001',
                                    category='signal_integrity',
                                    summary=f'{ref} pin {pin_name}: pull-up {pu["ref"]} value too high ({pu["ohms"]/1000:.0f}k)',
                                    description=(
                                        f'Pull-up {pu["ref"]} on net {net} has value '
                                        f'{pu["ohms"]/1000:.0f}k ohms, which is above the typical '
                                        f'maximum of {_PULLUP_MAX_OHMS/1000:.0f}k ohms. The signal '
                                        f'rise time may be too slow for reliable operation.'
                                    ),
                                    severity='info',
                                    confidence='heuristic',
                                    evidence_source='topology',
                                    components=[ref, pu['ref']],
                                    nets=[net],
                                    recommendation=f'Consider decreasing {pu["ref"]} to 4.7k-10k.',
                                ))

            if needs_pulldown:
                pulldowns = _find_pulldowns_on_net(ctx, net, resistor_nets, net_to_resistors)
                if not pulldowns:
                    findings.append(make_finding(
                        detector='validate_pullups',
                        rule_id='PU-001',
                        category='signal_integrity',
                        summary=f'{ref} pin {pin_name} ({net}) missing pull-down resistor',
                        description=(
                            f'Pin {pin_name} on {ref} ({ic.get("value", "")}) is '
                            f'connected to net {net} but has no pull-down resistor. '
                            f'This pin requires a defined logic level at startup.'
                        ),
                        severity='warning',
                        confidence='heuristic',
                        evidence_source='topology',
                        components=[ref],
                        nets=[net],
                        pins=[{'ref': ref, 'pin': pin_name, 'function': 'mode_select'}],
                        recommendation=f'Add a 10k pull-down resistor from {net} to GND.',
                        fix_params={
                            'type': 'add_component',
                            'components': [{'type': 'resistor', 'value': '10k',
                                            'net_from': net, 'net_to': 'GND'}],
                            'basis': f'Pin {pin_name} is a mode/boot selection pin',
                        },
                        report_section='Signal Integrity',
                        impact='Pin floats at startup, undefined behavior',
                    ))

    return findings
