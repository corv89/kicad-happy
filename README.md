# ⚡ kicad-happy

AI-powered design review for KiCad. Analyzes schematics, PCB layouts, and Gerbers. Catches real bugs before you order boards.

Works with **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** and **[OpenAI Codex](https://github.com/openai/codex)**, as a **GitHub Action** for automated PR reviews, or as standalone Python scripts you can run anywhere.

These skills turn your AI coding agent into a full-fledged electronics design assistant that understands your KiCad projects at a deep level: parses schematics and PCB layouts into structured data, cross-references component values against datasheets, detects common design errors, and walks you through the full prototype-to-production workflow.

## 🔬 What it looks like in practice

Point your agent at a KiCad project and it does the rest — parses every schematic and PCB file, traces every net, computes every voltage, and tells you what's wrong before you spend money on boards.

> "Analyze my KiCad project at `hardware/rev2/`"

Here's a condensed example from a real 6-layer BLDC motor controller (187 components). The agent found all of this automatically:

**It builds your power tree** — tracing every regulator from input to load, computing output voltages from feedback dividers, and flagging when the math doesn't match:

```
V+ (10-54V motor bus, TVS protected)
├── MAX17760 buck → +12V (feedback: 226k/16.2k, Vref=1.0V → Vout=14.95V) ⚠️
│   └── TPS629203 → +5V → TPS629203 → +3.3V
├── DRV8353 gate driver (PVDD = V+ direct)
└── 3-Phase Bridge: 6x FDMT80080DC (80V/80A)
    └── 36x 4.7uF 100V bulk caps = 169.2uF
```

**It identifies every subcircuit** — not just passives, but the functional blocks and how they connect:

| Subcircuit  | Details |
|-------------|---------|
| Motor drive | 6 FETs, gate driver, per-phase current sense (0.5mΩ), 3x matched RC filters (22Ω + 1nF = 7.23 MHz) |
| Buses       | 2x SPI, CAN with 120Ω termination, RS-422 differential |
| Protection  | TVS on V+ input (51V standoff matches bus spec), ground domain separation with net ties |
| Sensing     | Battery voltage divider (100k/4.7k → 54V max reads as 2.43V), FET temp NTC |

**It cross-references the PCB** — checking that the layout actually supports what the schematic promises:

```
Board: 56.0 x 56.0 mm, 6-layer, 1.55mm stackup
Routing: 100% complete, 0 unrouted nets

Thermal pad vias:
  Phase FETs: 21-85 vias per pad — good
  STM32 QFN-48: 14 vias — WARNING (recommended: 16)
  Inductor L2:   4 vias — INSUFFICIENT (recommended: 9)
```

**It tells you what needs attention** — and what doesn't:

| Severity   | Issue |
|------------|-------|
| WARNING    | Feedback divider computes to 14.95V, not 12V — Vref heuristic may be wrong, verify datasheet |
| WARNING    | STM32 thermal pad has 14 vias (need 16) — elevated die temp under load |
| WARNING    | Inductor L2 has 4 thermal vias (need 9) — carries the full +12V rail current |
| SUGGESTION | No test point on V+ motor bus — add for bring-up measurements |

**What looks good:** 170µF bus capacitance across 38 caps, proper GND/GNDPWR domain separation, CAN bus termination verified, 100% MPN coverage across all components, zero DFM violations, JLCPCB standard tier compatible.

**It maps your protection coverage** — finds every TVS, ESD suppressor, and fuse, then tells you which interfaces are unprotected:

```
Protection devices:
  D1 (PESD5V0S2UT): USB_DP, USB_DM → GND  [dual-channel ESD] ✓
  D3 (SMBJ51A): V+ motor bus → GND  [TVS, 51V standoff] ✓
  F1 (1A): V+ input  [fuse] ✓
  ⚠️ CAN_H / CAN_L — no TVS protection (exposed on connector J3)
  ⚠️ I2C_SDA / I2C_SCL — no ESD protection (exposed on header J5)
```

**It estimates your sleep current** — traces every always-on path and totals the quiescent draw per rail:

```
+3.3V sleep current breakdown:
  U3 (TPS629203) quiescent: ~15 µA
  R5/R6 feedback divider (226k/16.2k): 13.6 µA
  R12 pull-up (100k to +3.3V): 33 µA
  Total estimated: ~62 µA
```

For a complete example, see the [full design review](example-report.md) of an ESP32-S3 board — 52 components, 2-layer, dual boost converters, USB host, touch sensing. For the end-to-end walkthrough from S-expression parsing through signal detection and datasheet cross-referencing, see [How It Works](how-it-works.md).

## 🚀 Install

We're excited to release kicad-happy as a **Claude Code plugin** — you can now install it with two commands from the `/plugin` menu. For OpenAI Codex, the manual install and agent prompt methods still work as before.

**Claude Code plugin** (recommended):

```
/plugin marketplace add aklofas/kicad-happy
/plugin install kicad-happy@kicad-happy
```

<details>
<summary><strong>Other install methods</strong></summary>

**Ask your agent:**

> Clone https://github.com/aklofas/kicad-happy and install all the skills

**Claude Code (manual):**

```bash
git clone https://github.com/aklofas/kicad-happy.git
mkdir -p ~/.claude/skills
for skill in kicad spice emc bom digikey mouser lcsc element14 jlcpcb pcbway; do
  ln -sf "$(pwd)/kicad-happy/skills/$skill" ~/.claude/skills/$skill
done
```

**OpenAI Codex (manual):**

```bash
git clone https://github.com/aklofas/kicad-happy.git
mkdir -p ~/.codex/skills
for skill in kicad spice emc bom digikey mouser lcsc element14 jlcpcb pcbway; do
  ln -sf "$(pwd)/kicad-happy/skills/$skill" ~/.codex/skills/$skill
done
```

</details>

The analysis scripts are **pure Python 3.8+** with zero required dependencies. No pip install, no Docker, no KiCad installation needed.

## ⚙️ GitHub Action

Also available as a **GitHub Action** for automated PR reviews. Every push and PR that touches KiCad files gets a commit status check and a structured review comment — power tree, SPICE results, EMC risk, thermal analysis, and more. Optionally chain with Claude for AI-powered natural-language reviews.

See the **[GitHub Action setup guide](github-action.md)** for workflow examples, diff-based PR reviews, and AI-powered review configuration.

## 📦 Skills

| Skill | What it does |
|-------|-------------|
| **kicad** | ⚡ Parse and analyze KiCad schematics, PCB layouts, Gerbers, and PDF reference designs. Automated subcircuit detection, design review, DFM. |
| **spice** | 🔬 SPICE simulation — generates testbenches for detected subcircuits, validates filter frequencies, opamp gains, divider ratios. Monte Carlo tolerance analysis. ngspice, LTspice, Xyce. |
| **emc** | 📡 EMC pre-compliance — 42 rule checks for radiated emission risks, PDN impedance, diff pair skew, ESD paths. FCC/CISPR/automotive/military. |
| **bom** | 📋 Full BOM lifecycle — analyze, source, price, export tracking CSVs, generate per-supplier order files. |
| **digikey** | 🔎 Search DigiKey for components and download datasheets via API. |
| **mouser** | 🔎 Search Mouser for components and download datasheets. |
| **lcsc** | 🔎 Search LCSC for components (production sourcing, JLCPCB parts library). |
| **element14** | 🔎 Search Newark/Farnell/element14 (one API, three storefronts). |
| **jlcpcb** | 🏭 JLCPCB fabrication and assembly — design rules, BOM/CPL format, ordering workflow. |
| **pcbway** | 🏭 PCBWay fabrication and assembly — turnkey with MPN-based sourcing. |

## 🖐️ Ask about specific circuits

You don't have to ask for a full design review — just point the agent at whatever you're working on:

> "Check the two capacitive touch buttons on my PCB for routing or placement issues"

> "Is my boost converter loop area going to cause EMI problems?"

> "Trace the enable chain for my power sequencing — is the order correct?"

> "Are the differential pairs on my USB routed correctly?"

The agent runs the analysis scripts, then autonomously digs deeper — tracing nets, analyzing zone fills, calculating clearances, reading datasheets.

## What the analysis covers

| Domain | What it checks |
|--------|---------------|
| **Power** | Regulator Vout from feedback dividers (~60 Vref families), power sequencing, enable chains, inrush, sleep current |
| **Analog** | Opamp gain/bandwidth (per-part behavioral models), voltage dividers, RC/LC filters, crystal load caps |
| **Protection** | TVS/ESD mapping, reverse polarity FETs, fuse sizing, clamping voltage |
| **Digital** | I2C pull-up validation with rise time calculation, SPI CS counts, UART voltage domains, CAN termination |
| **Derating** | Capacitor voltage (ceramic 50%/electrolytic 80%), IC abs max, resistor power. Commercial/military/automotive profiles. Over-designed component detection. |
| **PCB** | Thermal via adequacy, zone stitching, trace width vs current, DFM scoring, impedance, proximity/crosstalk |
| **Manufacturing** | MPN coverage audit, JLCPCB/PCBWay format export, assembly complexity scoring |
| **Lifecycle** | Component EOL/NRND/obsolescence alerts, temperature grade audit, alternative part suggestions |
| **Thermal** | Junction temperature estimation for LDOs, switching regulators, shunt resistors. Package Rθ_JA lookup, PCB thermal via correction, proximity warnings for caps near hotspots. |
| **EMC** | Ground plane voids, decoupling, I/O filtering, switching harmonics, clock routing, diff pair skew, board edge radiation, PDN impedance, ESD paths, crosstalk, thermal derating. FCC/CISPR/automotive/military. |

## 🔬 SPICE simulation

> "Sweep my LC matching network and show me where it actually resonates vs where I designed it"

> "What's the actual phase margin on my opamp filter stage with this TL072?"

> "Run SPICE on everything the analyzer detected and tell me what doesn't look right"

The **spice** skill goes beyond static analysis. It automatically generates SPICE testbenches for detected subcircuits — RC/LC filters, voltage dividers, opamp stages, feedback networks, transistor switches, crystal oscillators — runs them, and reports whether simulated behavior matches calculated values.

For recognized opamps (~100 parts), it uses **per-part behavioral models** with the real GBW, slew rate, and output swing from distributor APIs or a built-in lookup table. When both schematic and PCB exist, it injects **PCB trace parasitics** into the simulation.

```
Simulation: 14 pass, 1 warn, 0 fail
  RC filter R5/C3 (fc=15.9kHz): confirmed, <0.3% error
  Opamp U4A (inverting, gain=-10): 20.0dB confirmed
    Bandwidth 98.8kHz (LM324 behavioral, GBW=1.0MHz)
    Note: signal frequency should stay below 85kHz for <1dB gain error
```

**Monte Carlo tolerance analysis** — run N simulations per subcircuit with randomized component values within tolerance bands. Shows which component dominates output variation:

```
Monte Carlo (N=100): RC filter R5/C3
  fc: 15.9kHz ± 1.8kHz (3σ), spread 22.6%
  Sensitivity: C3 (10%) contributes 68%, R5 (5%) contributes 32%
```

**What-if parameter sweep** — instantly see the impact of component changes without editing the schematic:

```
> "What happens if I change R5 from 10k to 4.7k?"

  RC filter R5/C3: cutoff 1.59kHz → 3.39kHz (+112.8%)
  Voltage divider R5/R6: ratio 0.32 → 0.50 (+56.4%)
```

Requires ngspice, LTspice, or Xyce (auto-detected). Without one, simulation is skipped — the rest of the analysis still works. For the full methodology — see **[SPICE Integration Guide](spice-integration.md)**.

## 📡 EMC pre-compliance

> "Will my board pass FCC Class B? Check for EMC issues."

> "Analyze my switching regulator layout for EMI problems"

> "Check my differential pairs for skew-induced common-mode radiation"

The **emc** skill predicts the most common causes of EMC test failures — ground plane voids, insufficient decoupling, unfiltered I/O cables, switching regulator harmonics, differential pair skew, and more. It operates on the schematic and PCB analyzer output using geometric rule checks and analytical emission formulas (Ott, Paul, Bogatin). When ngspice is available, PDN impedance and EMI filter checks are SPICE-verified for higher accuracy — otherwise analytical models are used as fallback.

```
EMC risk score: 73/100
  CRITICAL: 1 — SPI_CLK crosses ground plane void on In1.Cu
  HIGH:     2 — USB diff pair 5.2mm skew (exceeds 25ps limit),
                no ground via near TVS U5
  MEDIUM:   3 — decoupling cap 7mm from U3, clock on outer layer,
                via stitching gap near J2
  INFO:     4 — cavity resonance at 715 MHz, switching harmonics
                in 30-88 MHz band

Pre-compliance test plan:
  Focus band: 30-88 MHz (12 switching harmonics from U1, U4)
  Highest risk interface: J1 (USB-C, unfiltered, 480 Mbps)
  Probe points: L1 (45.2, 32.1)mm, Y1 (62.0, 18.5)mm
```

42 rule checks across power integrity, signal integrity, and radiation. Includes full-board PDN impedance with power tree analysis — traces impedance from regulator output through PCB traces to IC load points, and detects cross-rail coupling when a downstream switching regulator injects transients onto the upstream rail. Supports FCC, CISPR, automotive (CISPR 25), and military (MIL-STD-461G) standards. Generates a pre-compliance test plan with frequency band priorities, interface risk rankings, and near-field probe points. For the full methodology — see **[EMC Pre-Compliance Guide](emc-precompliance.md)**.

## 📄 Datasheet sync

> "Sync datasheets for my board at `hardware/rev2/`"

Downloads PDFs for every component with an MPN from DigiKey, LCSC, element14, or Mouser into a local `datasheets/` directory. 96% success rate across 240+ manufacturers. Each PDF is verified against the expected part number. The agent reads these during review to validate component values against manufacturer recommendations.

Pre-extracted datasheet specs can be cached as structured JSON for faster repeated reviews on large designs. See the [datasheet extraction reference](skills/kicad/references/datasheet-extraction.md).

## 📋 BOM management — from schematic to order

> "Source all the parts for my board, I'm building 5 prototypes"

This is where things get *really* good. The BOM skill manages the entire lifecycle of your bill of materials — and it all lives in your KiCad schematic as the single source of truth. No separate spreadsheets to keep in sync, no copy-pasting between tabs.

The agent analyzes your schematic to detect which distributor fields are populated (and which naming convention you're using — it handles dozens of variants like `Digi-Key_PN`, `DigiKey Part Number`, `DK`, etc.), identifies gaps, searches distributors to fill them, validates every match, and exports per-supplier order files in the exact upload format each distributor expects.

> "I need a 3.3V LDO that can do 500mA in SOT-223, under $1"

```
AZ1117CH-3.3TRG1 — Arizona Microdevices
  3.3V Fixed, 1A, SOT-223-3
  $0.45 @ qty 1, $0.32 @ qty 100
  In stock: 15,000+

AP2114H-3.3TRG1 — Diodes Incorporated
  3.3V Fixed, 1A, SOT-223
  $0.38 @ qty 1, $0.28 @ qty 100
  In stock: 42,000+
```

## 🏭 Manufacturing

> "Generate the BOM for JLCPCB assembly"

Cross-references LCSC part numbers, formats to JLCPCB's exact spec, flags basic vs extended parts. Per-supplier upload files — DigiKey bulk-add CSV, Mouser cart format, LCSC BOM — with quantities already computed for your board count + spares.

## 🗺️ Workflow

1. **Design** your board in KiCad
2. **Sync datasheets** — builds a local library the agent uses for validation
3. **Analyze** schematic and PCB
4. **Simulate** detected subcircuits (ngspice/LTspice/Xyce)
5. **EMC pre-compliance** — ground plane, decoupling, I/O filtering, switching harmonics, PDN impedance
6. **Thermal analysis** — junction temperatures, hotspot identification, proximity warnings
7. **Review** — agent cross-references analysis + simulation + EMC + thermal + datasheets
8. **Source** components from DigiKey/Mouser (prototype) or LCSC (production)
9. **Export** BOM + per-supplier order files for your assembler
10. **Order** from JLCPCB or PCBWay

Or just set up the GitHub Action and get automated reviews on every PR.

## Optional setup

**SPICE simulator** (for the spice skill): `apt install ngspice` or LTspice or Xyce. Auto-detected.

**API keys** (for distributor skills — falls back to web search without them):

| Service | Env variable | Notes |
|---------|-------------|-------|
| DigiKey | `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` | [developer.digikey.com](https://developer.digikey.com/) |
| Mouser | `MOUSER_SEARCH_API_KEY` | My Mouser → APIs |
| element14 | `ELEMENT14_API_KEY` | [partner.element14.com](https://partner.element14.com/) |
| LCSC | *none* | Free community API |

**Optional Python packages**: `requests` (better HTTP), `playwright` (JS-heavy datasheet sites), `pdftotext` (PDF text extraction).

## ✅ KiCad version support

| Version  | Schematic                     | PCB  | Gerber |
|----------|-------------------------------|------|--------|
| KiCad 10 | Full                          | Full | Full   |
| KiCad 9  | Full                          | Full | Full   |
| KiCad 8  | Full                          | Full | Full   |
| KiCad 7  | Full                          | Full | Full   |
| KiCad 6  | Full                          | Full | Full   |
| KiCad 5  | Full (legacy `.sch` + `.lib`) | Full | Full   |

## 🎯 v1.1 — EMC Pre-Compliance + Analysis Toolkit

New skill: **EMC pre-compliance risk analysis** — predicts the most common causes of EMC test failures from your KiCad schematic and PCB layout. Plus four new analysis tools for tolerance, diffing, thermal, and what-if exploration.

**What's in v1.1:**

| Category | Capabilities |
|----------|-------------|
| **EMC pre-compliance** | 42 rule checks across ground plane integrity, decoupling, I/O filtering, switching harmonics, diff pair skew, PDN impedance, ESD paths, crosstalk, board edge radiation, thermal-EMC, shielding. SPICE-enhanced when ngspice is available. FCC/CISPR/automotive/military. |
| **Plugin install** | Available as a Claude Code plugin marketplace — `/plugin marketplace add aklofas/kicad-happy`. |
| **Monte Carlo tolerance** | `--monte-carlo N` runs N simulations with randomized component values within tolerance bands. Reports 3σ bounds and per-component sensitivity analysis. |
| **Design diff** | Compares two analysis JSONs — component changes, signal parameter shifts, EMC finding deltas. GitHub Action `diff-base: true` for automatic PR comparison. |
| **Thermal hotspots** | Junction temperature estimation for LDOs, switching regulators, shunt resistors. Package Rθ_JA lookup, thermal via correction, proximity warnings. |
| **No-connect detection** | Correctly identifies NC markers, library-defined NC pins, and KiCad `unconnected` pin types. Eliminates false floating-pin warnings across 2,253 files. |
| **Code audit** | 22 bug fixes (trace inductance 25x overestimate, PDN target impedance, regulator voltage suffix parser, inner-layer reference planes, and more). Full AnalysisContext migration for cleaner internals. |
| **Validation** | 6,853 EMC analyses across 1,035 repos (zero crashes), 96 equations verified against primary sources, 404K+ regression assertions at 100% pass rate. |

## 🎯 v1.0 — First Stable Release

This is the first stable release of kicad-happy. It marks the point where every piece of the analysis pipeline — schematic parsing, PCB layout review, Gerber verification, SPICE simulation, datasheet cross-referencing, BOM sourcing, and manufacturing prep — has been built, tested against 1,035 real-world KiCad projects, and validated with 294K+ regression assertions. Zero analyzer crashes across the full corpus.

This isn't a beta or a preview. It's production-ready. If you're designing boards in KiCad, this is the version to start with.

**What's in v1.0:**

| Category | Capabilities |
|----------|-------------|
| **Schematic analysis** | 25+ subcircuit detectors (regulators, filters, opamps, bridges, protection, buses, crystals, current sense) with mathematical verification |
| **Voltage derating** | Ceramic (50%), electrolytic (80%), tantalum capacitors. IC absolute max voltage. Resistor power dissipation. Commercial, military, and automotive profiles. Over-designed component detection for cost optimization. |
| **Protocol validation** | I2C pull-up value and rise time calculation, SPI chip select counts, UART voltage domain crossing, CAN 120Ω termination |
| **Op-amp checks** | Bias current path detection, capacitive output loading, high-impedance feedback warning, unused channel detection for dual/quad parts |
| **SPICE simulation** | Auto-generated testbenches for 17 subcircuit types, per-part behavioral models (~100 opamps), PCB parasitic injection, ngspice/LTspice/Xyce |
| **Datasheet extraction** | Structured extraction cache with quality scoring, heuristic page selection, SPICE spec integration |
| **Lifecycle audit** | Component EOL/NRND/obsolescence alerts from 4 distributor APIs, temperature grade auditing (commercial/industrial/automotive/military), alternative part suggestions |
| **PCB layout** | DFM scoring, thermal via adequacy, impedance calculation, differential pair matching, proximity/crosstalk, zone stitching, tombstoning risk |
| **BOM sourcing** | DigiKey, Mouser, LCSC, element14 — per-supplier order file export, pricing comparison, datasheet sync (96% download success rate) |
| **Manufacturing** | JLCPCB and PCBWay format export, design rule validation, rotation offset tables, basic vs extended parts classification |
| **GitHub Action** | Two-tier automated PR reviews: deterministic analysis (free, no API key) + optional AI-powered review via Claude (`ANTHROPIC_API_KEY`). Datasheet download from LCSC (free) and optional DigiKey/Mouser/element14. |
| **KiCad support** | KiCad 5 through 10, including legacy `.sch` format. Single-sheet and multi-sheet hierarchical designs. |

## 🧪 Test harness

Everything above was validated against a [corpus of 1,035 open-source KiCad projects](https://github.com/aklofas/kicad-happy-testharness) — the kind of designs real engineers actually build. The corpus spans hobby boards, production hardware, motor controllers, RF frontends, battery management systems, IoT devices, audio amplifiers, and everything in between. KiCad 5 through 9. Single-sheet and multi-sheet hierarchical. 2-layer through 6-layer.

**The numbers:**

| Metric | Value |
|--------|-------|
| Repos in corpus | 1,035 |
| Schematic files analyzed | 6,845 (100% success) |
| PCB files analyzed | 3,498 (99.9% — 2 failures are empty stub files) |
| Gerber directories analyzed | 1,050 (100% success) |
| EMC pre-compliance analyses | 6,853 (100% success, 151K+ findings) |
| Components parsed | 312,956 |
| Nets traced | 531,418 |
| SPICE subcircuit simulations | 30,646 across 17 types |
| SPICE-verified EMC findings | 169 (PDN impedance via ngspice) |
| Regression assertions | 520K+ at 100% pass rate |
| Equations tracked & verified | 96 with source citations |
| Bugfix regression guards | 77 (100% pass — no fixed bugs have returned) |
| Closed analyzer issues | 191 |

Three-layer regression testing catches drift at every level:

| Layer | What it catches |
|-------|----------------|
| **Baselines** | Output drift between analyzer versions |
| **Assertions** | Hard regressions on known-good results (component counts, detected subcircuits, signal paths) |
| **LLM review** | Semantic issues deterministic checks miss — findings get promoted to machine-checkable assertions |

## 🎨 Why KiCad?

This project exists because **KiCad is absolutely incredible**. Fully open-source, cross-platform, backed by CERN, with a community that ships features faster than most commercial tools. It's used everywhere from weekend hobby projects to production hardware at real companies.

But what makes KiCad truly special for AI-assisted design — and the entire reason this project can exist — is its **beautifully open file format**. Every schematic, PCB layout, symbol, and footprint is stored as clean, human-readable S-expressions. No proprietary binary blobs. No vendor lock-in. No $500 "export plugin" just to read your own data.

This means your AI agent can read your KiCad files directly, understand every component, trace every net, and reason about your design at the same level a human engineer would. No plugins, no export steps, no intermediary formats. Just your KiCad project and a terminal.

Try doing that with Altium or OrCAD. 😉

## 📜 License

MIT

---

*Built with [Claude Code](https://docs.anthropic.com/en/docs/claude-code).* 🤖
