"""Rich finding schema shared by all detectors and validators.

Every detection and validation finding uses make_finding() to produce
a self-describing dict consumable by kidoc, suggest-fixes, and lighter LLMs.
"""

from __future__ import annotations

VALID_SEVERITIES = ('error', 'warning', 'info')
VALID_CONFIDENCES = ('deterministic', 'heuristic')
VALID_EVIDENCE_SOURCES = ('datasheet', 'topology', 'heuristic_rule')
VALID_FIX_TYPES = (
    'resistor_value_change', 'capacitor_value_change',
    'add_component', 'remove_component', 'swap_connection', 'add_protection',
)


def make_finding(
    detector: str,
    rule_id: str,
    category: str,
    summary: str,
    description: str,
    severity: str = 'warning',
    confidence: str = 'heuristic',
    evidence_source: str = 'heuristic_rule',
    components: list | None = None,
    nets: list | None = None,
    pins: list | None = None,
    recommendation: str = '',
    fix_params: dict | None = None,
    report_section: str | None = None,
    impact: str | None = None,
    standard_ref: str | None = None,
    **extra,
) -> dict:
    """Build a rich finding dict with consistent structure.

    Required fields: detector, rule_id, category, summary, description.
    All other fields have sensible defaults.

    Extra kwargs are merged into the finding (e.g., domain-specific data).
    """
    finding = {
        'detector': detector,
        'rule_id': rule_id,
        'category': category,
        'summary': summary,
        'description': description,
        'components': components if components is not None else [],
        'nets': nets if nets is not None else [],
        'pins': pins if pins is not None else [],
        'severity': severity,
        'confidence': confidence,
        'evidence_source': evidence_source,
        'recommendation': recommendation,
    }
    if fix_params is not None:
        finding['fix_params'] = fix_params
    finding['report_context'] = {
        'section': report_section or category.replace('_', ' ').title(),
        'impact': impact or '',
        'standard_ref': standard_ref or '',
    }
    if extra:
        finding.update(extra)
    return finding


# ---------------------------------------------------------------------------
# Detector name constants — avoids string typos across consumers
# ---------------------------------------------------------------------------

