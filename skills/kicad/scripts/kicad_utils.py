"""
Shared utility functions for KiCad schematic and PCB analyzers.

Contains component classification, value parsing, net name classification,
and other helpers extracted from analyze_schematic.py.
"""

import re


# Coordinate matching tolerance (mm) — used across net building and connectivity analysis
COORD_EPSILON = 0.01

# Regulator Vref lookup table — maps part number prefixes to their internal
# reference voltage.  Used by the feedback divider Vout estimator instead of
# guessing from a list.  Entries are checked in order; first prefix match wins.
# When a part isn't found here the analyzer falls back to the heuristic sweep.
_REGULATOR_VREF: dict[str, float] = {
    # TI switching regulators (verified against datasheets)
    "TPS6100": 0.6,    "TPS6102": 0.6,    "TPS6103": 0.6,   # TPS61023 FB = 0.6V
    "TPS5430": 1.221,  "TPS5450": 1.221,                     # TPS5430 Vref = 1.221V
    "TPS54160": 0.8,   "TPS54260": 0.8,   "TPS54360": 0.8,   # TPS5436x FB = 0.8V
    "TPS542": 0.6,     "TPS543": 0.6,     "TPS544": 0.6,
    "TPS54040": 0.8,   "TPS54060": 0.8,                       # TPS54040 Vref = 0.8V
    "TPS5410": 1.221,
    "TPS56": 0.6,      "TPS55": 0.6,
    "TPS6208": 0.6,    "TPS6209": 0.6,
    "TPS6211": 0.6,    "TPS6212": 0.6,
    "TPS6213": 0.6,    "TPS6215": 0.6,
    "TPS6300": 0.5,    "TPS6301": 0.5,
    "TPS40": 0.6,
    "LMR514": 0.8,     "LMR516": 0.8,                         # LMR51450 Vref = 0.8V
    "LMR336": 1.0,     "LMR338": 1.0,                         # LMR33630 Vref = 1.0V
    "LM516": 0.8,      "LM258": 1.285,   "LM259": 1.285,
    "LM260": 1.21,     "LM261": 1.21,
    "LM340": 1.25,
    "LMZ3": 0.8,       "LMZ2": 0.795,
    "TLV620": 0.5,     "TLV621": 0.5,
    # TI LDOs
    "TLV759": 0.55,                                            # TLV759P (adjustable) FB = 0.55V
    "TPS7A": 1.19,     "TPS7B": 1.21,
    # Analog Devices / Linear Tech (verified against datasheets)
    "LT361": 0.8,      "LT362": 0.8,
    "LT364": 1.22,     "LT365": 1.22,
    "LT801": 0.8,      "LT802": 0.8,
    "LT810": 0.97,     "LT811": 0.97,                         # LT8610 VFB = 0.970V typ
    "LT860": 0.97,     "LT862": 0.97,                         # LT8640/LT8620 VFB = 0.970V typ
    "LT871": 1.0,      "LT872": 1.0,
    "LTC34": 0.8,
    "LTM46": 0.6,       "LTM82": 0.6,
    # Richtek
    "RT5": 0.6,         "RT6": 0.6,
    "RT2875": 0.8,
    # MPS
    "MP1": 0.8,         "MP2": 0.8,         "MP8": 0.8,
    # Microchip
    "MIC29": 1.24,      "MIC55": 1.24,
    "MCP170": 1.21,
    # Diodes Inc
    "AP6": 0.6,         "AP73": 0.6,
    "AP2112": 0.8,                                              # AP2112 adjustable Vref = 0.8V
    # ST
    "LD1117": 1.25,                                             # LD1117 Vref = 1.25V
    "LDL1117": 1.25,
    "LD33": 1.25,
    # ON Semi
    "NCP1": 0.8,        "NCV4": 0.8,
    # SY
    "SY8": 0.6,                                                # SY8089 FB = 0.6V typ
    # Maxim
    "MAX5035": 1.22,    "MAX5033": 1.22,                         # MAX5035 VFB = 1.221V typ (datasheet)
    "MAX1771": 1.5,     "MAX1709": 1.24,                         # MAX1771 Vref = 1.5V, MAX1709 VFB = 1.24V (datasheet)
    "MAX17760": 0.8,                                              # MAX17760 FB = 0.8V (datasheet, min Vout = 0.8V)
    # ISL (Renesas/Intersil)
    "ISL854": 0.6,      "ISL850": 0.6,                           # ISL854102 FB = 0.6V (datasheet)
    # LM6x4xx (TI)
    "LM614": 1.0,       "LM619": 1.0,                            # LM61495 VFB = 1.0V (datasheet, 0.99/1.0/1.01)
    # Diodes Inc (BCD Semiconductor)
    "AP3015": 1.23,                                               # AP3015A VFB = 1.23V (datasheet, 1.205/1.23/1.255)
    # Generic (well-established values)
    "LM317": 1.25,     "LM337": 1.25,
    "AMS1117": 1.25,   "AMS1085": 1.25,
    "LM78": 1.25,      "LM79": 1.25,
    "LM1117": 1.25,
    # NOTE: Parts without feedback dividers are intentionally excluded:
    # LT3080 (uses 10uA SET current source), LTC3649 (uses 50uA ISET),
    # TLV713 (fixed output only), XC6206 (fixed output only, no FB pin),
    # AP2210 (unverified Vref).
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


def parse_value(value_str: str) -> float | None:
    """Parse an engineering-notation component value to a float.

    Handles: 10K, 4.7u, 100n, 220p, 1M, 2.2m, 47R, 0R1, 4K7, 1R0, etc.
    Returns None if unparseable.
    """
    if not value_str:
        return None

    # Strip tolerance, voltage rating, package, and other suffixes
    # Common formats: "680K 1%", "220k/R0402", "22uF/6.3V/20%/X5R/C0603"
    s = value_str.strip().split("/")[0].split()[0]  # take part before first "/" or space
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
        return float(s)
    except ValueError:
        return None


def classify_component(ref: str, lib_id: str, value: str, is_power: bool = False) -> str:
    """Classify component type from reference designator and library."""
    if is_power or lib_id.startswith("power:"):
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
        "C": "capacitor", "L": "inductor",
        "D": "diode", "TVS": "diode", "V": "varistor",
        # Semiconductors
        "Q": "transistor", "FET": "transistor",
        "U": "ic", "IC": "ic",
        # Connectors and mechanical
        "J": "connector", "P": "connector",
        "SW": "switch", "S": "switch", "BUT": "switch",
        "K": "relay",
        "F": "fuse", "FUSE": "fuse",
        "Y": "crystal",
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

    result = type_map.get(prefix)
    if result:
        # Override prefix-based heuristics when lib_id provides better info
        val_low = value.lower() if value else ""
        lib_low = lib_id.lower() if lib_id else ""
        if result == "varistor" and ("r_pot" in lib_low or "potentiometer" in lib_low
                                     or "potentiometer" in val_low):
            return "resistor"
        if result == "transformer" and any(x in lib_low or x in val_low
                                           for x in ("mosfet", "fet", "transistor",
                                                     "amplifier", "rf_amp", "mmic")):
            return "ic"
        if result == "thermistor" and any(x in lib_low or x in val_low
                                          for x in ("fuse", "polyfuse", "pptc",
                                                    "reset fuse", "ptc fuse")):
            return "fuse"
        if result == "thermistor" and any(x in lib_low or x in val_low
                                          for x in ("mov", "varistor")):
            return "varistor"
        if result == "diode" and ("led" in lib_low or "led" in val_low):
            return "led"
        return result

    # Fallback: check value/lib_id for common patterns
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
    if any(x in lib_lower for x in ["test_point", "testpoint"]):
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
        if any(x in val_lower for x in ["xtal", "crystal", "mhz", "khz", "osc"]):
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
    if "switch" in lib_lower:
        return "switch"
    if "relay" in lib_lower:
        return "relay"
    if "nettie" in lib_lower or "net_tie" in val_lower or "nettie" in val_lower:
        return "net_tie"
    if "led" in lib_lower and "diode" in lib_lower:
        return "led"
    if "transistor" in lib_lower or "mosfet" in lib_lower:
        return "transistor"
    if "diode" in lib_lower:
        return "diode"
    if "fuse" in lib_lower or "polyfuse" in lib_lower:
        return "fuse"
    if "inductor" in lib_lower or "choke" in lib_lower:
        return "inductor"
    if "capacitor" in lib_lower:
        return "capacitor"
    if "resistor" in lib_lower:
        return "resistor"

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
