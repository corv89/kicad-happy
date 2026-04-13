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


# ---------------------------------------------------------------------------
# VM-001: Cross-domain voltage mismatch
# ---------------------------------------------------------------------------

_VOLTAGE_THRESHOLDS = {
    1.8: (0.4, 1.35, 0.63, 1.17, 2.0),
    2.5: (0.4, 2.0, 0.7, 1.7, 2.75),
    3.3: (0.4, 2.4, 0.8, 2.0, 3.6),
    5.0: (0.5, 4.4, 1.5, 3.5, 5.5),
}


def _estimate_rail_voltage_for_ic(ctx: AnalysisContext, ref: str) -> float | None:
    pins = ctx.ref_pins.get(ref, {})
    for pnum, (net, _) in pins.items():
        if net and ctx.is_power_net(net) and not ctx.is_ground(net):
            v = parse_voltage_from_net_name(net)
            if v is not None:
                return v
    return None


def _closest_threshold(voltage: float) -> tuple:
    if voltage is None:
        return None
    best = None
    best_dist = float('inf')
    for v, thresh in _VOLTAGE_THRESHOLDS.items():
        dist = abs(v - voltage)
        if dist < best_dist:
            best_dist = dist
            best = thresh
    return best


def validate_voltage_levels(ctx: AnalysisContext, level_shifters: list[dict] | None = None) -> list[dict]:
    """VM-001: Detect signal nets crossing power domain boundaries without level shifting."""
    findings: list[dict] = []

    ic_voltages: dict[str, float] = {}
    for ic in get_unique_ics(ctx):
        v = _estimate_rail_voltage_for_ic(ctx, ic['reference'])
        if v is not None:
            ic_voltages[ic['reference']] = v

    shifted_nets: set[str] = set()
    if level_shifters:
        for ls in level_shifters:
            for net in ls.get('shifted_nets', []):
                shifted_nets.add(net)
            ref = ls.get('reference', ls.get('ref', ''))
            for pnum, (net, _) in ctx.ref_pins.get(ref, {}).items():
                if net and not ctx.is_power_net(net) and not ctx.is_ground(net):
                    shifted_nets.add(net)

    checked_nets: set[str] = set()

    for net_name, net_info in ctx.nets.items():
        if net_name in checked_nets or ctx.is_power_net(net_name) or ctx.is_ground(net_name):
            continue
        if net_name in shifted_nets:
            continue

        ic_pins_on_net = []
        for p in net_info.get('pins', []):
            ref = p['component']
            if ref in ic_voltages:
                ic_pins_on_net.append({
                    'ref': ref, 'pin': p.get('pin_name', ''),
                    'pin_number': p['pin_number'], 'voltage': ic_voltages[ref],
                })

        if len(ic_pins_on_net) < 2:
            continue

        voltages = set(p['voltage'] for p in ic_pins_on_net)
        if len(voltages) <= 1:
            continue

        checked_nets.add(net_name)
        v_max = max(voltages)
        v_min = min(voltages)

        low_thresh = _closest_threshold(v_min)
        if low_thresh is None:
            continue

        abs_max = low_thresh[4]
        if v_max > abs_max:
            severity = 'error'
            desc = (
                f'Net {net_name} connects ICs in {v_max}V and {v_min}V domains. '
                f'The {v_max}V output may exceed the {v_min}V input absolute maximum '
                f'rating of {abs_max}V, risking damage.'
            )
        else:
            high_thresh = _closest_threshold(v_max)
            voh = high_thresh[1] if high_thresh else v_max * 0.7
            vih = low_thresh[3]
            if voh < vih:
                severity = 'warning'
                desc = (
                    f'Net {net_name} connects ICs in {v_max}V and {v_min}V domains. '
                    f'The {v_max}V output VOH ({voh}V) may not meet the {v_min}V '
                    f'input VIH threshold ({vih}V).'
                )
            else:
                continue

        refs = list(set(p['ref'] for p in ic_pins_on_net))
        findings.append(make_finding(
            detector='validate_voltage_levels',
            rule_id='VM-001',
            category='signal_integrity',
            summary=f'Net {net_name}: {v_max}V / {v_min}V domain crossing without level shifter',
            description=desc,
            severity=severity,
            confidence='heuristic',
            evidence_source='topology',
            components=refs,
            nets=[net_name],
            pins=[{'ref': p['ref'], 'pin': p['pin'], 'function': 'signal_io'}
                  for p in ic_pins_on_net],
            recommendation=(
                f'Add a level shifter between the {v_max}V and {v_min}V domains on net {net_name}, '
                f'or verify that the connected ICs have tolerant inputs.'
            ),
            fix_params={
                'type': 'add_component',
                'components': [{'type': 'level_shifter',
                                'domain_a': f'{v_max}V', 'domain_b': f'{v_min}V',
                                'nets': [net_name]}],
                'basis': f'Net crosses {v_max}V to {v_min}V boundary',
            },
            report_section='Signal Integrity',
            impact='Risk of damage or unreliable logic levels',
        ))

    return findings


