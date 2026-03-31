---
name: spice
description: Run automatic SPICE simulations on detected subcircuits from KiCad schematic analysis — validates filter frequencies, divider ratios, opamp gains, LC resonance, and crystal load capacitance against ngspice simulation results. Generates testbenches, runs them in batch mode, and produces a structured pass/warn/fail report. Requires ngspice installed separately (not bundled with KiCad). Use this skill whenever the user asks to simulate, verify, or validate any analog subcircuit — RC filters, LC filters, voltage dividers, opamp circuits, crystal oscillators. Also use when the user says things like "simulate my circuit", "run spice", "verify with simulation", "check my filter cutoff", "does this divider actually give 1.65V", "what's the actual bandwidth of this opamp stage", "validate the analyzer's calculations", or wants to go beyond static analysis to dynamic SPICE verification. Use during design reviews whenever the schematic analyzer detects simulatable subcircuits and ngspice is available — simulation adds a layer of confidence that calculated values (fc, gain, Vout) match real circuit behavior. Even if the user doesn't explicitly ask for simulation, consider suggesting it when the kicad skill's analysis reports RC filters, opamp circuits, or feedback networks where a numerical validation would catch errors.
---

# SPICE Simulation Skill

Automatically generates and runs ngspice testbenches for circuit subcircuits detected by the `kicad` skill's schematic analyzer. Validates calculated values (filter frequencies, divider ratios, opamp gains) against actual SPICE simulation results and produces a structured report.

This skill inverts the typical simulation workflow: instead of requiring users to create simulation sources and configure analysis (which ~2.5% of KiCad users do), it generates targeted testbenches automatically from the analyzer's subcircuit detections.

## Related Skills

| Skill | Purpose |
|-------|---------|
| `kicad` | Schematic/PCB analysis — produces the analyzer JSON this skill consumes |

**Handoff guidance:** The `kicad` skill's `analyze_schematic.py` produces the analysis JSON with `signal_analysis` detections. This skill reads that JSON, generates SPICE testbenches for simulatable subcircuits, runs ngspice, and produces a structured verification report. Always run the schematic analyzer first. During a design review, run simulation after the analyzer and before writing the final report — simulation results should appear as a verification section in the report.

## Requirements

- **ngspice** — must be installed separately (not bundled with KiCad on any platform as a standalone binary)
  - Linux: `sudo apt install ngspice` or `sudo dnf install ngspice`
  - macOS: `brew install ngspice`
  - Windows: download from ngspice.sourceforge.io
  - Flatpak KiCad users: the Flatpak bundles `libngspice.so` for KiCad's GUI simulator but does NOT include the `ngspice` executable — a separate system install is required
- **Python 3.8+** — stdlib only, no pip dependencies
- **Schematic analyzer JSON** — from `analyze_schematic.py --output`

If ngspice is not installed, skip simulation gracefully and note it in the report. Do not treat a missing ngspice as an error — it's an optional enhancement.

## Workflow

### Step 1: Run the schematic analyzer

```bash
python3 <kicad-skill-path>/scripts/analyze_schematic.py design.kicad_sch --output analysis.json
```

### Step 2: Run SPICE simulations

```bash
# Simulate all supported subcircuit types
python3 <skill-path>/scripts/simulate_subcircuits.py analysis.json --output sim_report.json

# Simulate specific types only
python3 <skill-path>/scripts/simulate_subcircuits.py analysis.json --types rc_filters,voltage_dividers

# Keep simulation files for debugging (default: temp dir, cleaned up)
python3 <skill-path>/scripts/simulate_subcircuits.py analysis.json --workdir ./spice_runs

# Increase timeout for complex circuits (default: 5s per subcircuit)
python3 <skill-path>/scripts/simulate_subcircuits.py analysis.json --timeout 10

# Omit file paths from output (cleaner for reports)
python3 <skill-path>/scripts/simulate_subcircuits.py analysis.json --compact
```

### Step 3: Interpret results and present to user

Read the JSON report and incorporate findings into the design review. See the "Interpreting Results" and "Presenting to Users" sections below.

## What Gets Simulated

The script selects subcircuits from the analyzer's `signal_analysis` section. Not every detection is simulatable — the script skips configurations that can't produce meaningful results (comparators, open-loop opamps, active oscillators).

