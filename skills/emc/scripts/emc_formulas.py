"""EMC analytical formulas — radiation, harmonics, cavity resonance, impedance.

All functions are pure math with zero dependencies beyond Python stdlib.
Units are SI unless noted otherwise in the docstring.

References:
  - Henry Ott, "Electromagnetic Compatibility Engineering"
  - Clayton Paul, "Introduction to Electromagnetic Compatibility"
  - Eric Bogatin, "Signal and Power Integrity — Simplified"
  - LearnEMC.com calculators
"""

import math
from typing import List, Tuple, Optional, Dict

# Physical constants
C_0 = 2.998e8          # Speed of light in vacuum (m/s)
MU_0 = 4 * math.pi * 1e-7  # Permeability of free space (H/m)
ETA_0 = 377.0          # Impedance of free space (ohms)
EPSILON_0 = 8.854e-12  # Permittivity of free space (F/m)


# ---------------------------------------------------------------------------
# Emission limit tables
# ---------------------------------------------------------------------------

# FCC Part 15 radiated limits: (f_min_MHz, f_max_MHz, limit_dBuV_m, distance_m)
FCC_CLASS_B_RADIATED = [
    (30, 88, 40.0, 3),
    (88, 216, 43.5, 3),
    (216, 960, 46.0, 3),
    (960, 40000, 54.0, 3),
]

FCC_CLASS_A_RADIATED = [
    (30, 88, 39.1, 10),
    (88, 216, 43.5, 10),
    (216, 960, 46.4, 10),
    (960, 40000, 49.5, 10),
]

CISPR_CLASS_B_RADIATED = [
    (30, 230, 30.0, 10),
    (230, 1000, 37.0, 10),
]

CISPR_CLASS_A_RADIATED = [
    (30, 230, 40.0, 10),
    (230, 1000, 47.0, 10),
]

# CISPR 25 Class 5 approximate limits (automotive)
CISPR_25_CLASS5_RADIATED = [
    (30, 68, 34.0, 1),
    (68, 108, 28.0, 1),
    (108, 230, 26.0, 1),
    (230, 1000, 32.0, 1),
]

# MIL-STD-461G RE102 approximate narrowband limits
MIL_STD_461_RE102 = [
    (2, 30, 24.0, 1),
    (30, 1000, 24.0, 1),
    (1000, 18000, 24.0, 1),
]

STANDARDS = {
    'fcc-class-b': FCC_CLASS_B_RADIATED,
    'fcc-class-a': FCC_CLASS_A_RADIATED,
    'cispr-class-b': CISPR_CLASS_B_RADIATED,
    'cispr-class-a': CISPR_CLASS_A_RADIATED,
    'cispr-25': CISPR_25_CLASS5_RADIATED,
    'mil-std-461': MIL_STD_461_RE102,
}


def get_emission_limit(freq_hz: float, standard: str = 'fcc-class-b') -> Optional[Tuple[float, float]]:
    """Get the emission limit for a frequency under a given standard.

    Returns (limit_dBuV_m, measurement_distance_m) or None if frequency
    is outside the standard's range.
    """
    freq_mhz = freq_hz / 1e6
    table = STANDARDS.get(standard, FCC_CLASS_B_RADIATED)
    for f_min, f_max, limit, dist in table:
        if f_min <= freq_mhz < f_max:
            return (limit, dist)
    return None


# ---------------------------------------------------------------------------
# Differential-mode (loop) radiation
# ---------------------------------------------------------------------------

def dm_radiation_v_m(freq_hz: float, area_m2: float, current_a: float,
                     distance_m: float, ground_plane: bool = True) -> float:
    """Maximum far-field E from an electrically small current loop.

    E = K × f² × A × I / r

    where K = 1.316e-14 (free space) or 2.632e-14 (with ground plane image).

    Args:
        freq_hz: Frequency in Hz.
        area_m2: Loop area in m².
        current_a: Peak current in Amps.
        distance_m: Measurement distance in meters.
        ground_plane: If True, include ×2 ground plane image factor.

    Returns:
        Electric field in V/m.
    """
    k = 2.632e-14 if ground_plane else 1.316e-14
    return k * freq_hz**2 * area_m2 * current_a / distance_m


