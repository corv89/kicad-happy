# ⚡ kicad-happy

**AI coding agent skills for electronics design with KiCad.** Analyze schematics, review PCB layouts, download datasheets, source components, and prepare boards for fabrication — all from your terminal.

> 🛠️ **Works with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [OpenAI Codex](https://github.com/openai/codex)** — AI coding agents that live in your terminal. Skills like these extend them into entirely new domains beyond software.

These skills turn your AI coding agent into a full-fledged electronics design assistant that understands your KiCad projects at a deep level: parses schematics and PCB layouts into structured data, cross-references component values against datasheets, detects common design errors, and walks you through the full prototype-to-production workflow.

## 📦 What's included

| Skill         | What it does                                                                                                                                                |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **kicad**     | ⚡ Parse and analyze KiCad schematics, PCB layouts, Gerbers, and PDF reference designs. Automated subcircuit detection, design review, DRC/ERC verification. |
| **spice**     | 🔬 Automatic SPICE simulation — generates ngspice testbenches for detected subcircuits, validates filter frequencies, opamp gains, divider ratios against simulation. |
| **bom**       | 📋 Full BOM lifecycle — analyze, source, price, export tracking CSVs, generate per-supplier order files.                                                    |
| **digikey**   | 🔎 Search DigiKey for components and download datasheets via API.                                                                                           |
| **mouser**    | 🔎 Search Mouser for components and download datasheets.                                                                                                    |
| **lcsc**      | 🔎 Search LCSC for components (production sourcing, JLCPCB parts library).                                                                                  |
| **element14** | 🔎 Search Newark/Farnell/element14 for components (international sourcing, one API for three storefronts).                                                  |
| **jlcpcb**    | 🏭 JLCPCB fabrication and assembly — design rules, BOM/CPL format, ordering workflow.                                                                       |
| **pcbway**    | 🏭 PCBWay fabrication and assembly — turnkey assembly with MPN-based sourcing.                                                                              |

## 🚀 Install

The easiest way — just ask your agent:

> Clone https://github.com/aklofas/kicad-happy and install all the skills

And keep up to date with the latest as we use our [test harness](https://github.com/aklofas/kicad-happy-testharness) to validate against a [corpus](https://github.com/aklofas/kicad-happy-testharness/blob/main/repos.md) of open source projects.

> Pull the latest changes for kicad-happy and update my skills

Or do it manually:

<details>
<summary><strong>Claude Code</strong></summary>

```bash
git clone https://github.com/aklofas/kicad-happy.git
cd kicad-happy

# Install all skills (symlinks into ~/.claude/skills/)
mkdir -p ~/.claude/skills
for skill in kicad bom digikey mouser lcsc element14 jlcpcb pcbway spice; do
  ln -sf "$(pwd)/skills/$skill" ~/.claude/skills/$skill
done
```

You can also install individually — symlink any skill folder from `skills/` into `~/.claude/skills/`. For project-specific installs, use `.claude/skills/` in your project root instead.
</details>

<details>
<summary><strong>OpenAI Codex</strong></summary>

```bash
git clone https://github.com/aklofas/kicad-happy.git
cd kicad-happy

# Install all skills (symlinks into ~/.codex/skills/)
mkdir -p ~/.codex/skills
for skill in kicad bom digikey mouser lcsc element14 jlcpcb pcbway spice; do
  ln -sf "$(pwd)/skills/$skill" ~/.codex/skills/$skill
done
```

You can also install individually — symlink any skill folder from `skills/` into `~/.codex/skills/`. For project-specific installs, use `.codex/skills/` in your project root instead.
</details>

The **kicad** skill is the core — the others enhance it with sourcing, datasheets, and manufacturing workflows.

### Optional dependencies

The analysis scripts are pure Python 3 with no required dependencies. Optional extras:

- `ngspice` — SPICE simulation engine for the **spice** skill (`apt install ngspice` / `brew install ngspice`). Not bundled with KiCad — requires separate install. Without it, simulation is gracefully skipped.
- `requests` — better datasheet downloads (handles HTTP/2, manufacturer anti-bot)
- `playwright` — last-resort fallback for JS-heavy datasheet sites (Broadcom, Espressif)
- `pdftotext` (poppler-utils) — better PDF text extraction for datasheet verification

### API keys (optional)

The distributor skills work best with API credentials, but none are strictly required — the agent falls back to web search for component lookups and datasheet downloads.

> "Help me set up API keys for the distributor skills"

| Distributor   | Env variables                                | How to get                                                                                             |
| ------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| **DigiKey**   | `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` | [DigiKey API Portal](https://developer.digikey.com/) — register an app, get OAuth 2.0 credentials      |
| **Mouser**    | `MOUSER_SEARCH_API_KEY`                      | My Mouser → APIs → register for Search API key                                                         |
| **element14** | `ELEMENT14_API_KEY`                          | [element14 API Portal](https://partner.element14.com/) — one key covers Newark, Farnell, and element14 |
| **LCSC**      | *none needed*                                | Uses the free [jlcsearch](https://jlcsearch.tscircuit.com/) community API                              |

## 🔬 What it looks like in practice

> "Analyze my KiCad project at `hardware/rev2/`"

The agent runs the analysis scripts, reads datasheets, and produces a full design review. Here's a condensed example from a real project — a 6-layer BLDC motor controller (187 components):

**Power tree** — every regulator traced from input to output, feedback dividers identified, output voltage computed:

```
V+ (10-54V motor bus, TVS protected)
├── MAX17760 buck → +12V (feedback: 226k/16.2k, Vref=1.0V → Vout=14.95V)
│   └── TPS629203 → +5V → TPS629203 → +3.3V
├── DRV8353 gate driver (PVDD = V+ direct)
└── 3-Phase Bridge: 6x FDMT80080DC (80V/80A)
    └── 36x 4.7uF 100V bulk caps = 169.2uF
```

**Detected subcircuits** — found automatically from the schematic:

| Subcircuit  | Details                                                                                            |
| ----------- | -------------------------------------------------------------------------------------------------- |
| Motor drive | 6 FETs, gate driver, per-phase current sense (0.5mΩ), 3x matched RC filters (22Ω + 1nF = 7.23 MHz) |
| Buses       | 2x SPI, CAN with 120Ω termination, RS-422 differential                                             |
| Protection  | TVS on V+ input (51V standoff matches bus spec), ground domain separation with net ties            |
| Sensing     | Battery voltage divider (100k/4.7k → 54V max reads as 2.43V), FET temp NTC                         |

**PCB cross-reference** — the review covers layout too:

```
Board: 56.0 x 56.0 mm, 6-layer, 1.55mm stackup
Routing: 100% complete, 0 unrouted nets

Thermal pad vias:
  Phase FETs: 21-85 vias per pad — good
  STM32 QFN-48: 14 vias — WARNING (recommended: 16)
  Inductor L2:   4 vias — INSUFFICIENT (recommended: 9)
```

**Issues found:**

| Severity   | Issue                                                                                        |
| ---------- | -------------------------------------------------------------------------------------------- |
| WARNING    | Feedback divider computes to 14.95V, not 12V — Vref heuristic may be wrong, verify datasheet |
| WARNING    | STM32 thermal pad has 14 vias (need 16) — elevated die temp under load                       |
| WARNING    | Inductor L2 has 4 thermal vias (need 9) — carries the full +12V rail current                 |
| SUGGESTION | No test point on V+ motor bus — add for bring-up measurements                                |

**What looks good:** 170µF bus capacitance across 38 caps, proper GND/GNDPWR domain separation, CAN bus termination verified, 100% MPN coverage across all components, zero DFM violations, JLCPCB standard tier compatible.

For a complete example, see the [full design review](example-report.md) of an ESP32-S3 board — 52 components, 2-layer, dual boost converters, USB host, touch sensing.

The analysis covers every domain in the design:

| Category          | Examples                                                                                           |
| ----------------- | -------------------------------------------------------------------------------------------------- |
| **Power**         | Regulator Vout computed from feedback dividers, power sequencing, enable chains, inrush analysis   |
| **Analog**        | Op-amp gain computation, voltage dividers with ratios, RC/LC filter cutoff frequencies             |
| **Protection**    | TVS/ESD mapping per interface, MOSFET switch gate drive analysis, flyback diode checks             |
| **Digital**       | I2C pull-up verification, SPI/UART/CAN bus detection, differential pairs, level crossing analysis  |
| **Motor/Power**   | H-bridge and 3-phase bridge detection, current sense shunts, gate driver mapping                   |
| **RF**            | Signal chains, switch matrices, mixer/LNA/PA identification, balun detection                       |
| **PCB**           | Thermal via adequacy, zone stitching density, trace width vs current, DFM checks, tombstoning risk |
| **Manufacturing** | BOM consolidation opportunities, MPN coverage audit, assembly complexity scoring                   |

### 📚 How the analysis works

The analysis scripts parse KiCad's S-expression file format directly into structured JSON — component lists, net connectivity, detected subcircuits, board dimensions, DFM measurements. The agent then reads that JSON alongside your datasheets to cross-reference values, trace signal paths, and write a design review with every conclusion shown and verifiable. For the full end-to-end walkthrough from S-expression parsing through signal detection, datasheet cross-referencing, design review, and discussion of limitations — see **[How It Works](how-it-works.md)**. Detailed methodology documentation for each analyzer:

- **[Schematic analysis methodology](skills/kicad/scripts/methodology_schematic.md)** — parsing pipeline, multi-sheet net building, component classification heuristics, and all 21 signal path detectors (voltage dividers, regulators, RC/LC filters, op-amp circuits, transistor switches, protection devices, bridge circuits, bus detection, and more)
- **[PCB layout analysis methodology](skills/kicad/scripts/methodology_pcb.md)** — footprint extraction, union-find connectivity, DFM scoring, thermal/placement/signal integrity analysis
- **[Gerber analysis methodology](skills/kicad/scripts/methodology_gerbers.md)** — RS-274X and Excellon parsing, X2 attribute extraction, layer identification, completeness and alignment checks, zip archive staleness detection

### 🖐️ Ask about specific circuits

You don't have to ask for a full design review — just point the agent at whatever you're working on:

> "Check the two capacitive touch buttons on my PCB for routing or placement issues"

> "Is my boost converter loop area going to cause EMI problems?"

> "Trace the enable chain for my power sequencing — is the order correct?"

> "Are the differential pairs on my USB routed correctly?"

The agent runs the analysis scripts, then autonomously digs deeper — tracing nets, analyzing zone fills, calculating clearances, reading datasheets.

### 🔬 SPICE simulation — verify your circuits actually work

> "Simulate my feedback divider and check if the regulator will actually output the right voltage"

> "Sweep my LC matching network and show me where it actually resonates vs where I designed it"

> "What's the actual phase margin on my opamp filter stage with this TL072?"

The **spice** skill goes beyond static analysis. It automatically generates ngspice testbenches for detected subcircuits — RC/LC filters, voltage dividers, opamp stages, feedback networks, transistor switches, crystal oscillators, and more — runs them, and reports whether the simulated behavior matches the calculated values.

For common opamps like LM358, TL072, and MCP6002 (~100 parts in the lookup table), it uses **per-part behavioral models** with the real GBW, slew rate, and output swing from the datasheet. An LM358 at gain=-100 correctly shows bandwidth limited to ~10kHz — something the static analysis can't tell you.

When both schematic and PCB exist, it can inject **PCB trace parasitics** into the simulation — trace resistance, via inductance, and inter-trace coupling extracted from the actual board geometry. A 50mm trace adding 133mΩ to your 10kΩ RC filter? The simulation shows the actual impact.

```
Simulation Verification (14 pass, 1 warn, 0 fail)
ngspice verified 15 subcircuits in 0.03s.

  RC filter R5/C3 (fc=15.9kHz): confirmed, <0.3% error
  Feedback divider R10/R11 (Vout=0.596V): confirmed, 0.0% error
  Opamp U4A (inverting, gain=-10): gain confirmed at 20.0dB
    Bandwidth 98.8kHz (LM324 behavioral, GBW=1.0MHz)
    Note: signal frequency should stay below 85kHz for <1dB gain error
```

Requires `ngspice` installed separately (`apt install ngspice`). Without it, simulation is skipped and the design review still works — you just don't get the dynamic verification layer.

For the full methodology — model accuracy, parasitic extraction formulas, supported subcircuit types, and the model resolution cascade — see **[SPICE Integration Guide](spice-integration.md)**.

### 📏 Standards compliance (IPC/IEC)

For designs with high voltage (>50V), high current (>1A power traces), mains input, or safety isolation barriers, reviews automatically check against IPC-2221A conductor spacing and current capacity, IPC-4761 via protection, and ECMA-287/IEC 60664-1 creepage/clearance tables. It won't bother you about creepage on a 3.3V hobby board — standards checks kick in when they actually matter.

The reference tables are built from publicly available documents and secondary sources. Got official IPC/IEC PDFs collecting dust on a hard drive? Send them our way — safety standards shouldn't live behind a $200 paywall while engineers are out here trying to build things that don't catch fire.

### 📄 Sync datasheets for a project

> "Sync the datasheets for my board at `hardware/rev2/`"

```
Analyzing board.kicad_sch...
Found 18 unique parts with MPNs (12 skipped without MPN)
[1/14] STM32G474CEU6
  Searching DigiKey...
  OK: STM32G474CEU6_IC_MCU_32BIT_512KB_FLASH_UFQFPN-48.pdf (5,841,203 bytes) ✓ verified
[2/14] DRV8353SRTAR
  Trying schematic URL...
  OK: DRV8353SRTAR_IC_GATE_DRIVER_3PHASE_WQFN-40.pdf (3,127,445 bytes) ✓ verified
[3/14] TCAN1057AEV-Q1
  Searching DigiKey...
  OK: TCAN1057AEV-Q1_IC_CAN_TRANSCEIVER_5MBPS_SOIC-8.pdf (892,106 bytes) ✓ verified
...
Datasheet sync complete:
  Downloaded: 14
  Already present: 0
  Failed: 0
  Output: hardware/rev2/datasheets/
```

Creates a `datasheets/` directory with human-readable filenames and an `index.json` manifest. Subsequent runs only download new or changed parts. Each PDF is verified against the expected MPN. The agent then reads these datasheets during design review to validate component values against manufacturer recommendations.

### 📋 BOM management — from schematic to order

> "Source all the parts for my board, I'm building 5 prototypes"

This is where things get *really* good. The BOM skill manages the entire lifecycle of your bill of materials — and it all lives in your KiCad schematic as the single source of truth. No separate spreadsheets to keep in sync, no copy-pasting between tabs.

The agent analyzes your schematic to detect which distributor fields are populated (and which naming convention you're using — it handles dozens of variants like `Digi-Key_PN`, `DigiKey Part Number`, `DK`, etc.), identifies gaps, searches distributors to fill them, validates every match against the footprint and specs, and exports per-supplier order files in the exact upload format each distributor expects.

**The workflow:**

1. **Analyze** — scans your schematic for existing part numbers, detects the naming convention, identifies gaps
2. **Sync datasheets** — downloads PDFs for every MPN into a local `datasheets/` directory (DigiKey, LCSC, element14, and Mouser all supported)
3. **Source** — searches distributors by MPN, fills in missing part numbers, validates package/specs match
4. **Export** — generates a tracking CSV and per-supplier order files with quantities computed for your board count + spares

> "I need a 3.3V LDO that can do 500mA in SOT-223, under $1"

The agent searches DigiKey via API, filters by your specs, and returns pricing and stock:

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

Pick one, and the agent searches Mouser and LCSC for the same MPN to fill in alternate suppliers. One prompt, all suppliers populated, ready for your tracking CSV.

### 🏭 Prepare for manufacturing

> "Generate the BOM for JLCPCB assembly"

The agent extracts the BOM from your schematic, cross-references LCSC part numbers, formats it to JLCPCB's exact spec, and flags basic vs extended parts. CPL files are exported from KiCad directly — the agent handles the BOM side.

> "Generate order files for 10 boards with 2 spares per line"

The agent exports per-supplier upload files — DigiKey bulk-add CSV, Mouser cart format, LCSC BOM — with quantities already computed. It'll flag any parts where your chosen supplier is out of stock and suggest the alternate.

## 🗺️ Workflow overview

1. **Design** your board in KiCad
2. **Sync datasheets** for all components — builds a local library the agent uses for validation
3. **Analyze** the schematic and PCB with the analysis scripts
4. **Simulate** detected subcircuits with ngspice — verifies filter frequencies, opamp gains, divider ratios
5. **Review** the design — the agent cross-references the analysis + simulation with datasheets
6. **Source components** — search DigiKey/Mouser (prototype) or LCSC (production)
7. **Export** BOM tracking CSV + per-supplier order files + CPL for your assembler
8. **Order** boards from JLCPCB or PCBWay

## 🧪 Test harness

The analyzers are validated against **1,000+ open-source projects** across 25 categories using a [dedicated test harness](https://github.com/aklofas/kicad-happy-testharness) that runs every analyzer against the full corpus on each change and catches regressions automatically.

**Three-layer regression testing:**

| Layer          | What it catches                           | How                                                                                     |
| -------------- | ----------------------------------------- | --------------------------------------------------------------------------------------- |
| **Baselines**  | Output drift between analyzer versions    | Snapshot/diff of JSON outputs across the full corpus                                    |
| **Assertions** | Hard regressions on known-good results    | Machine-checkable facts per file (component counts, detected subcircuits, signal paths) |
| **LLM review** | Semantic issues deterministic checks miss | LLM reviews source + output pairs, findings get promoted to assertions                  |

**What gets tested:** All three analyzers (schematic, PCB, Gerber) against every file in the corpus, MPN extraction and validation across all four distributor APIs, the datasheet download pipeline, the BOM manager end-to-end, and legacy KiCad 5 format support.

## 🎨 Why KiCad?

This project exists because **KiCad is absolutely incredible** — and we're not being subtle about it. It is, hands down, the best EDA tool available today. Fully open-source, cross-platform, backed by CERN, with a community that ships features faster than most commercial tools. It's used everywhere from weekend hobby projects to production hardware at real companies. And it's *free*. In 2026. While Altium charges you $10K/year. Unreal. 🎉

But what makes KiCad truly special for AI-assisted design — and the entire reason this project can exist — is its **beautifully open file format**. Every schematic, PCB layout, symbol, and footprint is stored as clean, human-readable S-expressions. No proprietary binary blobs. No vendor lock-in. No reverse engineering. No $500 "export plugin" just to read your own data.

This means your AI agent can read your KiCad files directly, understand every component, trace every net, and reason about your design at the same level a human engineer would. The analysis scripts parse raw `.kicad_sch` and `.kicad_pcb` files into structured JSON, and the agent takes it from there — cross-referencing datasheets, computing filter cutoffs, checking thermal via adequacy, flagging missing pull-ups. No plugins, no export steps, no intermediary formats. Just your KiCad project and a terminal.

Try doing that with Altium or OrCAD. 😉

KiCad + an AI coding agent is the most powerful electronics design workflow you can set up today, and it costs exactly $0 for the EDA tool. The future of hardware design is open, and it's here.

## ✅ KiCad version support

| Version  | Schematic                     | PCB  | Gerber |
| -------- | ----------------------------- | ---- | ------ |
| KiCad 10 | Full                          | Full | Full   |
| KiCad 9  | Full                          | Full | Full   |
| KiCad 8  | Full                          | Full | Full   |
| KiCad 7  | Full                          | Full | Full   |
| KiCad 6  | Full                          | Full | Full   |
| KiCad 5  | Full (legacy `.sch` + `.lib`) | Full | Full   |

## 📜 License

MIT

---

*This project — including the analysis scripts, skills, and this README — was built entirely with [Claude Code](https://docs.anthropic.com/en/docs/claude-code).* 🤖