# ---------------------------------------------------------------------------
# PR-001..004: Protocol pin validation
# ---------------------------------------------------------------------------

_I2C_IC_KEYWORDS = (
    'pca9', 'tca9', 'pcf8574', 'pcf8575', 'mcp23', 'at24', 'eeprom',
    'ina219', 'ina226', 'ina228', 'ina260', 'ina3221',
    'tmp1', 'tmp4', 'lm75', 'sht3', 'sht4', 'bme280', 'bme680', 'bmp280',
    'mpu6050', 'mpu9250', 'icm20', 'lsm6', 'lis3', 'adxl3',
    'ads1015', 'ads1115', 'mcp4725', 'dac8', 'max517',
    'ds1307', 'ds3231', 'pcf8523', 'rv3028', 'rv8803',
    'si5351', 'pll', 'cdce',
    'tca6408', 'sx1509', 'pca6416',
    'as5600', 'as5048', 'vl53l',
    'is31fl', 'lp5024', 'pca9685',
    'stusb', 'fusb302', 'bq2597',
)
_I2C_SDA_NAMES = ('SDA', 'I2C_SDA', 'TWI_SDA', 'SDAI', 'SDA1', 'SDA2', 'SDATA')
_I2C_SCL_NAMES = ('SCL', 'I2C_SCL', 'TWI_SCL', 'SCLI', 'SCL1', 'SCL2', 'SCLK')
_I2C_PULLUP_RANGES = {
    'standard': (1000, 10000),
    'fast': (1000, 4700),
    'fast_plus': (470, 2200),
}

_SPI_IC_KEYWORDS = (
    'w25q', 'mx25', 'at25', 'sst26', 'is25', 'gd25',
    'mcp3', 'ads8', 'ad7', 'max11',
    'mcp49', 'dac8',
    'sx127', 'rfm9', 'cc1101', 'nrf24', 'si446',
    'enc28j', 'w5500', 'ksz8',
    'max7219', 'apa102', 'dotstar',
    'sd_card', 'sdcard',
    'lis3', 'adxl3', 'bmi160', 'icm42',
)
_SPI_CS_NAMES = ('CS', 'SS', 'NSS', 'SPI_CS', 'SPI_SS', 'CSN', 'NCS', 'CE0', 'CE1')

_CAN_IC_KEYWORDS = (
    'mcp2515', 'mcp2551', 'mcp2562', 'mcp25625',
    'sn65hvd', 'sn65hvd230', 'sn65hvd231', 'sn65hvd232',
    'tja1', 'tja1050', 'tja1051', 'tja1040', 'tja1042', 'tja1043',
    'iso1050', 'iso1042',
    'max3', 'max33',
    'adm3053',
)
_CAN_H_NAMES = ('CANH', 'CAN_H', 'CANHI', 'CAN_HIGH')
_CAN_L_NAMES = ('CANL', 'CAN_L', 'CANLO', 'CAN_LOW')