def dm_radiation_dbuv_m(freq_hz: float, area_m2: float, current_a: float,
                        distance_m: float, ground_plane: bool = True) -> float:
    """Same as dm_radiation_v_m but returns dBµV/m."""
    e = dm_radiation_v_m(freq_hz, area_m2, current_a, distance_m, ground_plane)
    if e <= 0:
        return -999.0
    return 20 * math.log10(e * 1e6)


def dm_max_loop_area_m2(freq_hz: float, current_a: float, limit_dbuv_m: float,
                        distance_m: float, margin_db: float = 6.0,
                        ground_plane: bool = True) -> float:
    """Maximum allowable loop area to stay under an emission limit.

    Solves E = K × f² × A × I / r for A, with margin.

    Args:
        margin_db: Design margin in dB (default 6 dB).

    Returns:
        Maximum loop area in m².
    """
    k = 2.632e-14 if ground_plane else 1.316e-14
    e_limit = 10**((limit_dbuv_m - margin_db) / 20) * 1e-6  # V/m
    if freq_hz <= 0 or current_a <= 0:
        return float('inf')
    return e_limit * distance_m / (k * freq_hz**2 * current_a)


# ---------------------------------------------------------------------------
# Common-mode (cable) radiation
# ---------------------------------------------------------------------------

def cm_radiation_v_m(freq_hz: float, cable_length_m: float,
                     cm_current_a: float, distance_m: float) -> float:
    """E-field from common-mode current on a cable (monopole over ground).

    E = µ₀ × f × L × I_CM / r = 1.257e-6 × f × L × I_CM / r

    The coefficient 1.257e-6 = µ₀ = 4π×10⁻⁷ includes the ×2 ground plane
    image factor (base short-dipole coefficient is 6.283e-7).

    Valid when cable length < λ/4 at the frequency of interest.

    Ref: Ott, "EMC Engineering" (Wiley, 2009), Chapter 6.
         Paul, "Introduction to EMC" (Wiley, 2006), Chapter 10.
    """
    return 1.257e-6 * freq_hz * cable_length_m * cm_current_a / distance_m


def cm_radiation_dbuv_m(freq_hz: float, cable_length_m: float,
                        cm_current_a: float, distance_m: float) -> float:
    """Same as cm_radiation_v_m but returns dBµV/m."""
    e = cm_radiation_v_m(freq_hz, cable_length_m, cm_current_a, distance_m)
    if e <= 0:
        return -999.0
    return 20 * math.log10(e * 1e6)


def cm_max_current_a(freq_hz: float, cable_length_m: float,
                     limit_dbuv_m: float, distance_m: float,
                     margin_db: float = 6.0) -> float:
    """Maximum allowable CM current to stay under an emission limit.

    Returns current in Amps.
    """
    e_limit = 10**((limit_dbuv_m - margin_db) / 20) * 1e-6  # V/m
    denom = 1.257e-6 * freq_hz * cable_length_m
    if denom <= 0:
        return float('inf')
    return e_limit * distance_m / denom


# ---------------------------------------------------------------------------
# Switching regulator harmonics
# ---------------------------------------------------------------------------

