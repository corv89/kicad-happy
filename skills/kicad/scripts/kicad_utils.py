"""
Shared utility functions for KiCad schematic and PCB analyzers.

Contains component classification, value parsing, net name classification,
and other helpers extracted from analyze_schematic.py.
"""

import re


# Coordinate matching tolerance (mm) — used across net building and connectivity analysis
COORD_EPSILON = 0.01

# Legacy .sch files use integer mils (1/1000 inch).  The conversion pipeline
# (mil * 0.0254 → round(4) → rotate via trig → round(4)) accumulates
# floating-point error that can exceed COORD_EPSILON.  Snapping back to the
# nearest mil grid after all transforms eliminates the drift.
_MIL_MM = 0.0254  # 1 mil in mm

def snap_to_mil_grid(x_mm: float) -> float:
    """Snap a mm coordinate to the nearest mil grid point."""
    return round(x_mm / _MIL_MM) * _MIL_MM

# Regulator Vref lookup table — maps part number prefixes to their internal
# reference voltage.  Used by the feedback divider Vout estimator instead of
# guessing from a list.  Entries are checked in order; first prefix match wins.
# When a part isn't found here the analyzer falls back to the heuristic sweep.
_REGULATOR_VREF: dict[str, float] = {
    # TI switching regulators — verified against TI datasheets 2026-03-31
    "TPS6100": 0.5,                                               # TPS61000/01 FB = 0.5V (datasheet)
    "TPS6102": 0.595,  "TPS6103": 0.595,                          # TPS61020/23/30 FB = 0.595V (datasheet SLVS510)
    "TPS5430": 1.221,  "TPS5450": 1.221,  "TPS5410": 1.221,     # TPS5430/50/10 Vref = 1.221V (datasheet)
    "TPS54160": 0.8,   "TPS54260": 0.8,   "TPS54360": 0.8,      # TPS541x0/542x0/543x0 FB = 0.8V (datasheet)
    "TPS54040": 0.8,   "TPS54060": 0.8,                          # TPS54040/60 Vref = 0.8V (datasheet)
    "TPS56": 0.6,                                                 # TPS560200 VSENSE = 0.6V (datasheet)
    "TPS6300": 0.5,    "TPS6301": 0.5,                           # TPS63000/01 VFB = 0.5V (datasheet)
    "TPS6310": 0.5,                                               # TPS631000 VFB = 0.5V (datasheet SLVSEK5)
    "LMR514": 0.8,     "LMR516": 0.8,                            # LMR51440/60 Vref = 0.8V (datasheet)
    "LMR336": 1.0,     "LMR338": 1.0,                            # LMR33630/60 Vref = 1.0V (datasheet)
    "LMR380": 1.0,                                                # LMR38010 VFB = 1.0V (datasheet SNVSB89)
    "LM258": 1.23,     "LM259": 1.23,                            # LM2596/LM2585 VFB = 1.23V (datasheet)
    "LMZ2": 0.795,                                                # LMZ23610 VFB = 0.795V (datasheet)
    "LM614": 1.0,      "LM619": 1.0,                             # LM61495 VFB = 1.0V (datasheet, 0.99/1.0/1.01)
    # TI LDOs
    "TLV759": 0.55,                                               # TLV759P (adjustable) FB = 0.55V (datasheet)
    "TPS7A": 1.19,                                                # TPS7A49 VFB = 1.185V typ (datasheet, 1.19 is ≈)
    # Analog Devices / Linear Tech — verified 2026-03-31
    "LT361": 0.6,      "LT362": 0.6,                             # LTC3610/3620 VFB = 0.6V (datasheet)
    "LT810": 0.97,     "LT811": 0.97,                            # LT8610/8614 VFB = 0.970V (datasheet)
    "LT860": 0.97,     "LT862": 0.97,                            # LT8640/8620 VFB = 0.970V (datasheet)
    "LT871": 1.213,                                               # LT8710 FBX = 1.213V (datasheet)
    "LTM46": 0.6,                                                 # LTM4600 VFB = 0.6V (datasheet)
    # Richtek
    "RT5": 0.6,         "RT6": 0.6,                              # RT5785/RT6150 VFB = 0.6V (datasheet)
    "RT2875": 0.6,                                                # RT2875 VFB = 0.6V (datasheet)
    # MPS
    "MP1": 0.8,         "MP2": 0.8,                               # MP1584/MP2315 VFB = 0.8V (datasheet)
    # Microchip
    "MIC29": 1.24,                                                # MIC29150/29300 Vref = 1.24V (datasheet)
    # Diodes Inc
    "AP736": 0.8,                                                 # AP7365 adjustable VFB = 0.8V (datasheet)
    "AP73": 0.6,                                                  # AP7362/63 adjustable VFB = 0.6V (datasheet)
    "AP2112": 0.8,                                                # AP2112 adjustable Vref = 0.8V (datasheet)
    "AP3015": 1.23,                                               # AP3015A VFB = 1.23V (datasheet, 1.205/1.23/1.255)
    # ST
    "LD1117": 1.25,    "LDL1117": 1.25,   "LD33": 1.25,         # LD1117 family Vref = 1.25V (datasheet)
    # ON Semi
    "NCP1117": 1.25,                                              # NCP1117 Vref = 1.25V (datasheet)
    # SY (Silergy)
    "SY8": 0.6,                                                   # SY8089 FB = 0.6V (datasheet)
    # Maxim
    "MAX5035": 1.22,    "MAX5033": 1.22,                          # MAX5035/33 VFB = 1.22V (datasheet)
    "MAX1771": 1.5,     "MAX1709": 1.25,                          # MAX1771 Vref = 1.5V, MAX1709 VFB = 1.25V (datasheet)
    "MAX17760": 0.8,                                               # MAX17760 FB = 0.8V (datasheet)
    # ISL (Renesas/Intersil)
    "ISL854": 0.6,      "ISL850": 0.8,                            # ISL85410 = 0.6V, ISL85003 = 0.8V (datasheets)
    # XL (XLSEMI)
    "XL70": 1.25,                                                  # XL7015 VFB = 1.25V (datasheet)
    # Generic (well-established values)
    "LM317": 1.25,     "LM337": 1.25,
    "AMS1117": 1.25,   "AMS1085": 1.25,
    "LM78": 1.25,      "LM79": 1.25,
    "LM1117": 1.25,
    # NOTE: Removed entries that couldn't be verified against datasheets:
    # TPS6102/6103 (0.595V unverified), TPS542/543/544 (mixed family, 0.6-0.8V),
    # TPS55 (TPS55340=1.229V, not 0.6V), TPS40 (TPS40200=0.7V, not 0.6V),
    # TPS6208-6215 (mixed, 0.45V-0.8V), TPS7B (fixed output only),
    # LM516 (LM5160=2.0V), LT364/365 (mixed/battery charger),
    # LT801/802/872 (no such parts), LTC34 (mixed), LTM82 (mixed),
    # MP8 (mixed), AP6 (mixed), MIC55/MCP170/NCV4 (fixed output only),
    # TLV620/621 (unverified), LM340 (fixed, redundant with LM78),
    # LMZ3 (unclear FB), LM260/261 (LM26001 Vref unclear).
}

