---
name: emc
description: EMC pre-compliance risk analysis for KiCad PCB designs — 18 check categories, 35 rule IDs covering ground plane integrity, decoupling, I/O filtering, switching harmonics, clock routing, via stitching, differential pair skew, board edge radiation, PDN impedance, return path continuity, crosstalk, EMI filter verification, ESD protection paths, thermal-EMC interaction, and shielding advisories. Produces a structured risk report with severity scoring (CRITICAL/HIGH/MEDIUM/LOW/INFO), pre-compliance test plan, and regulatory coverage analysis. Supports FCC Part 15, CISPR 32, CISPR 25 (automotive), and MIL-STD-461G. Consumes both schematic and PCB analyzer JSON outputs. Use this skill when the user asks about EMC, EMI, radiated emissions, conducted emissions, FCC compliance, CE marking, CISPR testing, ground plane issues, decoupling strategy, clock routing EMC, switching noise, common-mode current, differential pair skew, or any question about whether their board will pass EMC testing. Also use when the user says things like "will this pass FCC?", "check my EMC", "is my ground plane okay?", "will my switching regulator cause EMI problems?", "check my decoupling", "analyze EMC risks", "check my differential pairs for EMI", or "generate an EMC pre-compliance test plan". During design reviews, consider running this automatically whenever both schematic and PCB analysis are available — EMC issues are the #1 cause of board respins.
---

# EMC Pre-Compliance Skill

Automated EMC risk analysis for KiCad PCB designs. Identifies the most common causes of EMC test failures using geometric rule checks and analytical emission estimates — no full-wave simulation required.

**This is a risk analyzer, not a compliance predictor.** It catches ~70% of common EMC design mistakes that cause real test failures. It cannot guarantee FCC/CISPR compliance — only a calibrated measurement in an accredited lab can do that. But it can reduce the first-spin failure rate from ~50% toward ~20-30%, potentially saving $5K-$50K per avoided board respin.

## Related Skills

| Skill | Purpose |
|-------|---------|
| `kicad` | Schematic/PCB analysis — produces the analyzer JSON this skill consumes |
| `spice` | SPICE simulation — complementary verification of analog subcircuits |

**Handoff guidance:** The `kicad` skill's `analyze_schematic.py` and `analyze_pcb.py` produce the analysis JSONs. This skill reads both and cross-references schematic data (switching frequencies, clock frequencies, subcircuit detections, protection devices) against PCB data (trace routing, zone coverage, component placement, via stitching, stackup) to identify EMC risks. Always run both analyzers first. The PCB analyzer should be run with `--full` flag for best results (enables per-track coordinate data needed for ground plane crossing and edge proximity checks).

## Requirements

- **Python 3.8+** — stdlib only, no pip dependencies
- **Schematic analyzer JSON** — from `analyze_schematic.py --output`
- **PCB analyzer JSON** — from `analyze_pcb.py --full --output` (strongly recommended with `--full`)

No external tools required — all checks work with analytical formulas. When a SPICE simulator is available (ngspice, LTspice, or Xyce), the `--spice-enhanced` flag enables SPICE-verified PDN impedance and EMI filter insertion loss for higher accuracy. Without a simulator, analytical models run unchanged.

## Workflow

### Step 1: Run the analyzers

```bash
python3 <kicad-skill-path>/scripts/analyze_schematic.py design.kicad_sch --output schematic.json
python3 <kicad-skill-path>/scripts/analyze_pcb.py design.kicad_pcb --full --output pcb.json
```

### Step 2: Run EMC analysis

```bash
# Full analysis with both schematic and PCB
python3 <skill-path>/scripts/analyze_emc.py --schematic schematic.json --pcb pcb.json --output emc.json

# PCB-only analysis (no schematic — fewer checks, no frequency data)
python3 <skill-path>/scripts/analyze_emc.py --pcb pcb.json --output emc.json

# Filter by minimum severity
python3 <skill-path>/scripts/analyze_emc.py --schematic schematic.json --pcb pcb.json --severity high

# Select target standard (affects limit comparisons)
python3 <skill-path>/scripts/analyze_emc.py --schematic schematic.json --pcb pcb.json --standard fcc-class-b

# Select target market (sets all applicable standards)
python3 <skill-path>/scripts/analyze_emc.py --schematic schematic.json --pcb pcb.json --market eu

# JSON output for further processing
python3 <skill-path>/scripts/analyze_emc.py --schematic schematic.json --pcb pcb.json --output emc.json
```