def trapezoidal_harmonic_amplitude(n: int, v_peak: float, duty_cycle: float,
                                   rise_time_s: float,
                                   switching_freq_hz: float) -> float:
    """Amplitude of the nth harmonic of a trapezoidal switching waveform.

    Uses the standard trapezoidal Fourier envelope with sinc rolloffs.

    Args:
        n: Harmonic number (1 = fundamental).
        v_peak: Peak voltage of the switching node.
        duty_cycle: Duty cycle (0 to 1).
        rise_time_s: 10-90% rise time in seconds.
        switching_freq_hz: Switching frequency in Hz.

    Returns:
        Amplitude of the nth harmonic in Volts.
    """
    if n <= 0:
        return 0.0
    t_period = 1.0 / switching_freq_hz
    tau = duty_cycle * t_period  # pulse width

    # Base amplitude
    a0 = 2 * v_peak * tau / t_period

    # First sinc rolloff (pulse width)
    x1 = n * math.pi * tau / t_period
    sinc1 = math.sin(x1) / x1 if abs(x1) > 1e-10 else 1.0

    # Second sinc rolloff (rise time)
    x2 = n * math.pi * rise_time_s / t_period
    sinc2 = math.sin(x2) / x2 if abs(x2) > 1e-10 else 1.0

    return abs(a0 * sinc1 * sinc2)


def trapezoidal_corner_frequencies(duty_cycle: float, rise_time_s: float,
                                   switching_freq_hz: float) -> Tuple[float, float]:
    """Corner frequencies of the trapezoidal harmonic envelope.

    Returns:
        (f1, f2) where:
          f1 = 1/(π × τ) — onset of -20 dB/decade rolloff
          f2 = 1/(π × t_r) — onset of -40 dB/decade rolloff
    """
    t_period = 1.0 / switching_freq_hz
    tau = duty_cycle * t_period
    f1 = 1.0 / (math.pi * tau) if tau > 0 else float('inf')
    f2 = 1.0 / (math.pi * rise_time_s) if rise_time_s > 0 else float('inf')
    return (f1, f2)


def switching_harmonics_in_band(switching_freq_hz: float, band_min_hz: float,
                                band_max_hz: float) -> List[int]:
    """List harmonic numbers that fall within a frequency band.

    Args:
        switching_freq_hz: Fundamental switching frequency.
        band_min_hz: Lower edge of test band.
        band_max_hz: Upper edge of test band.

    Returns:
        List of harmonic numbers within the band.
    """
    if switching_freq_hz <= 0:
        return []
    n_min = max(1, int(math.ceil(band_min_hz / switching_freq_hz)))
    n_max = int(math.floor(band_max_hz / switching_freq_hz))
    return list(range(n_min, n_max + 1))


def harmonic_spectrum(switching_freq_hz: float, v_peak: float,
                      duty_cycle: float, rise_time_s: float,
                      max_freq_hz: float = 1e9) -> List[Dict]:
    """Generate the full harmonic spectrum up to max_freq_hz.

    Returns:
        List of dicts with keys: harmonic, freq_hz, amplitude_v, amplitude_dbuv.
    """
    results = []
    n = 1
    while True:
        f = n * switching_freq_hz
        if f > max_freq_hz:
            break
        amp = trapezoidal_harmonic_amplitude(n, v_peak, duty_cycle,
                                            rise_time_s, switching_freq_hz)
        amp_dbuv = 20 * math.log10(amp * 1e6) if amp > 0 else -999.0
        results.append({
            'harmonic': n,
            'freq_hz': f,
            'amplitude_v': amp,
            'amplitude_dbuv': amp_dbuv,
        })
        n += 1
    return results


# ---------------------------------------------------------------------------
# Board cavity resonance
# ---------------------------------------------------------------------------

def cavity_resonance_hz(length_m: float, width_m: float,
                        epsilon_r: float, m: int, n: int) -> float:
    """Resonant frequency of a PCB parallel-plate cavity.

    f_mn = (c / (2√εr)) × √((m/L)² + (n/W)²)

    Args:
        length_m: Board length in meters.
        width_m: Board width in meters.
        epsilon_r: Relative permittivity of dielectric.
        m, n: Mode indices (integers, not both zero).

    Returns:
        Resonant frequency in Hz.
    """
    if m == 0 and n == 0:
        return 0.0
    v = C_0 / math.sqrt(epsilon_r)
    return (v / 2) * math.sqrt((m / length_m)**2 + (n / width_m)**2)