| Detector | Analysis | What's Measured | Model Fidelity | Trustworthiness |
|----------|----------|-----------------|----------------|-----------------|
| `rc_filters` | AC sweep | -3dB frequency, phase at fc | Exact (ideal passives) | High — results are mathematically exact |
| `lc_filters` | AC sweep | Resonant frequency, Q factor, bandwidth | Near-exact (ideal L/C + ESR estimate) | High — small Q error from estimated ESR |
| `voltage_dividers` | DC operating point | Output voltage, error vs expected | Exact (ideal passives, unloaded) | High — but real loading not modeled |
| `feedback_networks` | DC operating point | FB pin voltage, divider ratio | Exact (ideal passives, unloaded) | High — confirms regulator Vout setting |
| `opamp_circuits` | AC sweep | Gain at 1kHz, -3dB bandwidth | Approximate (ideal opamp) | Medium — confirms feedback math but not real GBW |
| `crystal_circuits` | AC impedance | Load capacitance validation | Approximate (generic BVD model) | Medium — validates cap selection, not oscillation |
| `transistor_circuits` | DC sweep | Threshold voltage, on-state current | Approximate (generic FET/BJT) | Medium — confirms switching behavior, not exact Vth |
| `current_sense` | DC operating point | Current at 50mV/100mV drop | Exact (ideal resistor) | High — validates sense resistor value |
| `protection_devices` | DC sweep | Diode presence, clamping onset | Approximate (generic diode) | Low — confirms device exists, not real clamping voltage |

### What is NOT simulated

- **Comparators / open-loop opamps** — no feedback network to validate, skipped
- **Active oscillators** — self-contained modules, nothing to verify externally
- **Power regulators** — require real control loop models for stability analysis (Phase 2)
- **Level-shifter FETs** — require modeling both FETs together, skipped
- **High-side power switches** — source and drain both on power rails, need full load context
- **Fuses and varistors** — require manufacturer-specific models
- **Snubbers** — detected as a boolean flag on transistor circuits but component refs not captured; noted in transistor sim output
- **Anything without parsed component values** — if `parse_value()` couldn't extract R/C/L values, the detection is skipped

## Output Format

```json
{
  "summary": {"total": 5, "pass": 3, "warn": 1, "fail": 0, "skip": 1},
  "simulation_results": [
    {
      "subcircuit_type": "rc_filter",
      "components": ["R5", "C3"],
      "filter_type": "low-pass",
      "status": "pass",
      "expected": {"fc_hz": 15915, "type": "low-pass"},
      "simulated": {"fc_hz": 15878, "phase_at_fc_deg": -0.78},
      "delta": {"fc_error_pct": 0.23},
      "cir_file": "/tmp/spice_sim_xxx/rc-filter_R5_C3.cir",
      "log_file": "/tmp/spice_sim_xxx/rc-filter_R5_C3.log",
      "elapsed_s": 0.004
    }
  ],
  "workdir": "/tmp/spice_sim_xxx",
  "total_elapsed_s": 0.032,
  "ngspice": "/usr/bin/ngspice"
}
```

**Status values and what they mean:**

| Status | Meaning | Action |
|--------|---------|--------|
| **pass** | Simulation confirms the analyzer's detection within tolerance | Report as confirmed. No action needed. |
| **warn** | Simulation shows something worth noting — small deviation, model limitation, or edge case | Report with context. Often the "warn" reflects a real but minor issue (e.g., slight gain error from ideal opamp model). |
| **fail** | Simulation contradicts the analyzer — wrong frequency, large gain error, unexpected behavior | Investigate. Could be a real design issue, a topology misdetection by the analyzer, or a testbench generation bug. Check the `.cir` file and log. |
| **skip** | Could not simulate — missing data, unsupported configuration, ngspice error | Note in report. Check the `note` field for the reason. |

## Interpreting Results

### Passive circuits (RC filters, LC filters, voltage dividers)

These simulations use ideal component models, so **the simulation is mathematically exact**. Any significant deviation (>1%) from the analyzer's calculated value indicates a bug in either:
- The analyzer's topology detection (e.g., it misidentified which net is input vs output)
- The testbench generation (topology reconstruction error)
- The analyzer's value parsing (component value parsed incorrectly)

In testing across real projects, passive simulations consistently show <0.3% error — essentially confirming the analyzer's math is correct. A "pass" here means the calculated cutoff frequency, resonant frequency, or divider ratio is accurate.

**What these simulations do NOT tell you:** Whether the real circuit behaves this way. The simulation uses ideal isolated subcircuits without loading from downstream stages, PCB parasitics, or temperature effects. A voltage divider that simulates perfectly at 1.65V may actually produce 1.62V when loaded by a high-impedance ADC input — but that loading effect is real circuit behavior, not an analyzer error.

### Opamp circuits

The ideal opamp model (Aol=1e6, single-pole GBW=10MHz) validates the **feedback network math** — it confirms that the resistor/capacitor values produce the expected gain. It does NOT validate bandwidth accurately because real opamp GBW varies dramatically:

| Part | Typical GBW | Ideal model GBW |
|------|-------------|-----------------|
| LM358 | 1 MHz | 10 MHz |
| OPA2340 | 5.5 MHz | 10 MHz |
| AD8605 | 10 MHz | 10 MHz |
| OPA1612 | 80 MHz | 10 MHz |

Always include the `model_note` field in reports. When reporting opamp simulation results, frame them as: "The feedback network produces the expected gain of X dB. The simulated bandwidth of Y kHz is based on an ideal 10MHz GBW model — the actual [part name] has a GBW of Z MHz, which would give a bandwidth of W kHz."