_USB_DP_NAMES = ('D+', 'DP', 'USB_DP', 'USB_D+', 'USBDP', 'D_P')
_USB_DM_NAMES = ('D-', 'DM', 'USB_DM', 'USB_D-', 'USBDM', 'D_N')


def validate_i2c_bus(ctx: AnalysisContext) -> list[dict]:
    """PR-001: Validate I2C bus integrity — pull-ups, values, address conflicts."""
    findings: list[dict] = []
    resistors = get_components_by_type(ctx, 'resistor')
    resistor_nets, net_to_resistors = index_two_pin_components(ctx, resistors)

    i2c_buses: dict[str, dict] = {}
    for ic in get_unique_ics(ctx):
        ref = ic['reference']
        if not match_ic_keywords(ic, _I2C_IC_KEYWORDS):
            continue
        sda_net = _get_pin_net(ctx, ref, _I2C_SDA_NAMES)
        scl_net = _get_pin_net(ctx, ref, _I2C_SCL_NAMES)
        if not sda_net or not scl_net:
            continue
        bus_key = f'{sda_net}:{scl_net}'
        bus = i2c_buses.setdefault(bus_key, {'sda_net': sda_net, 'scl_net': scl_net, 'devices': []})
        bus['devices'].append(ref)

    for bus_key, bus in i2c_buses.items():
        sda, scl = bus['sda_net'], bus['scl_net']
        refs = bus['devices']

        sda_pullups = _find_pullups_on_net(ctx, sda, resistor_nets, net_to_resistors)
        if not sda_pullups:
            findings.append(make_finding(
                detector='validate_i2c_bus', rule_id='PR-001', category='protocol_integrity',
                summary=f'I2C bus {sda}/{scl}: SDA missing pull-up',
                description=f'I2C bus with {len(refs)} device(s) ({", ".join(refs)}) has no pull-up on SDA net {sda}.',
                severity='error', confidence='deterministic', evidence_source='topology',
                components=refs, nets=[sda, scl],
                recommendation='Add a 4.7k pull-up resistor from SDA to VDD.',
                fix_params={'type': 'add_component', 'components': [{'type': 'resistor', 'value': '4.7k', 'net_from': sda, 'net_to': '<VDD>'}], 'basis': 'I2C spec requires pull-ups on SDA'},
                standard_ref='I2C specification UM10204 section 3.1.1', impact='I2C bus non-functional',
            ))

        scl_pullups = _find_pullups_on_net(ctx, scl, resistor_nets, net_to_resistors)
        if not scl_pullups:
            findings.append(make_finding(
                detector='validate_i2c_bus', rule_id='PR-001', category='protocol_integrity',
                summary=f'I2C bus {sda}/{scl}: SCL missing pull-up',
                description=f'I2C bus with {len(refs)} device(s) ({", ".join(refs)}) has no pull-up on SCL net {scl}.',
                severity='error', confidence='deterministic', evidence_source='topology',
                components=refs, nets=[sda, scl],
                recommendation='Add a 4.7k pull-up resistor from SCL to VDD.',
                fix_params={'type': 'add_component', 'components': [{'type': 'resistor', 'value': '4.7k', 'net_from': scl, 'net_to': '<VDD>'}], 'basis': 'I2C spec requires pull-ups on SCL'},
                standard_ref='I2C specification UM10204 section 3.1.1', impact='I2C bus non-functional',
            ))

        for net_label, pullups in [('SDA', sda_pullups), ('SCL', scl_pullups)]:
            for pu in pullups:
                if pu['ohms'] is not None:
                    low, high = _I2C_PULLUP_RANGES['standard']
                    if pu['ohms'] < low or pu['ohms'] > high:
                        findings.append(make_finding(
                            detector='validate_i2c_bus', rule_id='PR-001', category='protocol_integrity',
                            summary=f'I2C {net_label} pull-up {pu["ref"]} out of range ({pu["ohms"]:.0f}R)',
                            description=f'I2C {net_label} pull-up {pu["ref"]} is {pu["ohms"]:.0f} ohms. Recommended: {low}-{high} ohms for standard-mode.',
                            severity='info', confidence='heuristic', evidence_source='topology',
                            components=[pu['ref']], nets=[bus['sda_net'] if net_label == 'SDA' else bus['scl_net']],
                            recommendation=f'Use a pull-up in the {low}-{high} ohm range.',
                            fix_params={'type': 'resistor_value_change', 'component': pu['ref'], 'current_value': pu['ohms'], 'target_range': [low, high], 'suggested_value': 4700},
                            standard_ref='I2C specification UM10204 Table 10',
                        ))

        # Address conflict: flag multiple same-IC-type on same bus
        if len(refs) >= 2:
            value_counts: dict[str, list[str]] = {}
            for ref in refs:
                comp = ctx.comp_lookup.get(ref)
                if comp:
                    val = comp.get('value', '').lower()
                    value_counts.setdefault(val, []).append(ref)
            for val, refs_with_val in value_counts.items():
                if len(refs_with_val) > 1:
                    findings.append(make_finding(
                        detector='validate_i2c_bus', rule_id='PR-001', category='protocol_integrity',
                        summary=f'I2C bus: possible address conflict — {len(refs_with_val)}x {val}',
                        description=f'Multiple {val} ({", ".join(refs_with_val)}) on same I2C bus. Verify address pins differ.',
                        severity='warning', confidence='heuristic', evidence_source='topology',
                        components=refs_with_val, nets=[bus['sda_net'], bus['scl_net']],
                        recommendation='Verify address pin configurations (A0/A1/A2) differ.',
                        impact='Address conflict causes bus corruption',
                    ))

    return findings