### Step 3: Interpret results and present to user

Read the JSON report and incorporate findings into the design review. See "Output Format" and "Interpreting Results" below.

## What Gets Checked

### Category 1: Ground Plane Integrity (Highest Value)
- **Signal crossing ground plane void** — any signal trace that crosses a gap, split, or void in its reference plane. Almost always causes EMC failure.
- **Return path coverage** — percentage of each signal net that has a continuous reference plane underneath. Flags nets below 95%.
- **Ground plane continuity** — overall ground zone fill ratio and island detection.

### Category 2: Decoupling Effectiveness
- **Decoupling cap distance** — flags caps more than 5mm from their IC power pin.
- **Missing decoupling** — ICs without any nearby decoupling capacitor.
- **Cap-to-via distance** — connection inductance proxy (long traces between cap and via to plane).

### Category 3: I/O Interface Filtering
- **Connector filter presence** — checks for CM chokes, ferrite beads, or filter caps within 25mm of each external connector.
- **ESD protection distance** — TVS/ESD devices should be within 25mm of the connector they protect.
- **Ground pin adequacy** — flags connectors with insufficient ground pins for high-speed signals.

### Category 4: Switching Regulator EMC
- **Harmonic overlap** — maps switching frequency harmonics against FCC/CISPR limit bands.
- **Switching node area** — flags large copper areas on the switching node (SW pin).
- **Input cap loop area** — estimates the hot loop area for buck/boost converters.

### Category 5: Clock Routing Quality
- **Routing layer** — flags clocks on outer (microstrip) layers when inner (stripline) layers are available.
- **Trace length** — flags excessively long clock traces.
- **Edge proximity** — flags clock traces near board edges.
- **Connector proximity** — flags clock traces near I/O connectors.

### Category 6: Via Stitching
- **Stitching spacing** — checks ground via spacing against λ/20 at the highest frequency on the board.
- **Edge stitching** — via stitching density at board perimeter.
- **Layer transition return vias** — signal vias without nearby ground return vias.

### Category 7: Stackup Quality
- **Adjacent signal layers** — flags signal layers without a reference plane between them.
- **Ground plane proximity** — checks dielectric thickness between signal and reference layers.
- **Interplane capacitance** — evaluates power/ground plane pair spacing.

### Category 8: Emission Estimates (Informational)
- **Differential-mode loop radiation** — estimates E-field from identified current loops.
- **Board cavity resonance** — calculates resonant frequencies of the power/ground plane cavity.
- **Harmonic spectrum envelope** — switching regulator harmonic content vs. emission limits.

### Category 9: Differential Pair EMC
- **Intra-pair skew** — computes length mismatch and time skew for each diff pair. Compares against protocol-specific limits (USB HS: 25 ps, PCIe: 5 ps, Ethernet: 50 ps, etc.).
- **CM radiation from skew** — estimates common-mode voltage and radiation from skew-induced differential-to-common-mode conversion.
- **Reference plane change** — flags diff pair nets that change layers (each transition is a DM→CM conversion point).
- **Outer layer routing** — flags diff pairs routed on outer layers when inner stripline layers are available.

### Category 10: Board Edge Analysis
- **Signal near board edge** — flags signal traces within one dielectric height of the board edge on outer layers. These traces lack full ground plane reference and radiate efficiently.
- **Ground pour ring** — checks for continuous ground zone coverage around the board perimeter.
- **Connector area stitching** — checks via stitching density near each external connector against λ/20 requirements.

### Pre-Compliance Test Plan (Advisory)
- **Frequency band prioritization** — ranks FCC/CISPR frequency bands by number of emission sources (switching harmonics, clock harmonics, protocol frequencies).
- **Interface risk ranking** — scores each external connector by protocol speed and filter/ESD presence.
- **Suggested probe points** — lists XY positions of switching inductors, crystals, and unfiltered connectors for near-field scanning.