### Crystal circuits

Crystal simulations validate load capacitor selection — they check that the effective load capacitance is in a reasonable range for the crystal's specified CL. They use a generic Butterworth-Van Dyke equivalent circuit model with typical parameters, not the specific crystal's data. The primary value is catching missing or grossly wrong load capacitors, not precise frequency prediction.

### When simulations fail or skip

Check the `note` field first. Common causes:

| Note | Cause | Fix |
|------|-------|-----|
| "ngspice could not measure -3dB frequency" | AC sweep range doesn't include the -3dB point | Check if the filter fc is very low (<0.1 Hz) or very high (>100 MHz) |
| "ngspice AC measurement failed" | Testbench topology error — the circuit doesn't converge | Check `.cir` file for floating nodes or missing connections |
| "Testbench generation failed: KeyError" | Analyzer detection is missing expected fields | Check analyzer JSON — the detection may be incomplete |
| "ngspice failed: ..." | ngspice error during simulation | Check `.log` file for ngspice error messages |

When debugging, use `--workdir` to preserve simulation files. The `.cir` file is a standard ngspice netlist that can be run manually (`ngspice -b file.cir`) or opened in any SPICE-compatible tool. The `.log` file contains ngspice stdout/stderr.

## Presenting Results to Users

When incorporating simulation results into a design review report, follow this pattern:

### For passing simulations (confidence builders)

```
### RC Filter R5/C3 (fc=15.9kHz lowpass) -- Confirmed
Simulated fc=15.9kHz, <0.3% from calculated. Phase=-45 deg at fc as expected.
```

Keep passing results brief — they confirm what the analyzer already reported. Group them if there are many.

### For warnings (context required)

```
### Opamp U4A (inverting gain=-10)
Simulated gain=20.0dB at 1kHz, matching expected -10x. Bandwidth 98.8kHz
(ideal model). Note: LM358 GBW is ~1MHz, so actual bandwidth would be
~100kHz — verify signal frequency stays below 85kHz for <1dB gain error.
```

### For failures (investigation needed)

```
### RC Filter R12/C8 -- MISMATCH
Simulated fc=3.2kHz vs expected 15.9kHz (80% deviation). This likely indicates
the analyzer misidentified the filter topology — R12 may be serving a different
purpose (pull-up, not series filter element). Manually verify the circuit
around R12/C8 in the schematic.
```

### For skips (note the gap)

```
### Crystal Y1 (32.768kHz) -- Not simulated
Active oscillator module — no external load caps to validate.
```

### Summary line for the simulation section

```
## Simulation Verification (4 pass, 1 warn, 0 fail, 1 skip)
ngspice verified 5 subcircuits in 0.03s. All passive circuits confirmed.
One opamp result requires interpretation (see U4A above).
```

## Model Accuracy Reference

For detailed information about the behavioral models used, their accuracy envelopes, and known limitations, read `references/simulation-models.md`. Consult this reference when:
- A user questions the accuracy of a simulation result
- An opamp or crystal simulation shows unexpected behavior
- You need to explain what "ideal model" means in concrete terms

## Script Reference

| Script | Purpose |
|--------|---------|
| `scripts/simulate_subcircuits.py` | Main orchestrator — CLI entry point, reads JSON, generates testbenches, runs ngspice, produces report |
| `scripts/spice_templates.py` | Testbench generators per detector type — one function per signal_analysis key |
| `scripts/spice_models.py` | Behavioral model definitions (ideal opamp, generic semiconductors), net sanitization, engineering notation formatting |
| `scripts/spice_results.py` | ngspice output parsing and per-type evaluation with pass/warn/fail/skip logic |

## Known Limitations

- **No manufacturer SPICE models.** KiCad does not bundle manufacturer models, and neither does this skill. All simulations use ideal or generic behavioral models. This is accurate for passives but approximate for active devices.
- **Opamp GBW is fixed at 10MHz.** The ideal opamp model cannot be adjusted per-part without manufacturer data. Bandwidth results should always be qualified with the actual part's GBW.
- **Voltage dividers are simulated unloaded.** The analyzer's ratio is R_bot/(R_top+R_bot) without loading. Adding a load resistor would make the simulation more "real" but would create false "errors" relative to the analyzer's calculated value. The purpose is to validate the calculation, not model the full circuit.
- **LC filter Q factor uses estimated inductor ESR.** A default Q=100 is assumed for the inductor. Real inductor Q varies from 10 (power inductors) to 300+ (RF inductors). The resonant frequency is accurate regardless of Q.
- **Single-supply opamps default to +/-5V.** The testbench doesn't detect the actual supply rails from the schematic. This affects rail-to-rail clipping behavior but not gain or bandwidth for signals well within the rails.
- **Net names from the analyzer may be `__unnamed_N`.** These are KiCad internal net names for unlabeled wires. They work correctly in simulation but make `.cir` files less readable.
