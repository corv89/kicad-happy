# Validation Summary

This document describes how kicad-happy is tested and validated. Every change to the analysis engine is verified against a corpus of real-world KiCad projects before release.

## Why this matters

Hardware design review tools must be trustworthy. A false negative (missed bug) can cost a board respin ($5K-$50K). A false positive (phantom warning) erodes trust until engineers ignore the tool entirely. kicad-happy addresses both through large-scale automated validation that no human reviewer could replicate.

## Test corpus

The [test harness](https://github.com/aklofas/kicad-happy-testharness) contains 5,800+ open-source KiCad projects — the kind of designs real engineers actually build.

**Corpus diversity:**

| Dimension | Coverage |
|-----------|----------|
| Project types | Hobby boards, production hardware, motor controllers, RF frontends, battery management systems, IoT devices, audio amplifiers, power supplies, sensor boards, dev kits |
| KiCad versions | 5, 6, 7, 8, 9, 10 |
| File formats | `.kicad_sch` (S-expression), legacy `.sch` (EESchema), `.kicad_pcb` |
| Design complexity | Single-sheet through multi-sheet hierarchical, 2-layer through 6-layer |
| Component counts | 3 to 500+ components per project |
| Net complexity | Simple power supplies to multi-bus digital designs (I2C, SPI, UART, CAN, USB, Ethernet, HDMI) |

The corpus is sourced from public GitHub repositories. It is not curated for "easy" designs — it includes incomplete projects, unusual topologies, non-standard conventions, and designs with real bugs.

## What gets tested

Every analysis script runs against every applicable file in the corpus. Nothing is skipped or excluded.

### Crash testing

| Analyzer | Files tested | Success rate |
|----------|-------------|--------------|
| Schematic (`analyze_schematic.py`) | 6,845 | 100% |
| PCB (`analyze_pcb.py`) | 3,498 | 99.9% (2 failures are empty stub files) |
| Gerber (`analyze_gerbers.py`) | 1,050 | 100% |
| EMC (`analyze_emc.py`) | 6,853 | 100% |
| Thermal (`analyze_thermal.py`) | corpus-wide | 100% |

A single unhandled exception across any analyzer on any file in the corpus is treated as a release blocker.

### Regression assertions

Hard assertions on known-good output values. If a previously correct result changes, the assertion fails and the change must be investigated.

| Category | Assertion count | Pass rate |
|----------|----------------|-----------|
| Schematic (component counts, subcircuit detection, signal paths) | ~110K | 100% |
| PCB (footprints, tracks, zones, DFM) | ~83K | 100% |
| EMC (rule findings, severity, scores) | ~100K | 100% |
| SPICE (simulation results, tolerances) | ~89K | 100% |
| Bugfix guards (specific past failures) | 77 | 100% |
| Foundational (parser, value parsing, net detection) | ~46K | 100% |
| **Total** | **428K+** | **100%** |

Assertions are seeded from validated output and checked on every run. When analyzer logic changes intentionally (new fields, corrected calculations), affected assertions are re-seeded after manual verification.

### SPICE simulation

| Metric | Value |
|--------|-------|
| Subcircuit simulations | 30,646 |
| Subcircuit types | 17 (RC filters, LC filters, voltage dividers, opamps, regulators, crystals, etc.) |
| SPICE-verified EMC findings | 169 (PDN impedance via ngspice) |
| Simulator | ngspice (primary), LTspice and Xyce also supported |

SPICE results are cross-checked against analytical calculations. Discrepancies above tolerance thresholds are flagged as issues.

### Equation audits

96 equations used in analysis and EMC rules are tracked with inline tags (`# EQ-001: ...`) and verified against primary sources (textbooks, application notes, standards). Each equation tag includes:

- The formula name and what it computes
- The source reference (e.g., "Bogatin, Signal and Power Integrity, Eq. 12.3")
- A unit test or analytical verification

### Constant audits

295 constants (threshold values, classification tables, default parameters) are tracked across the codebase. Each is tagged by risk level. Zero critical-risk constants are unverified.

## Signal detector coverage

40 active schematic detectors verified against the corpus:

| Detector category | Count | Example detectors |
|-------------------|-------|-------------------|
| Core (signal_detectors.py) | ~15 | Power regulators, filters (RC/LC/pi/notch), opamps, voltage dividers, transistor circuits, crystals, current sense, H-bridges, protection, decoupling |
| Domain (domain_detectors.py) | ~25 | ESD audit, USB-C CC, debug interfaces, power path, ADC conditioning, reset/supervisor, clock distribution, display/touch, sensor fusion, level shifters, audio, LED drivers, RTC, LED lighting audit, thermocouple/RTD, power sequencing |

Each detector is validated for:
- **Detection rate**: how many repos in the corpus trigger the detector
- **False positive rate**: manually verified on a sample when the detector is first written
- **Crash rate**: must be 0% across the full corpus

## EMC rule coverage

42 EMC rules across 17 categories, each validated against the full corpus:

| Category | Rules | What it checks |
|----------|-------|----------------|
| Ground plane integrity | GP-001, GP-002 | Split planes, 2-layer ground pours |
| Decoupling | DC-001..DC-005 | Per-IC bypass caps, shared power net coverage, bulk capacitance |
| I/O filtering | IO-001..IO-003 | Filter presence on external interfaces |
| Switching harmonics | SW-001, SW-002 | Harmonic emission estimates, switching node area |
| Clock routing | CK-001..CK-004 | Clock proximity to edges, guard rings, trace length |
| Differential pairs | DP-001, DP-002 | Skew limits, impedance continuity |
| PDN impedance | PD-001..PD-004 | Target impedance, distributed rail impedance, cross-rail coupling |
| ESD/protection | ES-001 | ESD path effectiveness |
| Via stitching | VS-001 | Ground via density vs wavelength |
| Board edge | BE-001 | Component/trace proximity to board edge |
| Thermal-EMC | TE-001 | Thermal-induced EMC degradation |
| Shielding | SH-001 | Shield can effectiveness |
| Crosstalk | XT-001, XT-002 | Near-end/far-end coupling estimates |
| Connector filtering | CF-001 | Connector-level EMI filtering |
| Return path | RP-001 | Return current continuity across layers |
| Cavity resonance | CR-001 | Board/enclosure resonance modes |
| Component placement | CP-001 | Placement-related EMC risks |

## How to reproduce

Anyone can reproduce the validation:

```bash
# 1. Clone the harness
git clone https://github.com/aklofas/kicad-happy-testharness.git
cd kicad-happy-testharness

# 2. Run the analyzers across the corpus
python3 run/run_schematic.py --jobs 16
python3 run/run_emc.py --jobs 16

# 3. Run regression assertions
python3 regression/run_checks.py --type schematic
python3 regression/run_checks.py --type emc
```

The harness requires Python 3.8+ and a checkout of the corpus repos (instructions in the harness README). ngspice is optional but recommended for SPICE-related assertions.

## Issue tracking

All analyzer bugs found during validation are tracked with sequential IDs:

- `KH-001` through `KH-198`: analyzer issues (198 total, 0 open as of v1.2)
- `TH-001` through `TH-008`: harness infrastructure issues (8 total, 0 open)

Each closed issue has a corresponding bugfix regression guard assertion that prevents the bug from returning.

## Release validation checklist

Before every tagged release:

1. All analyzers run against the full corpus with zero crashes
2. All regression assertions pass at 100%
3. All equation and constant audits pass
4. Any new detectors or rules have corpus-wide detection rates documented
5. No open KH-* or TH-* issues

## Numbers at a glance

| Metric | Value |
|--------|-------|
| Repos in corpus | 5,800+ |
| Schematic files | 6,845 (100% success) |
| PCB files | 3,498 (99.9%) |
| Gerber directories | 1,050 (100%) |
| EMC analyses | 6,853 (100%, 141K+ findings) |
| Components parsed | 312,956 |
| Nets traced | 531,418 |
| Regression assertions | 808K+ at 100% |
| SPICE simulations | 30,646 |
| Equations tracked | 96 with source citations |
| Constants tracked | 295 (0 critical-risk) |
| Bugfix guards | 77 (100% — no regressions) |
| Closed issues | 198 analyzer + 8 harness |
| Open issues | 0 |
| Schematic detectors | 40 |
| EMC rules | 42 across 17 categories |