class Det:
    """Detector name constants for filtering findings."""
    # Signal detectors
    VOLTAGE_DIVIDERS = 'detect_voltage_dividers'
    RC_FILTERS = 'detect_rc_filters'
    LC_FILTERS = 'detect_lc_filters'
    CRYSTAL_CIRCUITS = 'detect_crystal_circuits'
    OPAMP_CIRCUITS = 'detect_opamp_circuits'
    TRANSISTOR_CIRCUITS = 'detect_transistor_circuits'
    BRIDGE_CIRCUITS = 'detect_bridge_circuits'
    LED_DRIVERS = 'detect_led_drivers'
    POWER_REGULATORS = 'detect_power_regulators'
    INTEGRATED_LDOS = 'detect_integrated_ldos'
    DECOUPLING = 'detect_decoupling'
    CURRENT_SENSE = 'detect_current_sense'
    PROTECTION_DEVICES = 'detect_protection_devices'
    DESIGN_OBSERVATIONS = 'detect_design_observations'
    # Domain detectors
    BUZZER_SPEAKERS = 'detect_buzzer_speakers'
    KEY_MATRICES = 'detect_key_matrices'
    ISOLATION_BARRIERS = 'detect_isolation_barriers'
    ETHERNET_INTERFACES = 'detect_ethernet_interfaces'
    HDMI_DVI_INTERFACES = 'detect_hdmi_dvi_interfaces'
    LVDS_INTERFACES = 'detect_lvds_interfaces'
    MEMORY_INTERFACES = 'detect_memory_interfaces'
    RF_CHAINS = 'detect_rf_chains'
    RF_MATCHING = 'detect_rf_matching'
    BMS_SYSTEMS = 'detect_bms_systems'
    BATTERY_CHARGERS = 'detect_battery_chargers'
    MOTOR_DRIVERS = 'detect_motor_drivers'
    ADDRESSABLE_LEDS = 'detect_addressable_leds'
    DEBUG_INTERFACES = 'detect_debug_interfaces'
    POWER_PATH = 'detect_power_path'
    ADC_CIRCUITS = 'detect_adc_circuits'
    RESET_SUPERVISORS = 'detect_reset_supervisors'
    CLOCK_DISTRIBUTION = 'detect_clock_distribution'
    DISPLAY_INTERFACES = 'detect_display_interfaces'
    SENSOR_INTERFACES = 'detect_sensor_interfaces'
    LEVEL_SHIFTERS = 'detect_level_shifters'
    AUDIO_CIRCUITS = 'detect_audio_circuits'
    LED_DRIVER_ICS = 'detect_led_driver_ics'
    RTC_CIRCUITS = 'detect_rtc_circuits'
    THERMOCOUPLE_RTD = 'detect_thermocouple_rtd'
    WIRELESS_MODULES = 'detect_wireless_modules'
    TRANSFORMER_FEEDBACK = 'detect_transformer_feedback'
    I2C_ADDRESS_CONFLICTS = 'detect_i2c_address_conflicts'
    ENERGY_HARVESTING = 'detect_energy_harvesting'
    PWM_LED_DIMMING = 'detect_pwm_led_dimming'
    HEADPHONE_JACK = 'detect_headphone_jack'
    SOLDER_JUMPERS = 'detect_solder_jumpers'
    LABEL_ALIASES = 'detect_label_aliases'
    # Audit detectors
    ESD_AUDIT = 'audit_esd_protection'
    LED_AUDIT = 'audit_led_circuits'
    CONNECTOR_GROUND_AUDIT = 'audit_connector_ground_distribution'
    RAIL_SOURCE_AUDIT = 'audit_rail_sources'
    # Connectivity detectors
    CONNECTIVITY_SINGLE_PIN = 'analyze_connectivity'
    # Validation detectors
    PULLUPS = 'validate_pullups'
    VOLTAGE_LEVELS = 'validate_voltage_levels'
    I2C_BUS = 'validate_i2c_bus'
    SPI_BUS = 'validate_spi_bus'
    CAN_BUS = 'validate_can_bus'
    USB_BUS = 'validate_usb_bus'
    POWER_SEQUENCING = 'validate_power_sequencing'
    LED_RESISTORS = 'validate_led_resistors'
    FEEDBACK_STABILITY = 'validate_feedback_stability'


# ---------------------------------------------------------------------------
# Finding filter helpers — used by all consumers of analyzer JSON output
# ---------------------------------------------------------------------------

def get_findings(data, detector=None,
                 rule_prefix=None,
                 category=None):
    """Filter findings from an analyzer result dict.

    Args:
        data: Analyzer result dict with top-level 'findings' key.
        detector: Filter by detector name (e.g., Det.POWER_REGULATORS).
        rule_prefix: Filter by rule_id prefix (e.g., 'PU-').
        category: Filter by category (e.g., 'signal_integrity').

    Returns:
        List of matching finding dicts.
    """
    findings = data.get('findings', [])
    if detector:
        return [f for f in findings if f.get('detector') == detector]
    if rule_prefix:
        return [f for f in findings if f.get('rule_id', '').startswith(rule_prefix)]
    if category:
        return [f for f in findings if f.get('category') == category]
    return list(findings)


def group_findings(data):
    """Group findings by detector name.

    Returns:
        Dict mapping detector name to list of findings.
        Usage: group_findings(schematic).get(Det.POWER_REGULATORS, [])
    """
    groups = {}
    for f in data.get('findings', []):
        groups.setdefault(f.get('detector', ''), []).append(f)
    return groups


# ---------------------------------------------------------------------------
# Legacy key mapping — used by detection_schema / what_if / diff_analysis
# ---------------------------------------------------------------------------

