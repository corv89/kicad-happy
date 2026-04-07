# Validation Summary

This document describes how kicad-happy is tested and validated. Every change to the analysis engine is verified against a corpus of real-world KiCad projects before release.

*Auto-generated on 2026-04-07 by `generate_validation_md.py`.*

## Why this matters

Hardware design review tools must be trustworthy. A false negative (missed bug) can cost a board respin ($5K-$50K). A false positive (phantom warning) erodes trust until engineers ignore the tool entirely. kicad-happy addresses both through large-scale automated validation that no human reviewer could replicate.

## Test corpus

The [test harness](https://github.com/aklofas/kicad-happy-testharness) contains 5,855 open-source KiCad projects — the kind of designs real engineers actually build.

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
| KiCad 8 | 1,198 |
| KiCad 9 | 1,345 |
| KiCad 10 | 39 |

**Category distribution:**

| Category | Repos |
|----------|------:|
| Miscellaneous KiCad projects | 3,260 |
| Keyboards | 378 |
| ESP32 | 286 |
| Arduino recreations | 269 |
| STM32 | 177 |
| RP2040 / Raspberry Pi | 177 |
| Synthesizers / audio | 172 |
| Motor controllers / robotics | 153 |
| Networking / radio / SDR | 121 |
| Sensor boards / IoT | 107 |
| Retro computing | 104 |
| LED / display | 103 |
| USB / interface adapters | 102 |
| Power / battery | 88 |
| RISC-V / FPGA | 81 |
| *(other categories)* | 277 |

The corpus is sourced from public GitHub repositories. It is not curated for "easy" designs — it includes incomplete projects, unusual topologies, non-standard conventions, and designs with real bugs.

## What gets tested

Every analysis script runs against every applicable file in the corpus. Nothing is skipped or excluded.

### Crash testing

| Analyzer | Files tested | Success rate |
|----------|-------------|--------------|
| Schematic (`analyze_schematic.py`) | 29,179 | 100% |
| PCB (`analyze_pcb.py`) | 18,724 | 100% |
| Gerber (`analyze_gerbers.py`) | 5,447 | 100% |
| EMC (`analyze_emc.py`) | 29,175 | 100% |
| SPICE (`simulate_subcircuits.py`) | 29,181 | 100% |

A single unhandled exception across any analyzer on any file in the corpus is treated as a release blocker.

### Regression assertions

Hard assertions on known-good output values. If a previously correct result changes, the assertion fails and the change must be investigated.

| Category | Assertion count | Pass rate |
|----------|----------------|-----------|
| STRUCT | 707,123 | 100% |
| SEED | 648,547 | 100% |
| FND | 4,718 | 100% |
| BUGFIX | 77 | 100% |
| **Total** | **1,360,465** | **100%** |

Assertions are seeded from validated output and checked on every run. When analyzer logic changes intentionally (new fields, corrected calculations), affected assertions are re-seeded after manual verification.

## Signal detector coverage

40 active schematic detectors verified against the corpus:

| Detector | Repos with hits |
|----------|----------------|
| power_sequencing_validation | 5,061 |
| esd_coverage_audit | 4,166 |
| design_observations | 4,022 |
| decoupling_analysis | 3,086 |
| led_audit | 2,453 |
| power_regulators | 2,373 |
| rc_filters | 2,149 |
| transistor_circuits | 1,804 |
| voltage_dividers | 1,616 |
| crystal_circuits | 1,509 |
| protection_devices | 1,303 |
| debug_interfaces | 827 |
| lc_filters | 663 |
| opamp_circuits | 572 |
| feedback_networks | 385 |
| key_matrices | 349 |
| memory_interfaces | 344 |
| level_shifters | 297 |
| addressable_led_chains | 290 |
| sensor_interfaces | 280 |
| buzzer_speaker_circuits | 234 |
| battery_chargers | 217 |
| motor_drivers | 215 |
| adc_circuits | 215 |
| rf_matching | 187 |
| reset_supervisors | 184 |
| audio_circuits | 164 |
| clock_distribution | 162 |
| power_path | 146 |
| isolation_barriers | 140 |
| current_sense | 139 |
| rf_chains | 118 |
| bridge_circuits | 108 |
| rtc_circuits | 94 |
| ethernet_interfaces | 94 |
| hdmi_dvi_interfaces | 69 |
| led_driver_ics | 65 |
| display_interfaces | 44 |
| thermocouple_rtd | 41 |
| bms_systems | 22 |

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

- `KH-001` through `KH-179`: analyzer issues (179 total, 0 open)
- `TH-001` through `TH-008`: harness infrastructure issues

Each closed issue has a corresponding bugfix regression guard assertion that prevents the bug from returning.

## Numbers at a glance

| Metric | Value |
|--------|-------|
| Repos in corpus | 5,855 |
| Schematic files | 29,179 |
| PCB files | 18,724 |
| Gerber directories | 5,447 |
| EMC analyses | 29,175 |
| SPICE simulations | 29,181 |
| Components parsed | 1,036,545 |
| Nets traced | 1,720,418 |
| Regression assertions | 1,360,465 at 100% |
| Bugfix guards | 67 (100% — no regressions) |
| Closed issues | 179 analyzer + 8 harness |
| Open issues | 0 |
| Schematic detectors | 40 |