# Keywords for classifying MOSFET/BJT load type from net names.
# Used by _classify_load() for transistor analysis and by net classification
# for the "output_drive" net class.  Keys are load type names, values are
# keyword tuples matched as substrings of the uppercased net name.
# Avoid short prefixes that appear inside unrelated words:
#   "SOL" matches MISO_LEVEL, ISOL → use SOLENOID only
#   "MOT" matches REMOTE → use MOTOR only
_LOAD_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "motor": ("MOTOR",),
    "heater": ("HEAT", "HTR", "HEATER"),
    "fan": ("FAN",),
    "solenoid": ("SOLENOID",),
    "valve": ("VALVE",),
    "pump": ("PUMP",),
    "relay": ("RELAY", "RLY"),
    "speaker": ("SPEAK", "SPK"),
    "buzzer": ("BUZZ", "BZR", "BUZZER"),
    "lamp": ("LAMP", "BULB"),
}

# Flattened keyword set for net classification (output_drive class).
# Includes LED/PWM which aren't load types but are output drive signals.
_OUTPUT_DRIVE_KEYWORDS: tuple[str, ...] = (
    "LED", "PWM",
    *{kw for kws in _LOAD_TYPE_KEYWORDS.values() for kw in kws},
)


def lookup_regulator_vref(value: str, lib_id: str) -> tuple[float | None, str]:
    """Look up a regulator's internal Vref from its value or lib_id.

    Returns (vref, source) where source is "lookup" if found, or (None, "")
    if not.  Tries the value field first (usually the part number), then the
    lib_id part name after the colon.
    """
    candidates = [value.upper()]
    if ":" in lib_id:
        candidates.append(lib_id.split(":")[-1].upper())
    # Check for fixed-output voltage suffix (e.g., LM2596S-12, AMS1117-3.3,
    # TLV1117LV-33, RT9013-18GV — patterns: -3.3, -33, -3V3, -1V8, -12)
    for candidate in candidates:
        m = re.search(r'[-_](\d+)V(\d+)', candidate)
        if m:
            return float(f"{m.group(1)}.{m.group(2)}"), "fixed_suffix"
        m = re.search(r'[-_](\d+\.\d+)(?:V)?(?=[^0-9]|$)', candidate)
        if m:
            fixed_v = float(m.group(1))
            if 0.5 <= fixed_v <= 60:
                return fixed_v, "fixed_suffix"
        m = re.search(r'[-_](\d{2})(?=[^0-9.]|$)', candidate)
        if m:
            # Two-digit suffix: interpret as voltage with implicit decimal
            # 33 → 3.3V, 18 → 1.8V, 25 → 2.5V, 50 → 5.0V, 12 → 1.2V
            digits = m.group(1)
            fixed_v = float(digits[0] + "." + digits[1])
            if 0.5 <= fixed_v <= 9.9:
                return fixed_v, "fixed_suffix"
    for candidate in candidates:
        for prefix, vref in _REGULATOR_VREF.items():
            if candidate.startswith(prefix.upper()):
                return vref, "lookup"
    return None, ""