def validate_spi_bus(ctx: AnalysisContext) -> list[dict]:
    """PR-002: Validate SPI bus — CS pull-ups."""
    findings: list[dict] = []
    resistors = get_components_by_type(ctx, 'resistor')
    resistor_nets, net_to_resistors = index_two_pin_components(ctx, resistors)

    for ic in get_unique_ics(ctx):
        ref = ic['reference']
        if not match_ic_keywords(ic, _SPI_IC_KEYWORDS):
            continue
        cs_net = _get_pin_net(ctx, ref, _SPI_CS_NAMES)
        if cs_net and not ctx.is_power_net(cs_net) and not ctx.is_ground(cs_net):
            pullups = _find_pullups_on_net(ctx, cs_net, resistor_nets, net_to_resistors)
            if not pullups:
                findings.append(make_finding(
                    detector='validate_spi_bus', rule_id='PR-002', category='protocol_integrity',
                    summary=f'SPI device {ref}: CS pin ({cs_net}) missing pull-up',
                    description=f'SPI CS on {ref} ({ic.get("value", "")}) net {cs_net} has no pull-up. Device may be inadvertently selected during reset.',
                    severity='warning', confidence='heuristic', evidence_source='topology',
                    components=[ref], nets=[cs_net],
                    recommendation=f'Add a 10k pull-up on {cs_net}.',
                    fix_params={'type': 'add_component', 'components': [{'type': 'resistor', 'value': '10k', 'net_from': cs_net, 'net_to': '<VDD>'}], 'basis': 'SPI CS should be pulled high when not driven'},
                    impact='Device may be selected during reset causing bus contention',
                ))
    return findings