def board_cavity_resonances(length_m: float, width_m: float,
                            epsilon_r: float = 4.4,
                            max_freq_hz: float = 3e9,
                            max_modes: int = 10) -> List[Dict]:
    """Calculate all cavity resonance modes below max_freq_hz.

    Returns:
        List of dicts with keys: mode (m,n), freq_hz, freq_mhz.
        Sorted by frequency.
    """
    results = []
    for m in range(0, max_modes + 1):
        for n in range(0, max_modes + 1):
            if m == 0 and n == 0:
                continue
            f = cavity_resonance_hz(length_m, width_m, epsilon_r, m, n)
            if f <= max_freq_hz:
                results.append({
                    'mode': (m, n),
                    'freq_hz': f,
                    'freq_mhz': f / 1e6,
                })
    results.sort(key=lambda x: x['freq_hz'])
    return results


# ---------------------------------------------------------------------------
# Signal bandwidth and wavelength
# ---------------------------------------------------------------------------

def bandwidth_from_rise_time(rise_time_s: float) -> float:
    """3 dB bandwidth from 10-90% rise time.

    BW = 0.35 / t_r

    Args:
        rise_time_s: 10-90% rise time in seconds.

    Returns:
        Bandwidth in Hz.
    """
    if rise_time_s <= 0:
        return float('inf')
    return 0.35 / rise_time_s


def knee_frequency(rise_time_s: float) -> float:
    """EMC knee frequency — significant spectral content up to this point.

    f_knee = 0.5 / t_r

    More conservative than the 3 dB bandwidth.
    """
    if rise_time_s <= 0:
        return float('inf')
    return 0.5 / rise_time_s


def wavelength_in_pcb(freq_hz: float, epsilon_r: float = 4.4) -> float:
    """Wavelength in PCB dielectric at a given frequency.

    λ = c / (f × √εr)

    Returns:
        Wavelength in meters.
    """
    if freq_hz <= 0:
        return float('inf')
    return C_0 / (freq_hz * math.sqrt(epsilon_r))


def lambda_over_20(freq_hz: float, epsilon_r: float = 4.4) -> float:
    """λ/20 spacing rule for via stitching.

    Returns:
        Maximum via stitching spacing in meters.
    """
    return wavelength_in_pcb(freq_hz, epsilon_r) / 20.0


# ---------------------------------------------------------------------------
# Trace inductance and capacitance
# ---------------------------------------------------------------------------

def trace_inductance_nh_per_mm(width_mm: float, height_mm: float) -> float:
    """Loop inductance per mm for a microstrip trace over a ground plane.

    Computes Z0 via the Wheeler formula, then derives L from Z0/v_phase.
    Typical values: 0.3-0.8 nH/mm for microstrip over solid ground.
    The common "~1 nH/mm" rule of thumb is an upper bound for thin traces
    with distant return paths.

    Ref: Bogatin Rule of Thumb #6 — 50Ω FR4 microstrip ≈ 8.3 nH/inch
         (≈ 0.33 nH/mm). Wider or narrower traces scale with Z0.

    Args:
        width_mm: Trace width in mm.
        height_mm: Dielectric height to reference plane in mm.

    Returns:
        Inductance in nH per mm of trace length.
    """
    if width_mm <= 0 or height_mm <= 0:
        return 0.7  # fallback: mid-range for typical microstrip
    # Simplified Wheeler formula for microstrip inductance
    w_h = width_mm / height_mm
    if w_h >= 1:
        z0 = (120 * math.pi) / (w_h + 1.393 + 0.667 * math.log(w_h + 1.444))
    else:
        z0 = (60 * math.log(8 * height_mm / width_mm + width_mm / (4 * height_mm)))
    # L per unit length = Z0 / v_phase
    # For FR4 effective epsilon ~3.3 for typical microstrip
    v_phase = C_0 / math.sqrt(3.3)
    l_per_m = z0 / v_phase  # H/m
    return l_per_m * 1e6  # nH/mm


