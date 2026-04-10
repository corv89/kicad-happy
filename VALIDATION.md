# Validation Summary

This document describes how kicad-happy is tested and validated. Every change to the analysis engine is verified against a corpus of real-world KiCad projects before release.

*Auto-generated on 2026-04-09 by `generate_validation_md.py`.*

## Why this matters

Hardware design review tools must be trustworthy. A false negative (missed bug) can cost a board respin ($5K-$50K). A false positive (phantom warning) erodes trust until engineers ignore the tool entirely. kicad-happy addresses both through large-scale automated validation that no human reviewer could replicate.

## Test corpus

The [test harness](https://github.com/aklofas/kicad-happy-testharness) contains 5,856 open-source KiCad projects — the kind of designs real engineers actually build.

**Corpus diversity:**

| Dimension | Coverage |
|-----------|----------|
| Project types | Hobby boards, production hardware, motor controllers, RF frontends, battery management systems, IoT devices, audio amplifiers, power supplies, sensor boards, dev kits |
| KiCad versions | KiCad 5, KiCad 6, KiCad 7, KiCad 8, KiCad 9, KiCad 10 |
| File formats | `.kicad_sch` (S-expression), legacy `.sch` (EESchema), `.kicad_pcb` |
| Design complexity | Single-sheet through multi-sheet hierarchical, 2-layer through 6-layer |
| Component counts | 3 to 500+ components per project |
| Net complexity | Simple power supplies to multi-bus digital designs (I2C, SPI, UART, CAN, USB, Ethernet, HDMI) |

**KiCad version distribution:**

| Version | Repos |
|---------|------:|
| KiCad 5 | 2,209 |
| KiCad 6 | 1 |
| KiCad 7 | 9 |
| KiCad 8 | 1,225 |
| KiCad 9 | 1,365 |
| KiCad 10 | 40 |

**Category distribution:**

| Category | Repos |
|----------|------:|
| Miscellaneous KiCad projects | 1,810 |
| Keyboards | 449 |
| Synthesizers / audio | 324 |
| Motor controllers / robotics | 315 |
| LED / display | 304 |
| ESP32 | 294 |
| Arduino recreations | 294 |
| Networking / radio / SDR | 254 |
| Sensor boards / IoT | 250 |
| Retro computing | 235 |
| USB / interface adapters | 214 |
| Power / battery | 207 |
| RP2040 / Raspberry Pi | 192 |
| STM32 | 179 |
| ADC / DAC / measurement | 110 |
| *(other categories)* | 425 |

The corpus is sourced from public GitHub repositories. It is not curated for "easy" designs — it includes incomplete projects, unusual topologies, non-standard conventions, and designs with real bugs.

## What gets tested

Every analysis script runs against every applicable file in the corpus. Nothing is skipped or excluded.

### Crash testing

| Analyzer | Files tested | Success rate |
|----------|-------------|--------------|
| Schematic (`analyze_schematic.py`) | 36,545 | 100% |
| PCB (`analyze_pcb.py`) | 18,726 | 100% |
| Gerber (`analyze_gerbers.py`) | 5,447 | 100% |
| EMC (`analyze_emc.py`) | 36,529 | 100% |
| SPICE (`simulate_subcircuits.py`) | 36,547 | 100% |

A single unhandled exception across any analyzer on any file in the corpus is treated as a release blocker.

### Regression assertions

Hard assertions on known-good output values. If a previously correct result changes, the assertion fails and the change must be investigated.

| Category | Assertion count | Pass rate |
|----------|----------------|-----------|
| **Total** | **2,042,829** | **100%** |

Assertions are seeded from validated output and checked on every run. When analyzer logic changes intentionally (new fields, corrected calculations), affected assertions are re-seeded after manual verification.

## Signal detector coverage

42 active schematic detectors verified against the corpus:

| Detector | Repos with hits |
|----------|----------------|
| power_sequencing_validation | 5,842 |
| rail_voltages | 5,550 |
| esd_coverage_audit | 5,074 |
| design_observations | 4,954 |
| decoupling_analysis | 3,848 |
| led_audit | 3,021 |
| power_regulators | 2,986 |
| rc_filters | 2,657 |
| transistor_circuits | 2,295 |
| voltage_dividers | 2,032 |
| crystal_circuits | 1,852 |
| protection_devices | 1,676 |
| debug_interfaces | 1,024 |
| lc_filters | 833 |
| opamp_circuits | 741 |
| feedback_networks | 524 |
| memory_interfaces | 435 |
| key_matrices | 423 |
| sensor_interfaces | 373 |
| addressable_led_chains | 366 |
| level_shifters | 359 |
| buzzer_speaker_circuits | 307 |
| adc_circuits | 281 |
| motor_drivers | 274 |
| battery_chargers | 273 |
| rf_matching | 244 |
| reset_supervisors | 237 |
| clock_distribution | 211 |
| audio_circuits | 203 |
| isolation_barriers | 188 |
| power_path | 187 |
| current_sense | 177 |
| rf_chains | 154 |
| bridge_circuits | 137 |
| rtc_circuits | 121 |
| ethernet_interfaces | 119 |
| led_driver_ics | 83 |
| hdmi_dvi_interfaces | 80 |
| display_interfaces | 55 |
| thermocouple_rtd | 48 |
| bms_systems | 25 |
| lvds_interfaces | 15 |

## How to reproduce

Anyone can reproduce the validation:

```bash
# 1. Clone the harness
git clone https://github.com/aklofas/kicad-happy-testharness.git
cd kicad-happy-testharness

# 2. Clone test repos
python3 checkout.py

# 3. Run analyzers (auto-parallelizes across all CPU cores)
python3 run/run_schematic.py --resume
python3 run/run_pcb.py --resume
python3 run/run_emc.py --resume

# 4. Run regression assertions
python3 regression/run_checks.py
```

The harness requires Python 3.8+ and a checkout of the corpus repos. ngspice is optional but recommended for SPICE assertions. Use `--cross-section smoke` for a quick 20-repo validation.

## Issue tracking

All analyzer bugs found during validation are tracked with sequential IDs:

- `KH-001` through `KH-228`: analyzer issues (208 filed, 208 closed, 0 open)
- `TH-001` through `TH-008`: harness infrastructure issues

Each closed issue has a corresponding bugfix regression guard assertion that prevents the bug from returning.

## Numbers at a glance

| Metric | Value |
|--------|-------|
| Repos in corpus | 5,856 |
| Schematic files | 36,545 |
| PCB files | 18,726 |
| Gerber directories | 5,447 |
| EMC analyses | 36,529 |
| SPICE simulations | 36,547 |
| Components parsed | 1,305,219 |
| Nets traced | 2,093,210 |
| Regression assertions | 2,042,829 at 100% |
| Bugfix guards | 67 (100% — no regressions) |
| Closed issues | 208 analyzer + 8 harness |
| Open issues | 0 |
| Schematic detectors | 42 |