def parse_voltage_from_net_name(net_name: str) -> float | None:
    """Try to extract a voltage value from a power net name.

    Examples: '+3V3' → 3.3, '+5V' → 5.0, '+12V' → 12.0, '+1V8' → 1.8,
    'VCC_3V3' → 3.3, '+2.5V' → 2.5, 'VBAT' → None
    """
    if not net_name:
        return None
    # Pattern: digits V digits  (e.g. 3V3 → 3.3, 1V8 → 1.8)
    m = re.search(r'(\d+)V(\d+)', net_name, re.IGNORECASE)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    # Pattern: digits.digits V  or  digits V  (e.g. 3.3V, 5V, 12V)
    m = re.search(r'(\d+\.?\d*)V', net_name, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def format_frequency(hz: float) -> str:
    """Format a frequency in Hz to a human-readable string with SI prefix."""
    if hz >= 1e9:
        return f"{hz / 1e9:.2f} GHz"
    elif hz >= 1e6:
        return f"{hz / 1e6:.2f} MHz"
    elif hz >= 1e3:
        return f"{hz / 1e3:.2f} kHz"
    else:
        return f"{hz:.2f} Hz"


def parse_value(value_str: str, component_type: str | None = None) -> float | None:
    """Parse an engineering-notation component value to a float.

    Handles: 10K, 4.7u, 100n, 220p, 1M, 2.2m, 47R, 0R1, 4K7, 1R0, etc.
    Returns None if unparseable.

    If component_type is "capacitor" and the result is a bare integer >=1.0
    (no unit suffix), treat it as picofarads (KH-153: legacy KiCad 5 convention).
    """
    # EQ-068: SI prefix: p=1e-12 n=1e-9 u=1e-6 m=1e-3 k=1e3 M=1e6
    if not value_str:
        return None

    # Strip tolerance, voltage rating, package, and other suffixes
    # Common formats: "680K 1%", "220k/R0402", "22uF/6.3V/20%/X5R/C0603"
    # KiCad 9 uses space-separated units: "18 pF", "4.7 uF" — rejoin if
    # the second token starts with an SI prefix letter.
    parts = value_str.strip().split("/")[0].split()
    if len(parts) >= 2 and parts[1] and parts[1][0] in "pnuµmkKMGRr":
        s = parts[0] + parts[1]
    else:
        s = parts[0] if parts else ""
    # KH-112: Ferrite bead impedance notation (600R/200mA, 120R@100MHz)
    # is not a parseable component value — return None to avoid nonsensical results
    if re.search(r'\d+[Rr]\s*[/@]\s*\d', s):
        return None

    # Strip trailing unit words (mOhm, Ohm, ohm, ohms) before single-char stripping
    s = re.sub(r'[Oo]hms?$', '', s)
    s = s.rstrip("FHΩVfhv%")         # strip trailing unit letters

    if not s:
        return None

    # Multiplier map (SI prefixes used in EE)
    multipliers = {
        "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
        "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9,
        "R": 1, "r": 1,  # "R" as decimal point: 4R7 = 4.7, 0R1 = 0.1
    }

    # Handle prefix-first European notation: "u1" -> 0.1e-6, "p47" -> 0.47e-12
    # The letter replaces the decimal point; when it comes first, implied leading 0.
    if len(s) >= 2 and s[0] in multipliers and s[1:].isdigit():
        mult = multipliers[s[0]]
        try:
            return float(f"0.{s[1:]}") * mult
        except ValueError:
            pass

    # Handle embedded multiplier: "4K7" -> 4.7e3, "0R1" -> 0.1, "1R0" -> 1.0
    for suffix, mult in multipliers.items():
        if suffix in s and not s.endswith(suffix):
            idx = s.index(suffix)
            before = s[:idx]
            after = s[idx + 1:]
            if before.replace(".", "").isdigit() and after.isdigit():
                try:
                    return float(f"{before}.{after}") * mult
                except ValueError:
                    pass

    # Handle trailing multiplier: "10K", "100n", "4.7u"
    if s[-1] in multipliers:
        mult = multipliers[s[-1]]
        try:
            return float(s[:-1]) * mult
        except ValueError:
            return None

    # Plain number: "100", "47", "0.1"
    try:
        result = float(s)
        # KH-153: Bare integers for capacitors are picofarads in legacy schematics
        if component_type == "capacitor" and result >= 1.0:
            result *= 1e-12
        return result
    except ValueError:
        return None


def parse_tolerance(value_str: str) -> float | None:
    """Extract tolerance percentage from a component value string.

    Returns tolerance as a fraction (0.01 for 1%, 0.05 for 5%, etc.),
    or None if no tolerance is found in the string.

    Examples:
        "680K 1%"                            -> 0.01
        "22uF/6.3V/20%/X5R"                 -> 0.20
        "10K 5%"                             -> 0.05
        "0.1uF/25V(10%)"                    -> 0.10
        ".1uF/X7R/+-10%"                    -> 0.10
        "0.02±1%"                            -> 0.01
        "02.0001_R0402_0R_1%"               -> 0.01
        "033uF_0603_Ceramic_Capacitor,_10%"  -> 0.10
        "100nF"                              -> None
    """
    if not value_str:
        return None
    # Split on all common delimiters: / space _ , ± and also break on ( boundaries
    tokens = re.split(r'[/\s_,±]+', value_str)
    for token in tokens:
        # Strip parentheses and +- prefixes
        cleaned = token.strip('()+-')
        m = re.match(r'^(\d+(?:\.\d+)?)\s*%$', cleaned)
        if m:
            return float(m.group(1)) / 100.0
        # Also try extracting from within parentheses: "25V(10%)" -> "10%"
        inner = re.search(r'\((\d+(?:\.\d+)?)\s*%\)', token)
        if inner:
            return float(inner.group(1)) / 100.0
    return None


def classify_component(ref: str, lib_id: str, value: str, is_power: bool = False, footprint: str = "", in_bom: bool = False) -> str:
    """Classify component type from reference designator and library."""
    # Power symbols: trust the lib_symbol (power) flag unconditionally.
    # KH-080: Components in the power: library WITHOUT the (power) flag
    # (e.g., DD4012SA buck converter) are real parts, not power symbols —
    # only treat them as power symbols if they're not in BOM.
    if is_power:
        return "power_symbol"
    if lib_id.startswith("power:") and not in_bom:
        return "power_symbol"
    # Fallback: #PWR references are always power symbols even if the
    # (power) flag is missing from lib_symbols (can happen after KiCad
    # version upgrades that reorganize the symbol library structure).
    # KiCad uses #PWR for all power symbols including GND, VCC, +3V3, etc.
    if ref.startswith("#PWR"):
        return "power_symbol"

    prefix = ""
    for c in ref:
        if c.isalpha() or c == "#":
            prefix += c
        else:
            break

    type_map = {
        # Passive components
        "R": "resistor", "RS": "resistor", "RN": "resistor_network",
        "RM": "resistor_network", "RA": "resistor_network",
        "C": "capacitor", "VC": "capacitor", "L": "inductor",
        "D": "diode", "TVS": "diode", "CR": "diode", "V": "varistor",
        # Semiconductors
        "Q": "transistor", "FET": "transistor",
        "U": "ic", "IC": "ic",
        # Connectors and mechanical
        "J": "connector", "P": "connector",
        "SW": "switch", "S": "switch", "BUT": "switch", "BTN": "switch", "BUTTON": "switch",
        "K": "relay",
        "F": "fuse", "FUSE": "fuse",
        "Y": "crystal",
        # Connector prefixes that conflict with single-char fallback (LAN→L→inductor)
        "LAN": "connector", "CON": "connector", "USB": "connector",
        "HDMI": "connector", "RJ": "connector", "ANT": "connector",
        "BT": "battery",
        "BZ": "buzzer", "LS": "speaker", "SP": "speaker",
        "OK": "optocoupler", "OC": "optocoupler",
        "NTC": "thermistor", "TH": "thermistor", "RT": "thermistor",
        "PTC": "thermistor",
        "VAR": "varistor", "RV": "varistor",
        "SAR": "surge_arrester",
        "NT": "net_tie",
        "MOV": "varistor",
        "A": "ic",
        "TP": "test_point",
        "MH": "mounting_hole", "H": "mounting_hole",
        "FB": "ferrite_bead", "FL": "filter",
        "LED": "led",
        "T": "transformer", "TR": "transformer",
        # Mechanical/manufacturing
        "FID": "fiducial",
        "MK": "fiducial",
        "JP": "jumper", "SJ": "jumper",
        "LOGO": "graphic",
        "MP": "mounting_hole",
        "#PWR": "power_flag", "#FLG": "flag",
    }

    # --- Full prefix match: high confidence ---
    result = type_map.get(prefix)
    if result:
        val_low = value.lower() if value else ""
        lib_low = lib_id.lower() if lib_id else ""
        fp_low = footprint.lower() if footprint else ""
        if any(x in val_low or x in lib_low or x in fp_low
               for x in ("testpad", "test_pad", "testpoint", "test_point")):
            return "test_point"
        # Crystal/oscillator override: Q-prefix crystals (Q for quartz),
        # CR-prefix oscillators, or any prefix where lib_id clearly says crystal/oscillator
        if result not in ("crystal", "oscillator"):
            has_xtal = any(x in lib_low for x in ("crystal", "xtal"))
            has_osc = "oscillator" in lib_low
            if has_xtal:
                return "crystal"
            if has_osc:
                return "oscillator"
        if result == "varistor":
            if ("r_pot" in lib_low or "pot" in lib_low or "potentiometer" in lib_low
                    or "potentiometer" in val_low):
                return "resistor"
            if ("regulator" in lib_low or "regulator" in fp_low
                    or any(x in val_low for x in ("ams1117", "lm78", "lm317",
                                                   "ld1117", "lm1117", "ap1117"))):
                return "ic"
        if result == "transformer":
            # KH-111: Common-mode chokes — classify as inductor, not transformer
            if any(x in val_low for x in ("cmc", "common mode", "common_mode",
                                           "rfcmf", "acm", "dlw")):
                return "inductor"
            if any(x in lib_low for x in ("common_mode", "cmc", "emi_filter")):
                return "inductor"
            if any(x in lib_low or x in val_low or x in fp_low
                   for x in ("mosfet", "fet", "transistor", "bjt",
                             "q_npn", "q_pnp", "q_nmos", "q_pmos")):
                return "transistor"
            if any(x in lib_low or x in val_low
                   for x in ("amplifier", "rf_amp", "mmic")):
                return "ic"
        if result == "thermistor" and any(x in lib_low or x in val_low
                                          for x in ("fuse", "polyfuse", "pptc",
                                                    "reset fuse", "ptc fuse")):
            return "fuse"
        if result == "thermistor" and any(x in lib_low or x in val_low
                                          for x in ("mov", "varistor")):
            return "varistor"
        if result == "diode" and re.search(r'(?<![a-z])led(?![a-z])', lib_low + " " + val_low):
            return "led"
        # KH-122: Addressable LEDs (SK6812, WS2812) with D prefix
        if result == "diode" and any(k in val_low or k in lib_low
                                     for k in ("ws2812", "ws2813", "ws2815",
                                               "sk6812", "apa102", "apa104",
                                               "sk9822", "ws2811", "neopixel")):
            return "led"
        if result == "inductor":
            if any(x in lib_low or x in val_low for x in ("ferrite", "bead")):
                return "ferrite_bead"
            # KH-112: Ferrite bead impedance notation (600R/200mA, 120R@100MHz)
            if re.search(r'\d+[Rr]\s*[/@]\s*\d', value):
                return "ferrite_bead"
        # KH-106: MX/Cherry/Kailh keyboard switches (K prefix maps to relay)
        if result == "relay":
            if any(x in lib_low for x in ("mx", "cherry", "kailh", "gateron",
                                           "alps_hybrid", "key_switch")):
                return "switch"
            if any(x in val_low for x in ("mx-", "cherry", "kailh", "gateron")):
                return "switch"
        return result

    # --- No full-prefix match.  Try lib_id / value before single-char fallback ---
    # This ordering ensures that DA1 with lib=Analog_DAC gets "ic" (not D→diode),
    # and PS1 with lib=Regulator_Linear gets "ic" (not P→connector).
    val_lower = value.lower() if value else ""
    lib_lower = lib_id.lower() if lib_id else ""

    if any(x in val_lower for x in ["mountinghole", "mounting_hole"]):
        return "mounting_hole"
    if any(x in val_lower for x in ["fiducial"]):
        return "fiducial"
    if any(x in val_lower for x in ["testpad", "test_pad"]):
        return "test_point"
    if any(x in lib_lower for x in ["mounting_hole", "mountinghole"]):
        return "mounting_hole"
    if any(x in lib_lower for x in ["fiducial"]):
        return "fiducial"
    if any(x in lib_lower for x in ["test_point", "testpoint", "testpad", "test_pad"]):
        return "test_point"

    # X prefix: crystal or oscillator if value/lib suggests it, otherwise connector
    # Distinguish passive crystals (need load caps) from active MEMS/IC oscillators
    if prefix == "X":
        # Active oscillator ICs (MEMS, TCXO, VCXO) — have VCC/GND/OUT, no load caps
        if any(x in lib_lower for x in ["oscillator"]) and not any(x in lib_lower for x in ["crystal", "xtal"]):
            return "oscillator"
        if any(x in val_lower for x in ["dsc6", "si5", "sg-", "asfl", "sit8", "asco"]):
            return "oscillator"
        # Passive crystals
        # Also catch compact frequency notation like "8M", "12M", "32.768K"
        if any(x in val_lower for x in ["xtal", "crystal", "mhz", "khz", "osc"]):
            return "crystal"
        if re.match(r'^\d+\.?\d*[mkMK]$', value):
            return "crystal"
        if any(x in lib_lower for x in ["crystal", "xtal", "osc", "clock"]):
            return "crystal"
        return "connector"

    # MX key switches (keyboard projects)
    if prefix == "MX" or "cherry" in val_lower or "kailh" in val_lower:
        return "switch"

    # Common prefixes that are context-dependent
    if prefix in ("RST", "RESET", "PHYRST"):
        return "switch"  # reset buttons/circuits
    if prefix == "BAT" or prefix == "BATSENSE":
        return "connector"  # battery connector
    if prefix == "RGB" or prefix == "PWRLED":
        return "led"

    # Library-based fallback for non-standard reference prefixes
    if "thermistor" in lib_lower or "thermistor" in val_lower or "ntc" in val_lower:
        return "thermistor"
    if "varistor" in lib_lower or "varistor" in val_lower:
        return "varistor"
    if "optocoupler" in lib_lower or "opto" in lib_lower:
        return "optocoupler"
    lib_prefix = lib_lower.split(":")[0] if ":" in lib_lower else lib_lower
    if lib_prefix == "led" or val_lower.startswith("led/") or val_lower == "led":
        return "led"
    if "ws2812" in val_lower or "neopixel" in val_lower or "sk6812" in val_lower:
        return "led"
    if "jumper" in lib_lower or val_lower in ("opened", "closed") or val_lower.startswith("opened("):
        return "jumper"
    # Connector detection: lib names and common connector part number patterns
    if "connector" in lib_lower or "conn_" in val_lower:
        return "connector"
    # KH-110: Audio jack connectors
    if "connector_audio" in lib_lower or "audio_jack" in lib_lower:
        return "connector"
    if any(value.startswith(p) for p in ("PJ-3", "SJ-3", "MJ-3")):
        return "connector"
    if any(x in val_lower for x in ["usb_micro", "usb_c", "usb-c", "rj45", "rj11",
                                     "pin_header", "pin_socket", "barrel_jack"]):
        return "connector"
    # JST and similar connector part numbers in value
    if any(value.startswith(p) for p in ["S3B-", "S4B-", "S6B-", "S8B-", "SM0",
                                        "B2B-", "BM0", "MISB-", "ZL2", "ZL3",
                                        "HN1x", "NH1x", "NS(HN", "NS(NH",
                                        "FL40", "FL20", "FPV-", "SCJ3",
                                        "TFC-", "68020-", "RJP-", "RJ45"]):
        return "connector"
    # Common non-standard connector prefixes (OLIMEX, etc.)
    if prefix in ("CON", "USB", "USBUART", "MICROSD", "UEXT", "LAN",
                   "HDMI", "EXT", "GPIO", "CAN", "SWD", "JTAG",
                   "ANT", "RJ", "SUPPLY"):
        return "connector"
    # KH-106: Catch MX/Cherry/Kailh keyboard switches before relay detection
    if "switch" in lib_lower or "button" in lib_lower:
        return "switch"
    if any(x in lib_lower for x in ("cherry_mx", "mx_switch", "kailh", "gateron")):
        return "switch"
    if any(x in val_lower for x in ("button", "tact", "push", "t1102", "t1107", "yts-a",
                                     "cherry_mx", "mx_switch", "kailh", "gateron")):
        return "switch"
    if "relay" in lib_lower:
        return "relay"
    if "nettie" in lib_lower or "net_tie" in val_lower or "nettie" in val_lower:
        return "net_tie"
    if "led" in lib_lower and "diode" in lib_lower:
        return "led"
    # IC detection from KiCad stdlib library prefixes (Analog_ADC, MCU_*, Regulator_*, etc.)
    _ic_lib_prefixes = ("analog_", "audio", "comparator", "converter_",
                        "driver_", "display_", "fpga_", "interface_",
                        "logic_", "mcu_", "memory_", "motor_",
                        "multiplexer", "power_management", "power_supervisor",
                        "regulator_", "sensor_", "timer", "rf_")
    if any(lib_prefix.startswith(p) for p in _ic_lib_prefixes):
        return "ic"
    if "transistor" in lib_lower or "mosfet" in lib_lower:
        return "transistor"
    if "diode" in lib_lower:
        return "diode"
    if "fuse" in lib_lower or "polyfuse" in lib_lower:
        return "fuse"
    if "ferritebead" in lib_lower or "ferrite_bead" in lib_lower:
        return "ferrite_bead"
    if "inductor" in lib_lower or "choke" in lib_lower:
        return "inductor"
    if "capacitor" in lib_lower:
        return "capacitor"
    if "resistor" in lib_lower:
        return "resistor"

    # --- Last resort: single-char prefix fallback ---
    # Only reached when lib_id/value didn't resolve the type.
    # Deliberately placed last so lib_id always takes priority.
    # KH-079: After single-char match, check lib_id/footprint/value for
    # contradicting evidence that overrides the single-char classification.
    if len(prefix) > 1:
        result = type_map.get(prefix[0])
        if result:
            fp_lower = footprint.lower() if footprint else ""
            if result == "transformer":
                if "tvs" in lib_lower or "tvs" in val_lower:
                    return "diode"
                if "test" in lib_lower or "tp" in fp_lower:
                    return "test_point"
                if any(x in lib_lower or x in val_lower or x in fp_lower
                       for x in ("mosfet", "fet", "transistor", "bjt",
                                 "q_npn", "q_pnp", "q_nmos", "q_pmos")):
                    return "transistor"
            if result == "fuse":
                if "fiducial" in lib_lower:
                    return "fiducial"
                if "filter" in lib_lower or "emi" in lib_lower:
                    return "filter"
                if "ferrite" in lib_lower or "bead" in lib_lower:
                    return "ferrite_bead"
            if result == "capacitor":
                if "shield" in lib_lower or "clip" in lib_lower:
                    return "mechanical"
            if result == "switch":
                if "standoff" in lib_lower or "smtso" in val_lower:
                    return "mounting_hole"
            if result == "varistor":
                if ("regulator" in lib_lower or "regulator" in fp_lower
                        or any(x in val_lower for x in ("ams1117", "lm78", "lm317",
                                                         "ld1117", "lm1117", "ap1117"))):
                    return "ic"
                if "pot" in lib_lower or "potentiometer" in lib_lower:
                    return "resistor"
            if result == "ic":
                if "bjt" in lib_lower or "transistor" in lib_lower:
                    return "transistor"
                if "transformer" in lib_lower:
                    return "transformer"
            return result

    return "other"


def is_power_net_name(net_name: str | None, power_rails: set[str] | None = None) -> bool:
    """Check if a net name looks like a power rail by naming convention.

    Covers both power-symbol-defined rails (via power_rails set) and nets that
    look like power from their name alone — including local/hierarchical labels
    like VDD_nRF, VBATT_MCU, V_BATT that lack an explicit power: symbol.
    """
    if not net_name:
        return False
    if power_rails and net_name in power_rails:
        return True
    # Strip hierarchical sheet path prefix (e.g., "/Power Supply/VCC" → "VCC")
    if "/" in net_name:
        net_name = net_name.rsplit("/", 1)[-1]
    nu = net_name.upper()
    # Explicit known names
    if nu in ("GND", "VSS", "AGND", "DGND", "PGND", "GNDPWR", "GNDA", "GNDD",
              "VCC", "VDD", "AVCC", "AVDD", "DVCC", "DVDD", "VBUS",
              "VAA", "VIO", "VMAIN", "VPWR", "VSYS", "VBAT", "VCORE",
              "VIN", "VOUT", "VREG", "VBATT",
              "V3P3", "V1P8", "V1P2", "V2P5", "V5P0", "V12P0",
              "VCCA", "VCCD", "VCCIO", "VDDA", "VDDD", "VDDIO"):
        return True
    # Pattern-based detection
    if nu.startswith("+") or nu.startswith("V+"):
        return True
    # Vnn, VnnV patterns (V3V3, V1V8, V5V0)
    if len(nu) >= 3 and nu[0] == "V" and nu[1].isdigit():
        return True
    # PWRnVn patterns (PWR3V3, PWR1V8, PWR5V0)
    if re.match(r'^PWR\d', nu):
        return True
    # VDD_xxx, VCC_xxx, VBAT_xxx, VBATT_xxx variants (local label power nets)
    # Split on _ and check if first segment is a known power prefix
    first_seg = nu.split("_")[0] if "_" in nu else ""
    if first_seg in ("VDD", "VCC", "AVDD", "AVCC", "DVDD", "DVCC", "VBAT",
                      "VBATT", "VSYS", "VBUS", "VMAIN", "VPWR", "VCORE",
                      "VDDIO", "VCCIO", "VIN", "VOUT", "VREG", "POW",
                      "PWR", "VMOT", "VHEAT"):
        return True
    return False


def is_ground_name(net_name: str | None) -> bool:
    """Check if a net name looks like a ground rail."""
    if not net_name:
        return False
    # Strip hierarchical sheet path prefix (e.g., "/Power Supply/GND" → "GND")
    if "/" in net_name:
        net_name = net_name.rsplit("/", 1)[-1]
    nu = net_name.upper()
    # Exact matches
    if nu in ("GND", "VSS", "AGND", "DGND", "PGND", "GNDPWR", "GNDA", "GNDD"):
        return True
    # Prefix/suffix patterns: GND_ISO, GND_SEC, GNDISO, etc.
    if nu.startswith("GND") or nu.endswith("GND"):
        return True
    # VSS variants
    if nu.startswith("VSS"):
        return True
    return False


def get_two_pin_nets(pin_net: dict, ref: str) -> tuple[str | None, str | None]:
    """Get the two nets a 2-pin component connects to.

    Takes pin_net map explicitly instead of closing over it.
    """
    n1, _ = pin_net.get((ref, "1"), (None, None))
    n2, _ = pin_net.get((ref, "2"), (None, None))
    return n1, n2


# ---------------------------------------------------------------------------
# Capacitor package extraction and ESR/ESL estimation
# ---------------------------------------------------------------------------

# Regex to extract package size from KiCad footprint strings
# Matches: C_0402_1005Metric, C_0805_2012Metric, CP_EIA-3216-18_Kemet-A, etc.
_CAP_PKG_RE = re.compile(r'C[P]?_(\d{4})_')
_CAP_PKG_EIA_RE = re.compile(r'EIA-(\d{4})')

# Typical MLCC ESR by package and capacitance range (X7R/X5R, 1kHz reference)
# Source: aggregate datasheet data from Murata, Samsung, TDK
# Format: (package, max_farads) → esr_ohm
# Checked in order — first match where farads <= max_farads wins
_CAP_ESR_TABLE = [
    # 0402 (1005 metric)
    ("0402", 1e-8,  5.0),    # ≤10nF
    ("0402", 1e-7,  1.0),    # ≤100nF
    ("0402", 1e-5,  0.5),    # ≤10µF
    # 0603 (1608 metric)
    ("0603", 1e-8,  2.0),
    ("0603", 1e-7,  0.5),
    ("0603", 1e-6,  0.15),
    ("0603", 1e-4,  0.1),
    # 0805 (2012 metric)
    ("0805", 1e-7,  0.3),
    ("0805", 1e-6,  0.08),
    ("0805", 1e-4,  0.03),
    # 1206 (3216 metric)
    ("1206", 1e-6,  0.1),
    ("1206", 1e-5,  0.03),
    ("1206", 1e-3,  0.01),
    # 1210 (3225 metric)
    ("1210", 1e-5,  0.02),
    ("1210", 1e-3,  0.008),
    # 2220 (5750 metric)
    ("2220", 1e-3,  0.005),
]

# Typical ESL by package (nH) — dominated by package geometry, not capacitance
_CAP_ESL = {
    "0402": 0.3,
    "0603": 0.5,
    "0805": 0.7,
    "1206": 1.0,
    "1210": 1.0,
    "1812": 1.2,
    "2220": 1.5,
}


def extract_cap_package(footprint):
    """Extract capacitor package size from KiCad footprint string.

    Examples:
        'Capacitor_SMD:C_0402_1005Metric' → '0402'
        'Capacitor_SMD:C_0805_2012Metric' → '0805'
        'Capacitor_SMD:CP_EIA-3216-18_Kemet-A' → '3216'
        'Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm' → None (THT, no standard package)
        '' → None

    Returns:
        Package designator string (e.g., '0402') or None
    """
    if not footprint:
        return None
    # Try standard "C_0402_..." pattern first
    m = _CAP_PKG_RE.search(footprint)
    if m:
        return m.group(1)
    # Try EIA pattern
    m = _CAP_PKG_EIA_RE.search(footprint)
    if m:
        # Convert EIA metric to imperial: 3216 → 1206, etc.
        eia = m.group(1)
        eia_to_imperial = {
            "1005": "0402", "1608": "0603", "2012": "0805",
            "3216": "1206", "3225": "1210", "4532": "1812",
            "5750": "2220",
        }
        return eia_to_imperial.get(eia, eia)
    return None


def estimate_cap_esr(farads, package):
    """Estimate ESR for an MLCC capacitor based on package and value.

    Very approximate — real ESR depends on manufacturer, voltage rating,
    dielectric type (X7R vs C0G), and measurement frequency. These are
    typical values at ~1kHz for X7R/X5R MLCCs.

    Args:
        farads: Capacitance in farads
        package: Package designator (e.g., '0402', '0805')

    Returns:
        Estimated ESR in ohms, or None if package not recognized
    """
    # EQ-067: ESR estimate from package size + capacitance (empirical)
    if not package or not farads or farads <= 0:
        return None
    pkg = package.upper()
    for tbl_pkg, max_f, esr in _CAP_ESR_TABLE:
        if pkg == tbl_pkg and farads <= max_f:
            return esr
    # No match — return a conservative default
    if farads < 1e-6:
        return 0.5
    elif farads < 1e-4:
        return 0.1
    else:
        return 0.05


def estimate_cap_esl(package):
    """Estimate parasitic inductance (ESL) for an MLCC.

    ESL is primarily driven by package geometry (current path length
    through the component), not by capacitance value.

    Args:
        package: Package designator (e.g., '0402', '0805')

    Returns:
        Estimated ESL in henries, or None if package not recognized
    """
    # EQ-066: ESL estimate from package size (empirical table)
    if not package:
        return None
    esl_nh = _CAP_ESL.get(package.upper())
    if esl_nh is None:
        return None
    return esl_nh * 1e-9  # Convert nH to H