def via_inductance_nh(length_mm: float, drill_mm: float) -> float:
    """Approximate inductance of a single via.

    L = (µ₀ / 2π) × h × [ln(4h/d) + 1]

    In practical units with h and d in mm:
      L ≈ 0.2 × h × (ln(4h/d) + 1) nH

    Equivalent to the Goldfarb/Grover formula L = 5.08 × h × (ln(4h/d) + 1) nH
    when h and d are in inches (0.2 × 25.4 = 5.08).

    Ref: Goldfarb & Pucha, IEEE Microwave and Guided Wave Letters, Vol 1 No 6, 1991.
    Also: Howard Johnson, "High-Speed Signal Propagation" (2003).
    """
    if length_mm <= 0 or drill_mm <= 0:
        return 0.5  # typical fallback
    h = length_mm
    d = drill_mm
    return 0.2 * h * (math.log(4 * h / d) + 1)  # nH


# ---------------------------------------------------------------------------
# Interplane capacitance
# ---------------------------------------------------------------------------

def interplane_capacitance_pf_per_cm2(dielectric_thickness_mm: float,
                                      epsilon_r: float = 4.4) -> float:
    """Capacitance per cm² between two parallel copper planes.

    C = ε₀ × εr / d

    Args:
        dielectric_thickness_mm: Spacing between planes in mm.
        epsilon_r: Relative permittivity.

    Returns:
        Capacitance in pF per cm².
    """
    if dielectric_thickness_mm <= 0:
        return 0.0
    d_m = dielectric_thickness_mm / 1000.0
    c_per_m2 = EPSILON_0 * epsilon_r / d_m  # F/m²
    return c_per_m2 * 1e-4 * 1e12  # pF/cm²


# ---------------------------------------------------------------------------
# Decoupling effectiveness
# ---------------------------------------------------------------------------

def cap_self_resonant_freq(capacitance_f: float, esl_h: float) -> float:
    """Self-resonant frequency of a capacitor.

    f_SRF = 1 / (2π√(LC))

    Args:
        capacitance_f: Capacitance in Farads.
        esl_h: Equivalent series inductance in Henries.

    Returns:
        Self-resonant frequency in Hz.
    """
    if capacitance_f <= 0 or esl_h <= 0:
        return float('inf')
    return 1.0 / (2 * math.pi * math.sqrt(esl_h * capacitance_f))


def cap_impedance_at_freq(freq_hz: float, capacitance_f: float,
                          esr_ohm: float, esl_h: float) -> float:
    """Impedance magnitude of a capacitor (series RLC model) at frequency.

    |Z| = √(ESR² + (2πfL - 1/(2πfC))²)
    """
    if freq_hz <= 0:
        return float('inf')
    omega = 2 * math.pi * freq_hz
    x_l = omega * esl_h
    x_c = 1.0 / (omega * capacitance_f) if capacitance_f > 0 else float('inf')
    x_net = x_l - x_c
    return math.sqrt(esr_ohm**2 + x_net**2)


# ---------------------------------------------------------------------------
# ESR/ESL lookup by package size (typical MLCC values)
# ---------------------------------------------------------------------------

# Typical ESL values for MLCC by package (Henries)
MLCC_ESL = {
    '0201': 0.3e-9,
    '0402': 0.5e-9,
    '0603': 0.7e-9,
    '0805': 0.9e-9,
    '1206': 1.1e-9,
    '1210': 1.2e-9,
    '1812': 1.5e-9,
    '2220': 1.8e-9,
}

# Typical ESR values for MLCC by package (Ohms) — X5R/X7R at 1 MHz
MLCC_ESR = {
    '0201': 0.5,
    '0402': 0.3,
    '0603': 0.1,
    '0805': 0.05,
    '1206': 0.03,
    '1210': 0.02,
    '1812': 0.02,
    '2220': 0.015,
}