DETECTOR_TO_LEGACY_KEY = {
    "detect_power_regulators": "power_regulators",
    "detect_integrated_ldos": "power_regulators",
    "detect_voltage_dividers": "voltage_dividers",
    "detect_rc_filters": "rc_filters",
    "detect_lc_filters": "lc_filters",
    "detect_crystal_circuits": "crystal_circuits",
    "detect_decoupling": "decoupling_analysis",
    "detect_current_sense": "current_sense",
    "detect_protection_devices": "protection_devices",
    "detect_opamp_circuits": "opamp_circuits",
    "detect_transistor_circuits": "transistor_circuits",
    "detect_bridge_circuits": "bridge_circuits",
    "detect_rf_matching": "rf_matching",
    "detect_rf_chains": "rf_chains",
    "detect_bms_systems": "bms_systems",
    "detect_battery_chargers": "battery_chargers",
    "detect_motor_drivers": "motor_drivers",
    "detect_ethernet_interfaces": "ethernet_interfaces",
    "detect_buzzer_speakers": "buzzer_speaker_circuits",
    "detect_key_matrices": "key_matrices",
    "detect_isolation_barriers": "isolation_barriers",
    "detect_hdmi_dvi_interfaces": "hdmi_dvi_interfaces",
    "detect_lvds_interfaces": "lvds_interfaces",
    "detect_memory_interfaces": "memory_interfaces",
    "detect_addressable_leds": "addressable_led_chains",
    "detect_debug_interfaces": "debug_interfaces",
    "detect_adc_circuits": "adc_circuits",
    "detect_reset_supervisors": "reset_supervisors",
    "detect_clock_distribution": "clock_distribution",
    "detect_display_interfaces": "display_interfaces",
    "detect_sensor_interfaces": "sensor_interfaces",
    "detect_level_shifters": "level_shifters",
    "detect_audio_circuits": "audio_circuits",
    "detect_led_driver_ics": "led_driver_ics",
    "detect_rtc_circuits": "rtc_circuits",
    "detect_thermocouple_rtd": "thermocouple_rtd",
    "detect_wireless_modules": "wireless_modules",
    "detect_transformer_feedback": "transformer_feedback",
    "detect_i2c_address_conflicts": "i2c_address_conflicts",
    "detect_energy_harvesting": "energy_harvesting",
    "detect_pwm_led_dimming": "pwm_led_dimming",
    "detect_headphone_jack": "headphone_jacks",
    "detect_power_path": "power_path",
    "detect_design_observations": "design_observations",
    "detect_led_drivers": "led_drivers",
    "audit_esd_protection": "esd_coverage_audit",
    "audit_led_circuits": "led_audit",
    "audit_connector_ground_distribution": "connector_ground_audit",
}


def group_findings_legacy(data):
    """Group findings by legacy signal_analysis key names.

    Returns {legacy_key: [finding, ...]} dict compatible with the
    old signal_analysis dict-of-lists layout.  Detector names are
    mapped via DETECTOR_TO_LEGACY_KEY so that downstream code (SCHEMAS,
    SPICE templates, --fix CLI) works unchanged.

    Detects pre-v1.3 JSON (signal_analysis wrapper, no findings[]) and
    emits a warning to stderr.  Returns empty dict in that case — callers
    should check is_old_schema() first if they need to abort early.
    """
    if "signal_analysis" in data and "findings" not in data:
        import sys
        print("Warning: this JSON uses the pre-v1.3 signal_analysis wrapper "
              "format. Re-run the analyzer to produce the current findings[] "
              "format.", file=sys.stderr)
        return {}
    sa = {}
    for f in data.get("findings", []):
        det = f.get("detector", "")
        if det:
            key = DETECTOR_TO_LEGACY_KEY.get(det, det)
            sa.setdefault(key, []).append(f)
    return sa


def is_old_schema(data):
    """Return True if data uses the pre-v1.3 signal_analysis wrapper format."""
    return "signal_analysis" in data and "findings" not in data
