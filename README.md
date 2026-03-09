# ⚡ kicad-happy

**[Claude Code](https://docs.anthropic.com/en/docs/claude-code) skills for electronics design with KiCad.** Analyze schematics, review PCB layouts, download datasheets, source components, and prepare boards for fabrication — all from your terminal.

> 🛠️ **Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code)** — Anthropic's agentic coding tool that lives in your terminal. Skills like these let you extend it into entirely new domains beyond software.

These skills turn Claude Code into a full-fledged electronics design assistant that understands your KiCad projects at a deep level: it parses schematics and PCB layouts into structured data, cross-references component values against datasheets, detects common design errors, and walks you through the full prototype-to-production workflow.

## 📦 What's included

| Skill       | What it does                                                                                                                                                 |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **kicad**   | 🔍 Parse and analyze KiCad schematics, PCB layouts, Gerbers, and PDF reference designs. Automated subcircuit detection, design review, DRC/ERC verification. |
| **digikey** | 📄 Search DigiKey for components, download datasheets via API, sync a local datasheets directory for your project.                                           |
| **bom**     | 📋 BOM management — create, enrich, price, and export bills of materials across prototype and production phases.                                             |
| **mouser**  | 🛒 Search Mouser for components (secondary prototype source, used when DigiKey is out of stock).                                                             |
| **lcsc**    | 🔎 Search LCSC for components (production sourcing, JLCPCB parts library).                                                                                   |
| **jlcpcb**  | 🏭 JLCPCB fabrication and assembly — design rules, BOM/CPL format, ordering workflow.                                                                        |
| **pcbway**  | 🏭 PCBWay fabrication and assembly — turnkey assembly with MPN-based sourcing.                                                                               |

## 🚀 Install

Clone the repo and symlink each skill into your Claude Code skills directory:

```bash
git clone https://github.com/aklofas/kicad-happy.git
cd kicad-happy

# Install all skills (symlinks into ~/.claude/skills/)
mkdir -p ~/.claude/skills
for skill in kicad digikey bom lcsc jlcpcb mouser pcbway; do
  ln -sf "$(pwd)/$skill" ~/.claude/skills/$skill
done
```

Or install individually — copy or symlink any skill folder into `~/.claude/skills/`. For project-specific installs, use `.claude/skills/` in your project root instead.

The **kicad** skill is the core — the others enhance it with sourcing, datasheets, and manufacturing workflows.

### Optional dependencies

The analysis scripts are pure Python 3 with no required dependencies. Optional extras:

- `requests` — better datasheet downloads (handles HTTP/2, manufacturer anti-bot)
- `playwright` — last-resort fallback for JS-heavy datasheet sites (Broadcom, Espressif)
- `pdftotext` (poppler-utils) — better PDF text extraction for datasheet verification