def pdn_target_impedance(v_rail: float, ripple_pct: float = 5.0,
                         i_transient_a: float = 0.5) -> float:
    """Target impedance for a power distribution network.

    Z_target = V_rail × ripple% / (0.5 × I_transient)

    The factor 0.5 accounts for the worst-case scenario where the
    transient current is split equally between supply and return.

    Ref: Bogatin, "Signal and Power Integrity — Simplified", Ch. 10.

    Args:
        v_rail: Rail voltage in Volts.
        ripple_pct: Allowable voltage ripple as percentage (default 5%).
        i_transient_a: Transient current demand in Amps.

    Returns:
        Target impedance in Ohms.
    """
    if i_transient_a <= 0:
        return float('inf')
    return v_rail * (ripple_pct / 100) / (0.5 * i_transient_a)


def parallel_cap_impedance(freq_hz: float,
                           caps: List[Dict]) -> float:
    """Impedance of multiple capacitors in parallel at a given frequency.

    Each cap dict should have: farads (float), esr_ohm (float), esl_h (float).
    Missing fields use defaults from package lookup.

    The parallel impedance: 1/Z_total = sum(1/Z_i)
    """
    if freq_hz <= 0 or not caps:
        return float('inf')

    sum_admittance = 0.0
    for cap in caps:
        c = cap.get('farads', 0)
        if c <= 0:
            continue
        esr = cap.get('esr_ohm', 0.1)
        esl = cap.get('esl_h', 1e-9)
        z = cap_impedance_at_freq(freq_hz, c, esr, esl)
        if z > 0:
            sum_admittance += 1.0 / z

    if sum_admittance <= 0:
        return float('inf')
    return 1.0 / sum_admittance


def pdn_impedance_sweep(caps: List[Dict],
                        plane_cap_f: float = 0,
                        freq_start: float = 1e3,
                        freq_stop: float = 1e9,
                        points_per_decade: int = 50) -> List[Dict]:
    """Sweep frequency and compute PDN impedance at each point.

    Args:
        caps: List of capacitor dicts with farads, esr_ohm, esl_h.
        plane_cap_f: Interplane capacitance in Farads (parallel with caps).
        freq_start: Start frequency in Hz.
        freq_stop: Stop frequency in Hz.
        points_per_decade: Resolution.

    Returns:
        List of {freq_hz, impedance_ohm} dicts.
    """
    if not caps and plane_cap_f <= 0:
        return []

    all_caps = list(caps)
    if plane_cap_f > 0:
        # Model interplane capacitance as a low-ESR, low-ESL cap
        all_caps.append({
            'farads': plane_cap_f,
            'esr_ohm': 0.001,  # Very low ESR for plane cap
            'esl_h': 0.05e-9,  # Very low ESL
        })

    results = []
    decades = math.log10(freq_stop / freq_start)
    n_points = max(10, int(decades * points_per_decade))

    for i in range(n_points + 1):
        f = freq_start * (10 ** (i * decades / n_points))
        z = parallel_cap_impedance(f, all_caps)
        results.append({'freq_hz': f, 'impedance_ohm': z})

    return results


def find_anti_resonances(sweep: List[Dict],
                         z_target: float = None) -> List[Dict]:
    """Find anti-resonance peaks (local maxima) in a PDN impedance sweep.

    Args:
        sweep: Output from pdn_impedance_sweep().
        z_target: Target impedance. If provided, only peaks exceeding it
                  are returned.

    Returns:
        List of {freq_hz, impedance_ohm, exceeds_target} dicts for each peak.
    """
    peaks = []
    if len(sweep) < 3:
        return peaks

    for i in range(1, len(sweep) - 1):
        z_prev = sweep[i - 1]['impedance_ohm']
        z_curr = sweep[i]['impedance_ohm']
        z_next = sweep[i + 1]['impedance_ohm']

        if z_curr > z_prev and z_curr > z_next:
            exceeds = z_curr > z_target if z_target else False
            if z_target is None or exceeds:
                peaks.append({
                    'freq_hz': sweep[i]['freq_hz'],
                    'freq_mhz': sweep[i]['freq_hz'] / 1e6,
                    'impedance_ohm': z_curr,
                    'exceeds_target': exceeds,
                })

    return peaks


