# EMC Analysis Methodology

How the `analyze_emc.py` script works — data sources, check categories, scoring model, and limitations.

## Architecture

```
analyze_schematic.py ──→ schematic.json ──┐
                                          ├──→ analyze_emc.py ──→ emc.json
analyze_pcb.py ────────→ pcb.json ────────┘
```

The EMC analyzer is a **consumer** of the kicad skill's analysis output. It does not parse KiCad files directly. It cross-references schematic data (component types, switching frequencies, protection devices, bus topology) against PCB data (trace routing, zone coverage, component placement, via stitching, stackup) to identify EMC risks.

## Data Flow

### From schematic JSON
- `signal_analysis.power_regulators` → switching frequencies, topologies
- `signal_analysis.crystal_circuits` → clock frequencies
- `signal_analysis.protection_devices` → ESD/TVS inventory
- `design_analysis.buses` → bus topology (I2C, SPI, UART, CAN)

### From PCB JSON
- `footprints` → component positions, reference designators, pad-to-net mapping
- `zones` → ground plane zones, fill ratios, island counts
- `tracks` (with --full) → per-track coordinates for return path analysis
- `return_path_continuity` (with --full) → per-net reference plane coverage
- `decoupling_placement` → IC-to-cap distances
- `net_lengths` → per-net routing length and layer distribution
- `setup.stackup` → layer ordering, dielectric thickness, εr
- `layers` → layer types (signal/power)
- `statistics` → board dimensions, via count, layer count
- `ground_domains` → ground domain detection
- `vias` → via count and distribution

## Check Categories

### Category 1: Ground Plane Integrity (rules GP-xxx)
The highest-value checks. A signal crossing a ground plane void has a near-100% correlation with EMC failure. Uses `return_path_continuity` data for per-net coverage, and `zones` data for overall plane quality.

### Category 2: Decoupling Effectiveness (rules DC-xxx)
Uses `decoupling_placement` data to check cap-to-IC distances and identify ICs without nearby decoupling.

### Category 3: I/O Interface Filtering (rules IO-xxx)
Cross-references connector positions (from `footprints`) against filter/protection component positions. Schematic `protection_devices` data supplements the geometric check.

### Category 4: Switching Regulator EMC (rules SW-xxx)
Uses schematic `power_regulators` data to identify switching converters, estimates switching frequency from part number lookup, then calculates harmonic overlap with FCC/CISPR test bands using the trapezoidal spectrum model.

### Category 5: Clock Routing Quality (rules CK-xxx)
Uses `net_lengths` layer distribution to check if clocks are on outer vs inner layers. Schematic `crystal_circuits` and `buses` data identify which nets are clock signals.

### Category 6: Via Stitching (rules VS-xxx)
Estimates average via spacing from total via count and board area. Compares against λ/20 requirement at the highest detected frequency.

### Category 7: Stackup Quality (rules SU-xxx)
Analyzes `setup.stackup` for adjacent signal layers, signal-to-reference plane spacing, and power/ground plane pair interplane capacitance.

### Category 8: Emission Estimates (rules EE-xxx)
Informational calculations: board cavity resonance frequencies and switching regulator harmonic envelopes. These are order-of-magnitude estimates (±10-20 dB), not pass/fail predictions.

## Risk Scoring

```
score = 100 - (CRITICAL × 15) - (HIGH × 8) - (MEDIUM × 3) - (LOW × 1)
```

Clamped to [0, 100]. Interpretation:

| Score | Assessment |
|-------|-----------|
| 90-100 | Low EMC risk — basic hygiene checks pass |
| 70-89  | Moderate risk — some issues to address |
| 50-69  | Significant risk — multiple issues likely to cause failures |
| <50    | High risk — fundamental design issues need resolution |

## Severity Assignment

- **CRITICAL:** Near-certain EMC failure. Signal crossing ground plane gap, no ground plane on multi-layer board.
- **HIGH:** Very likely to cause issues. Missing decoupling, unfiltered external connector, adjacent signal layers, large switching loop.
- **MEDIUM:** May cause issues depending on specifics. Decoupling distance, clock on outer layer, via stitching gaps.
- **LOW:** Minor risk, good practice. Internal header filtering, signal layer spacing, interplane capacitance.
- **INFO:** Informational. Cavity resonance frequencies, harmonic spectrum data.

## Limitations

1. **No absolute emission prediction.** Analytical formulas are accurate within ~10-20 dB. Do not use emission estimates to predict pass/fail — use them to identify which frequency bands are most at risk.

2. **Zone polygon data not available.** The PCB analyzer exposes zone bounding boxes and fill ratios but not individual fill polygon coordinates. Ground plane void detection relies on the `return_path_continuity` sampler (requires `--full` mode), which checks at 2mm intervals along traces.

3. **No enclosure modeling.** Board-level analysis only. Cannot account for shielding, apertures, or seam effects.

4. **No cable radiation prediction.** Cannot determine external cable routing or length. Connector filtering checks assume cables may be attached.

5. **Switching frequency estimation.** Based on part number lookup table. Unknown regulators are skipped. User can extend the lookup in `emc_rules._estimate_switching_freq()`.

6. **2-layer board limitations.** The adjacent signal layer check (SU-001) will always fire on 2-layer boards. This is technically correct (2-layer boards inherently have worse EMC than multi-layer) but may generate noise for simple designs where EMC compliance isn't a goal.

## SPICE-Enhanced Mode

When `--spice-enhanced` is passed and a SPICE simulator is detected (via the `spice` skill's `detect_simulator()`), two checks use AC analysis for improved accuracy:

- **PDN impedance (PD-001/PD-002):** Generates an AC sweep testbench with series R-L-C models for each decoupling cap and a 1A current source. Measures V(rail) = Z(f) across 1 kHz to 1 GHz. Captures phase interactions at anti-resonance peaks that the analytical parallel-impedance model underestimates. The SPICE path uses `SpiceTestbench` from `spice_templates.py` for portable rendering across ngspice/LTspice/Xyce.

- **EMI filter (EF-001/EF-002):** Simulates actual insertion loss of the input LC filter including cap parasitics (ESR + ESL). Reports attenuation in dB at the switching frequency and 3rd harmonic.

Implementation: `emc_spice.py` imports from the spice skill's `spice_simulator.py` and `spice_templates.py`. Falls back to analytical if import fails or simulation errors.

**Switching harmonic FFT** — When SPICE is available, `run_switching_fft()` generates a PULSE source at the switching frequency, runs transient analysis for 20 cycles, dumps the raw waveform to ASCII, and computes harmonic magnitudes using the Goertzel algorithm (O(N) per frequency, no numpy). Results appear in EE-002 findings as "SPICE FFT: h1=X, h3=Y (envelope: Z)".

**Cap suggestion verification** — When PD-001 flags an anti-resonance peak, `_suggest_pdn_cap()` computes a cap value whose SRF fills the gap (via `cap_value_for_srf()`, rounded to E12), then optionally re-runs the SPICE PDN sweep with the suggested cap added to verify the peak is resolved. The recommendation includes the specific component value and SPICE verification result.

## Future Enhancements

- **Trace-level ground plane crossing** — when full zone polygon data becomes available, check per-trace-segment crossing of specific voids