def validate_can_bus(ctx: AnalysisContext) -> list[dict]:
    """PR-003: Validate CAN bus — termination resistors."""
    findings: list[dict] = []
    resistors = get_components_by_type(ctx, 'resistor')
    resistor_nets, net_to_resistors = index_two_pin_components(ctx, resistors)

    can_transceivers = []
    for ic in get_unique_ics(ctx):
        if match_ic_keywords(ic, _CAN_IC_KEYWORDS):
            ref = ic['reference']
            canh = _get_pin_net(ctx, ref, _CAN_H_NAMES)
            canl = _get_pin_net(ctx, ref, _CAN_L_NAMES)
            if canh or canl:
                can_transceivers.append({'ref': ref, 'value': ic.get('value', ''), 'canh': canh, 'canl': canl})

    for xcvr in can_transceivers:
        canh, canl = xcvr['canh'], xcvr['canl']
        if not canh or not canl:
            continue
        term_found = False
        for rref in net_to_resistors.get(canh, []):
            n1, n2 = resistor_nets.get(rref, (None, None))
            if not n1 or not n2:
                continue
            other = n2 if n1 == canh else n1
            if other == canl:
                ohms = ctx.parsed_values.get(rref)
                if ohms is not None and 100 <= ohms <= 150:
                    term_found = True
                    break
        if not term_found:
            findings.append(make_finding(
                detector='validate_can_bus', rule_id='PR-003', category='protocol_integrity',
                summary=f'CAN transceiver {xcvr["ref"]}: no 120R termination',
                description=f'CAN transceiver {xcvr["ref"]} ({xcvr["value"]}) has no 120R termination between CANH ({canh}) and CANL ({canl}).',
                severity='warning', confidence='deterministic', evidence_source='topology',
                components=[xcvr['ref']], nets=[n for n in [canh, canl] if n],
                recommendation=f'Add a 120R resistor between {canh} and {canl} if at bus end.',
                fix_params={'type': 'add_component', 'components': [{'type': 'resistor', 'value': '120', 'net_from': canh, 'net_to': canl}], 'basis': 'ISO 11898 requires 120R termination'},
                standard_ref='ISO 11898-2 section 7.3', impact='Bus reflections cause communication errors',
            ))
    return findings


def validate_usb_bus(ctx: AnalysisContext) -> list[dict]:
    """PR-004: Validate USB data lines — series resistors."""
    findings: list[dict] = []
    resistors = get_components_by_type(ctx, 'resistor')
    resistor_nets, net_to_resistors = index_two_pin_components(ctx, resistors)

    for comp in ctx.components:
        if comp['type'] != 'connector':
            continue
        val_lib = (comp.get('value', '') + ' ' + comp.get('lib_id', '')).lower()
        if 'usb' not in val_lib:
            continue
        ref = comp['reference']
        dp_net = _get_pin_net(ctx, ref, _USB_DP_NAMES)
        dm_net = _get_pin_net(ctx, ref, _USB_DM_NAMES)
        if not dp_net and not dm_net:
            continue

        for net_label, net in [('D+', dp_net), ('D-', dm_net)]:
            if not net:
                continue
            series_r = [rref for rref in net_to_resistors.get(net, [])
                        if ctx.parsed_values.get(rref) is not None and 15 <= ctx.parsed_values[rref] <= 33]
            if not series_r and 'usb_c' not in val_lib and 'usb3' not in val_lib:
                findings.append(make_finding(
                    detector='validate_usb_bus', rule_id='PR-004', category='protocol_integrity',
                    summary=f'USB connector {ref}: {net_label} ({net}) missing series resistor',
                    description=f'USB {net_label} on {ref} has no series resistor (typically 22R for USB 2.0).',
                    severity='info', confidence='heuristic', evidence_source='topology',
                    components=[ref], nets=[net],
                    recommendation=f'Add a 22R series resistor on {net_label} near the connector.',
                    fix_params={'type': 'add_component', 'components': [{'type': 'resistor', 'value': '22', 'net_from': net, 'net_to': f'{net}_MCU'}], 'basis': 'USB 2.0 recommends 22R series termination'},
                    standard_ref='USB 2.0 specification section 7.1.2',
                ))
    return findings