def estimate_esl(package: str) -> float:
    """Estimate ESL for an MLCC package. Returns Henries."""
    return MLCC_ESL.get(package, 1.0e-9)


def estimate_esr(package: str) -> float:
    """Estimate ESR for an MLCC package. Returns Ohms."""
    return MLCC_ESR.get(package, 0.1)


# ---------------------------------------------------------------------------
# Differential pair formulas
# ---------------------------------------------------------------------------

# Protocol-specific differential pair parameters.
# max_skew_ps: maximum allowed intra-pair skew (picoseconds)
# v_diff: differential signal amplitude (Volts, peak)
# rise_time_ns: typical 10-90% rise time (nanoseconds)
# z_ohm: differential impedance target (Ohms)
#
# Sources:
#   USB: USB 2.0 spec §7.1.2, USB 3.x spec §6.8
#   Ethernet: IEEE 802.3 §40.6.1
#   HDMI: HDMI 2.1 spec §4.2.1.1
#   LVDS: TIA/EIA-644 §4.2
#   PCIe: PCI Express Base Spec §4.3.3
#   CAN: ISO 11898-2
DIFF_PAIR_PROTOCOLS = {
    'USB':      {'max_skew_ps': 25,  'v_diff': 0.4,  'rise_time_ns': 0.5,  'z_ohm': 90},
    'USB3':     {'max_skew_ps': 5,   'v_diff': 0.4,  'rise_time_ns': 0.1,  'z_ohm': 90},
    'Ethernet': {'max_skew_ps': 50,  'v_diff': 1.0,  'rise_time_ns': 4.0,  'z_ohm': 100},
    'HDMI':     {'max_skew_ps': 20,  'v_diff': 0.4,  'rise_time_ns': 0.2,  'z_ohm': 100},
    'LVDS':     {'max_skew_ps': 25,  'v_diff': 0.35, 'rise_time_ns': 0.5,  'z_ohm': 100},
    'PCIe':     {'max_skew_ps': 5,   'v_diff': 0.4,  'rise_time_ns': 0.1,  'z_ohm': 85},
    'SATA':     {'max_skew_ps': 10,  'v_diff': 0.4,  'rise_time_ns': 0.1,  'z_ohm': 100},
    'MIPI':     {'max_skew_ps': 15,  'v_diff': 0.2,  'rise_time_ns': 0.2,  'z_ohm': 100},
    'CAN':      {'max_skew_ps': 500, 'v_diff': 2.0,  'rise_time_ns': 50,   'z_ohm': 120},
    'RS-485':   {'max_skew_ps': 200, 'v_diff': 2.0,  'rise_time_ns': 10,   'z_ohm': 120},
}


def propagation_delay_ps_per_mm(epsilon_r: float = 4.4) -> float:
    """Propagation delay for microstrip in ps/mm.

    Uses effective dielectric constant for microstrip: εeff ≈ (εr + 1) / 2.
    Delay = 1 / v_phase = √εeff / c.

    For FR4 (εr=4.4): εeff ≈ 2.7, delay ≈ 5.48 ps/mm.
    For stripline (fully embedded): εeff = εr, delay ≈ 6.99 ps/mm.
    """
    eps_eff = (epsilon_r + 1) / 2  # microstrip approximation
    v_phase = C_0 / math.sqrt(eps_eff)  # m/s
    return 1e12 / (v_phase * 1e3)  # ps/mm


def diff_pair_skew_ps(length_diff_mm: float, epsilon_r: float = 4.4) -> float:
    """Time skew from differential pair length mismatch.

    Args:
        length_diff_mm: Absolute length difference in mm.
        epsilon_r: Board dielectric constant.

    Returns:
        Time skew in picoseconds.
    """
    return abs(length_diff_mm) * propagation_delay_ps_per_mm(epsilon_r)