DigiKey API access requires `DIGIKEY_CLIENT_ID` and `DIGIKEY_CLIENT_SECRET` environment variables ([get credentials here](https://developer.digikey.com/)). Datasheet downloads still work without credentials using fallback web search.

## 🔬 What it looks like in practice

### Analyze a schematic

> "Analyze my KiCad project at `hardware/rev2/`"

Claude runs the analysis scripts, reads datasheets, and produces a full design review report. Here's a condensed example from a 6-layer BLDC motor controller (STM32G474 + DRV8353 gate driver, 3-phase bridge, 187 components):

#### ⚡ Power tree with computed regulator outputs

```
V+ (10-54V motor bus, TVS protected)
├── MAX17760 (switching buck) → +12V
│   ├── R6/R7 feedback (226k/16.2k), Vref=1.0V → Vout=14.95V
│   └── TPS629203 → +5V (CAN transceiver, gate driver logic)
│       └── TPS629203 → +3.3V (MCU, encoder, RS-422)
├── DRV8353 gate driver (PVDD = V+ direct)
└── 3-Phase Bridge: 6x FDMT80080DC (80V/80A)
    └── 36x 4.7uF 100V bulk caps = 169.2uF bus capacitance
```

Every regulator is traced from input to output. Feedback divider resistors are identified, Vref is looked up from a built-in table of ~60 regulator families, and output voltage is computed. When the computed Vout doesn't match the rail name, it's flagged.

#### 📊 Signal analysis — detected automatically from the schematic

| What                  | Details                                                                                |
| --------------------- | -------------------------------------------------------------------------------------- |
| 3-phase bridge        | 6 FETs, DRV8353 gate driver, per-phase current sense (0.5mΩ shunts)                    |
| Current sense filters | 3x matched RC: 22Ω + 1nF = 7.23 MHz cutoff (anti-aliasing for ADC)                     |
| Voltage dividers      | Battery sense (100k/4.7k, ratio 0.045→ max 54V reads as 2.43V), FET temp NTC (47k/10k) |
| Bus topology          | 2x SPI (gate driver + encoder), CAN with 120Ω termination, RS-422 differential         |
| Ground domains        | GND (signal) / GNDPWR (power) separated, single-point net ties                         |
| Decoupling            | 170µF on V+ bus (38 caps), multi-tier bypass on all rails                              |
| Protection            | SMBJ51D TVS on V+ input, 51V standoff matches bus spec                                 |

#### 🔧 PCB layout cross-reference

The report doesn't stop at the schematic — it cross-references against the PCB layout:

```
Board: 56.0 x 56.0 mm, 6-layer, 1.55mm stackup
Routing: 100% complete, 0 unrouted nets
Components: 79 front / 108 back, 94% SMD

Thermal pad via analysis:
  Q1 (FDMT80080DC, phase A high): 67 vias under 36.1mm² pad — good
  Q3 (FDMT80080DC, phase B high): 85 vias — good
  Q6 (FDMT80080DC, phase C low):  21 vias — adequate but lowest
  U4 (STM32G474, QFN-48):         14 vias — WARNING (recommended: 16)
  L2 (100µH inductor):             4 vias — INSUFFICIENT (recommended: 9)

Power planes:
  +3.3V on In2.Cu: 1036mm² dedicated plane
  +5V on In3.Cu:    896mm² dedicated plane
  GND stitching:    98 vias across 2070mm²
  V+ bus:          243 stitching vias

DFM: JLCPCB standard tier compatible
  Min trace: 0.152mm (6 mil)
  Min drill: 0.254mm (10 mil)
  Min annular ring: 0.153mm
```

#### 🐛 Issues found

| Severity   | Issue                                                                                                                 |
| ---------- | --------------------------------------------------------------------------------------------------------------------- |
| WARNING    | MAX17760 feedback divider computes to 14.95V, not 12V — Vref heuristic may be wrong, verify against datasheet Table 1 |
| WARNING    | STM32 QFN-48 thermal pad has 14 vias (recommended minimum: 16) — may cause elevated die temperature under heavy load  |
| WARNING    | Inductor L2 has only 3-4 thermal vias (recommended: 9) — this inductor carries the full +12V rail current             |
| WARNING    | I2C pull-ups not detected on AUX expansion ports — external pull-ups required if I2C mode is used                     |
| SUGGESTION | No test point on V+ motor bus — add for bring-up voltage/ripple measurements                                          |
| SUGGESTION | 38 medium tombstoning-risk 0402 parts — consider thermal relief optimization on ground zones                          |

#### ✅ Positive findings

- Excellent bus capacitance — 170µF across 38 caps, all rated 100V
- Proper ground domain separation (GND/GNDPWR) with single-point net ties — critical for motor noise isolation
- MOSFET thermal design: 21-85 vias per FET pad with extensive copper fill on V+ and phase output zones
- 100% MPN coverage, zero DFM violations, JLCPCB standard tier compatible
- CAN bus properly terminated (120Ω), TVS on power input, all SPI buses verified
- Dedicated +3.3V and +5V power planes on inner layers with good stitching density

---

#### 🎯 What it detects across all designs

The analysis isn't limited to motor controllers. Here's what it automatically identifies across any KiCad project:

| Category          | Subcircuits                                                                                                                                                                     |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Power**         | LDO and switching regulators (Vout computed from feedback dividers), power sequencing, enable chains, power budget estimation, inrush analysis                                  |
| **Analog**        | Op-amp circuits (inverting/non-inverting/transimpedance, gain computation), Howland current pumps, voltage dividers with ratio calculation, RC/LC filters with cutoff frequency |
| **Protection**    | TVS/ESD mapping per external interface, MOSFET high-side/low-side switches with gate drive analysis, flyback diode checks                                                       |
| **Digital**       | I2C buses with pull-up verification, SPI/UART/CAN bus detection, differential pairs, cross-domain signal analysis (3.3V↔5V level compatibility)                                 |
| **Motor/Power**   | H-bridge and 3-phase bridge detection, current sense shunts with measurement range, gate driver mapping                                                                         |
| **RF**            | RF signal chains, switch matrices, mixer/LNA/PA identification, balun detection                                                                                                 |
| **Passives**      | Crystal circuits with load cap calculation, decoupling analysis per rail (bulk + bypass + HF tiers), LED current limiting verification                                          |
| **PCB**           | Thermal pad via adequacy, zone stitching density, trace width vs current capacity, routing completeness, DFM checks, tombstoning risk                                           |
| **Manufacturing** | BOM optimization (consolidation opportunities), MPN/distributor coverage audit, assembly complexity scoring                                                                     |

Claude then cross-references all of this against datasheets to validate component values, check absolute maximum ratings, and verify the design matches the manufacturer's reference circuit. The report includes positive findings too — not just bugs, but confirmation that things are done right.

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

Creates a `datasheets/` directory with human-readable filenames and an `index.json` manifest. Subsequent runs only download new or changed parts. Each downloaded PDF is verified against the expected MPN by extracting text and checking for matches.

The datasheets are then automatically used during schematic analysis — Claude reads them to validate component values against manufacturer recommendations.

### 🩺 Design review

> "Review my schematic for bugs before I order boards"

Claude combines the analyzer output with datasheet cross-referencing to produce a structured review:

- Validates feedback resistor values against regulator Vref (catches wrong output voltage)
- Checks all ICs have proper decoupling caps
- Verifies crystal load cap values match the oscillator requirements
- Flags floating inputs, missing pull-ups on I2C, unprotected external interfaces
- Checks power sequencing across regulators
- Validates signal level compatibility between 3.3V and 5V domains
- Computes thermal budget for power components
- Checks battery voltage range covers regulator input range (including UVLO)

### 🛒 Search for components

> "I need a 3.3V LDO that can do 500mA in SOT-223, under $1"

Claude searches DigiKey, filters by specs, and returns pricing and stock:

```
AZ1117CH-3.3TRG1 - Arizona Microdevices
  3.3V Fixed, 1A, SOT-223-3
  $0.45 @ qty 1, $0.32 @ qty 100
  In stock: 15,000+

AP2114H-3.3TRG1 - Diodes Incorporated
  3.3V Fixed, 1A, SOT-223
  $0.38 @ qty 1, $0.28 @ qty 100
  In stock: 42,000+
```

### 🏭 Prepare for manufacturing

> "Generate the BOM and CPL for JLCPCB assembly"

Claude extracts the BOM from your schematic, cross-references LCSC part numbers, formats the BOM and component placement list to JLCPCB's exact spec, flags basic vs extended parts, and warns about rotation offsets for common packages.

## 🧪 The analysis scripts

Three Python scripts extract structured JSON from KiCad files. They're the data layer — Claude reads the output and applies higher-level reasoning (datasheet validation, design pattern matching, error detection).

```bash
# Schematic analysis (supports .kicad_sch and legacy .sch)
python3 kicad/scripts/analyze_schematic.py hardware/board.kicad_sch

# PCB layout analysis
python3 kicad/scripts/analyze_pcb.py hardware/board.kicad_pcb

# Gerber/drill file verification
python3 kicad/scripts/analyze_gerbers.py hardware/gerbers/
```

All output JSON to stdout. Add `--output file.json` to write to a file, `--compact` for minified output.

### Schematic analyzer output

| Section           | What's in it                                                                                                                                               |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `components`      | Every placed symbol — reference, value, footprint, MPN, position, type classification                                                                      |
| `nets`            | Full connectivity map with pin-to-net assignments and wire counts                                                                                          |
| `bom`             | Deduplicated bill of materials with quantities                                                                                                             |
| `signal_analysis` | Detected subcircuits: regulators, voltage dividers, RC/LC filters, op-amps, transistor drivers, bridges, protection devices, feedback networks, decoupling |
| `design_analysis` | Power domains, bus detection (I2C/SPI/UART/CAN), differential pairs, cross-domain signals, ERC warnings                                                    |

Supports KiCad 5 through 9. Hierarchical designs are parsed recursively. Tested on 240+ components across multiple real projects.

### PCB analyzer output

Footprint inventory with pad/net details, track/via statistics, zone summary, board outline/dimensions, routing completeness, unrouted nets. Add `--full` for individual track/via coordinates.

### Gerber analyzer output

Layer completeness check, drill tool/hole summary, aperture counts, layer alignment verification.

## 🗺️ Workflow overview

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│   KiCad     │───▶│  Datasheet   │───▶│   Analyze   │
│  Schematic  │    │    Sync      │    │  (scripts)  │
└─────────────┘    └──────────────┘    └─────────────┘
                                              │
                   ┌──────────────┐    ┌──────┴──────┐
                   │  Component   │◀───│   Review    │
                   │   Sourcing   │    │ (datasheets)│
                   └──────────────┘    └─────────────┘
                          │
               ┌──────────┴──────────┐
               ▼                     ▼
        ┌─────────────┐    ┌──────────────┐
        │    BOM      │───▶│  Order PCBs  │
        │  + Gerbers  │    │  (JLCPCB/    │
        │  + CPL      │    │   PCBWay)    │
        └─────────────┘    └──────────────┘
```

1. **Design** your board in KiCad
2. **Sync datasheets** for all components — builds a local library Claude uses for validation
3. **Analyze** the schematic and PCB with the analysis scripts
4. **Review** the design — Claude cross-references the analysis with datasheets
5. **Source components** via DigiKey (prototype) or LCSC (production)
6. **Export** BOM + CPL formatted for your assembler
7. **Order** boards from JLCPCB or PCBWay

## 🎨 Why KiCad?

This project exists because **KiCad is absolutely incredible** — and we're not being subtle about it. It is, hands down, the best EDA tool available today. Fully open-source, cross-platform, backed by CERN, with a community that ships features faster than most commercial tools. It's used everywhere from weekend hobby projects to production hardware at real companies. And it's *free*. In 2025. While Altium charges you $10K/year. Unreal. 🎉

But what makes KiCad truly special for AI-assisted design — and the entire reason this project can exist — is its **beautifully open file format**. Every schematic, PCB layout, symbol, and footprint is stored as clean, human-readable S-expressions. No proprietary binary blobs. No vendor lock-in. No reverse engineering. No $500 "export plugin" just to read your own data.

This means Claude can read your KiCad files directly, understand every component, trace every net, and reason about your design at the same level a human engineer would. The analysis scripts parse raw `.kicad_sch` and `.kicad_pcb` files into structured JSON, and Claude takes it from there — cross-referencing datasheets, computing filter cutoffs, checking thermal via adequacy, flagging missing pull-ups. No plugins, no export steps, no intermediary formats. Just your KiCad project and a terminal.

Try doing that with Altium or OrCAD. 😉

KiCad + Claude Code is the most powerful electronics design workflow you can set up today, and it costs exactly $0 for the EDA tool. The future of hardware design is open, and it's here.

## ✅ KiCad version support

| Version | Schematic            | PCB  | Gerber |
| ------- | -------------------- | ---- | ------ |
| KiCad 9 | Full                 | Full | Full   |
| KiCad 8 | Full                 | Full | Full   |
| KiCad 7 | Full                 | Full | Full   |
| KiCad 6 | Full                 | Full | Full   |
| KiCad 5 | Full (legacy `.sch`) | Full | Full   |

## 📜 License

MIT

---

*This project — including the analysis scripts, skills, and this README — was built entirely with [Claude Code](https://docs.anthropic.com/en/docs/claude-code).* 🤖