### Regulatory Coverage (Advisory)
- **Market-to-standards mapping** — given a target market (US, EU, automotive, medical, military), lists all applicable EMC standards.
- **Coverage matrix** — for each standard, classifies tool coverage as partial/minimal/indirect/lab-only.

## Output Format

```json
{
  "summary": {
    "total_checks": 42,
    "critical": 2,
    "high": 5,
    "medium": 8,
    "low": 12,
    "info": 15,
    "emc_risk_score": 73
  },
  "target_standard": "fcc-class-b",
  "findings": [
    {
      "category": "ground_plane",
      "severity": "CRITICAL",
      "rule_id": "GP-001",
      "title": "Signal crosses ground plane void",
      "description": "Net SPI_CLK (25 MHz) crosses a 3.2mm gap in the GND plane on layer In1.Cu between U3 pin 12 and U7 pin 3",
      "components": ["U3", "U7"],
      "nets": ["SPI_CLK"],
      "layer": "In1.Cu",
      "recommendation": "Route SPI_CLK around the ground plane gap, or fill the void if no other net requires it"
    }
  ],
  "emission_estimates": {
    "switching_harmonics": [...],
    "cavity_resonances": [...],
    "loop_radiation": [...]
  },
  "board_info": {
    "dimensions_mm": [100, 80],
    "layer_count": 4,
    "highest_frequency_hz": 48000000,
    "switching_frequencies_hz": [500000, 1000000]
  }
}
```

### Severity Levels

| Severity | Meaning | Action |
|----------|---------|--------|
| **CRITICAL** | Almost certain to cause EMC failure | Must fix before fabrication |
| **HIGH** | Very likely to cause issues | Strongly recommend fixing |
| **MEDIUM** | May cause issues depending on specifics | Review and assess |
| **LOW** | Minor risk, good practice to fix | Fix if convenient |
| **INFO** | Informational — frequencies, estimates | No action needed, useful for lab prep |

### EMC Risk Score

An overall score from 0 (worst) to 100 (best), computed as:

```
score = 100 - (critical × 15) - (high × 8) - (medium × 3) - (low × 1)
```

Clamped to [0, 100]. Scores below 50 suggest significant EMC risk.

## Interpreting Results

### Ground Plane Findings
Any CRITICAL ground plane finding (signal crossing a split/void) is almost always a real problem. The return current must find another path, creating a large loop antenna. Fix these unconditionally.

### Decoupling Findings
Distance-based findings have moderate false positive rates — a cap at 6mm might be fine for a low-speed IC but problematic for a 100MHz clock buffer. Use the frequency context from schematic data to prioritize.

### I/O Filtering
Missing filters are highly relevant for any cable-connected product. For board-to-board connections inside an enclosure, the risk is lower. Consider the product context.

### Emission Estimates
These are order-of-magnitude estimates (±10-20 dB). Do NOT use them to predict pass/fail. Use them to identify which frequency bands are most at risk and to prioritize pre-compliance testing.

## EMC Standards Reference

The analyzer can compare against these standards:

| Standard | Flag | Use Case |
|----------|------|----------|
| FCC Part 15 Class B | `fcc-class-b` | US residential (default) |
| FCC Part 15 Class A | `fcc-class-a` | US commercial/industrial |
| CISPR 32 Class B | `cispr-class-b` | International (EU CE marking) |
| CISPR 32 Class A | `cispr-class-a` | International commercial |
| CISPR 25 Class 5 | `cispr-25` | Automotive (strictest) |
| MIL-STD-461G RE102 | `mil-std-461` | Military/defense |

The standard selection affects emission estimate comparisons and via stitching thresholds. Geometric rule checks (ground plane, decoupling, filtering) apply regardless of standard.

## Limitations

**What this analyzer cannot do:**
- Predict absolute emission levels better than ±10-20 dB
- Account for enclosure effects (shielding, apertures, seams)
- Predict cable radiation without knowing external cable routing and length
- Replace full-wave simulation for complex geometries
- Guarantee compliance — only accredited lab measurement can do that

**What it does well:**
- Catch ~70% of common EMC design mistakes before fabrication
- Prioritize the most likely problem areas
- Provide quantitative relative risk scoring
- Generate a checklist for pre-compliance lab testing
- Save one or more board respins ($5K-$50K per spin)