def diff_pair_cm_voltage(v_diff: float, skew_ps: float,
                         rise_time_ps: float) -> float:
    """Common-mode voltage generated by differential pair skew.

    V_CM = V_diff × Δt / (2 × T_rise)

    Ref: Ott, "EMC Engineering" Ch. 19; Johnson, "High-Speed Signal
    Propagation" Ch. 11.

    Args:
        v_diff: Differential signal amplitude in Volts.
        skew_ps: Intra-pair time skew in picoseconds.
        rise_time_ps: Signal rise time (10-90%) in picoseconds.

    Returns:
        Common-mode voltage in Volts.
    """
    if rise_time_ps <= 0:
        return 0.0
    return v_diff * skew_ps / (2 * rise_time_ps)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def point_to_segment_distance(px: float, py: float,
                              x1: float, y1: float,
                              x2: float, y2: float) -> float:
    """Minimum distance from point (px, py) to line segment (x1,y1)-(x2,y2).

    Returns the perpendicular distance if the projection falls on the
    segment, otherwise the distance to the nearest endpoint.
    """
    dx = x2 - x1
    dy = y2 - y1
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        # Degenerate segment (zero length)
        return math.sqrt((px - x1)**2 + (py - y1)**2)
    # Parameter t of the projection onto the line
    t = ((px - x1) * dx + (py - y1) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    # Closest point on segment
    cx = x1 + t * dx
    cy = y1 + t * dy
    return math.sqrt((px - cx)**2 + (py - cy)**2)


# ---------------------------------------------------------------------------
# Market-to-standards mapping for regulatory gap analysis
# ---------------------------------------------------------------------------

MARKET_STANDARDS = {
    'us': [
        {'name': 'FCC Part 15 Class B', 'type': 'radiated', 'standard_key': 'fcc-class-b'},
        {'name': 'FCC Part 15 Class B', 'type': 'conducted', 'standard_key': 'fcc-class-b'},
    ],
    'eu': [
        {'name': 'EN 55032 Class B (CISPR 32)', 'type': 'radiated', 'standard_key': 'cispr-class-b'},
        {'name': 'EN 55032 Class B (CISPR 32)', 'type': 'conducted', 'standard_key': 'cispr-class-b'},
        {'name': 'IEC 61000-4-2', 'type': 'esd', 'standard_key': None},
        {'name': 'IEC 61000-4-4', 'type': 'eft', 'standard_key': None},
        {'name': 'IEC 61000-4-5', 'type': 'surge', 'standard_key': None},
    ],
    'automotive': [
        {'name': 'CISPR 25 Class 5', 'type': 'radiated', 'standard_key': 'cispr-25'},
        {'name': 'CISPR 25 Class 5', 'type': 'conducted', 'standard_key': 'cispr-25'},
        {'name': 'ISO 10605', 'type': 'esd', 'standard_key': None},
        {'name': 'ISO 7637-2', 'type': 'transient', 'standard_key': None},
    ],
    'medical': [
        {'name': 'EN 55032 + EN 60601-1-2', 'type': 'radiated', 'standard_key': 'cispr-class-b'},
        {'name': 'EN 55032 + EN 60601-1-2', 'type': 'conducted', 'standard_key': 'cispr-class-b'},
        {'name': 'IEC 61000-4-2 Level 4', 'type': 'esd', 'standard_key': None},
        {'name': 'IEC 61000-4-3 (10 V/m)', 'type': 'radiated_immunity', 'standard_key': None},
        {'name': 'IEC 61000-4-5 Level 4', 'type': 'surge', 'standard_key': None},
    ],
    'military': [
        {'name': 'MIL-STD-461G RE102', 'type': 'radiated', 'standard_key': 'mil-std-461'},
        {'name': 'MIL-STD-461G CE102', 'type': 'conducted', 'standard_key': 'mil-std-461'},
        {'name': 'MIL-STD-461G CS118', 'type': 'esd', 'standard_key': None},
        {'name': 'MIL-STD-461G CS114/CS116', 'type': 'conducted_susceptibility', 'standard_key': None},
    ],
}
