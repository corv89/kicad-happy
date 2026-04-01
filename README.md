# ⚡ kicad-happy

AI-powered design review for KiCad. Analyzes schematics, PCB layouts, and Gerbers. Catches real bugs before you order boards.

Works with **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** and **[OpenAI Codex](https://github.com/openai/codex)**, as a **GitHub Action** for automated PR reviews, or as standalone Python scripts you can run anywhere.

These skills turn your AI coding agent into a full-fledged electronics design assistant that understands your KiCad projects at a deep level: parses schematics and PCB layouts into structured data, cross-references component values against datasheets, detects common design errors, and walks you through the full prototype-to-production workflow.

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

| Subcircuit  | Details |
|-------------|---------|
| Motor drive | 6 FETs, gate driver, per-phase current sense (0.5mΩ), 3x matched RC filters (22Ω + 1nF = 7.23 MHz) |
| Buses       | 2x SPI, CAN with 120Ω termination, RS-422 differential |
| Protection  | TVS on V+ input (51V standoff matches bus spec), ground domain separation with net ties |
| Sensing     | Battery voltage divider (100k/4.7k → 54V max reads as 2.43V), FET temp NTC |

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

| Severity   | Issue |
|------------|-------|
| WARNING    | Feedback divider computes to 14.95V, not 12V — Vref heuristic may be wrong, verify datasheet |
| WARNING    | STM32 thermal pad has 14 vias (need 16) — elevated die temp under load |
| WARNING    | Inductor L2 has 4 thermal vias (need 9) — carries the full +12V rail current |
| SUGGESTION | No test point on V+ motor bus — add for bring-up measurements |

**What looks good:** 170µF bus capacitance across 38 caps, proper GND/GNDPWR domain separation, CAN bus termination verified, 100% MPN coverage across all components, zero DFM violations, JLCPCB standard tier compatible.

For a complete example, see the [full design review](example-report.md) of an ESP32-S3 board — 52 components, 2-layer, dual boost converters, USB host, touch sensing. For the end-to-end walkthrough from S-expression parsing through signal detection and datasheet cross-referencing, see [How It Works](how-it-works.md).

## 🚀 Install

Ask your agent:

> Clone https://github.com/aklofas/kicad-happy and install all the skills

<details>
<summary><strong>Claude Code (manual)</strong></summary>

```bash
git clone https://github.com/aklofas/kicad-happy.git
mkdir -p ~/.claude/skills
for skill in kicad bom digikey mouser lcsc element14 jlcpcb pcbway spice; do
  ln -sf "$(pwd)/kicad-happy/skills/$skill" ~/.claude/skills/$skill
done
```
</details>

<details>
<summary><strong>OpenAI Codex (manual)</strong></summary>

```bash
git clone https://github.com/aklofas/kicad-happy.git
mkdir -p ~/.codex/skills
for skill in kicad bom digikey mouser lcsc element14 jlcpcb pcbway spice; do
  ln -sf "$(pwd)/kicad-happy/skills/$skill" ~/.codex/skills/$skill
done
```
</details>

The analysis scripts are **pure Python 3.8+** with zero required dependencies. No pip install, no Docker, no KiCad installation needed.

## ⚙️ GitHub Action

Add automated design review to any KiCad project. No account needed — just add the workflow file:

```yaml
# .github/workflows/kicad-review.yml
name: KiCad Design Review
on:
  push:
    paths: ['**/*.kicad_sch', '**/*.kicad_pcb']
  pull_request:
    paths: ['**/*.kicad_sch', '**/*.kicad_pcb']

permissions:
  contents: read
  pull-requests: write
  statuses: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - run: sudo apt-get install -y ngspice poppler-utils
      - uses: aklofas/kicad-happy@v1
        id: analysis
      - uses: thollander/actions-comment-pull-request@v3
        if: github.event_name == 'pull_request'
        with:
          file-path: ${{ steps.analysis.outputs.report-path }}
          comment-tag: kicad-happy-review
          mode: upsert
```

Every push and PR that touches KiCad files gets a **commit status check** (green/red with findings summary). On PRs, a structured review comment is also posted — power tree, protocol compliance, voltage derating, SPICE results, component health, and PCB stats. The comment updates on re-pushes. A [full report](skills/kicad/references/report-generation.md) is available on the Actions run page.

<details>
<summary><strong>Add AI-powered review (optional — needs Anthropic API key)</strong></summary>

Chain with [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) for Claude to read the analysis + datasheets and write a natural-language design review. Two options:

**Quick review** (~$1-3 per PR, 5-10 min):

```yaml
      - uses: anthropics/claude-code-action@v1
        if: github.event_name == 'pull_request' && env.ANTHROPIC_API_KEY != ''
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          prompt: |
            The kicad-happy deterministic analysis has already been run.
            Read the markdown report at ${{ steps.analysis.outputs.report-path }}.

            Do NOT re-run analysis scripts. Review the findings and:
            1. Verify the top 3-5 IC pinouts against datasheets
            2. Check WARNING findings for accuracy
            3. Note anything the analysis may have missed

            Post a concise summary (under 2000 chars) as a PR comment.
            Focus on actionable findings only.
          claude_args: '--model claude-sonnet-4-6 --max-turns 25'
```

**Thorough review** (~$5-15 per PR, 10-20 min):

```yaml
      - uses: anthropics/claude-code-action@v1
        if: github.event_name == 'pull_request' && env.ANTHROPIC_API_KEY != ''
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          prompt: |
            The kicad-happy deterministic analysis has already been run.
            Read the JSON at ${{ steps.analysis.outputs.schematic-json }}
            and the report at ${{ steps.analysis.outputs.report-path }}.

            Do NOT re-run analysis scripts. Perform a thorough review:
            1. Read datasheets for every IC and verify pinouts
            2. Check voltage divider/feedback calculations against datasheets
            3. Verify application circuit compliance for regulators
            4. Check power sequencing and enable chain logic
            5. Review protection device coverage on external interfaces
            6. Note any design concerns the analysis missed

            Post your review as a PR comment. Include specific datasheet
            page references for each finding.
          claude_args: '--model claude-sonnet-4-6 --max-turns 50'
```

**Setup:** Get an API key from [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys), then add it as a repository secret named `ANTHROPIC_API_KEY` in Settings → Secrets → Actions. Cost depends on design complexity — see [Anthropic pricing](https://www.anthropic.com/pricing).

</details>

See [`action/examples/`](action/examples/) for fork-safe workflows, distributor API keys for datasheet download, and advanced configuration.

## 📦 Skills

| Skill | What it does |
|-------|-------------|
| **kicad** | ⚡ Parse and analyze KiCad schematics, PCB layouts, Gerbers, and PDF reference designs. Automated subcircuit detection, design review, DFM. |
| **spice** | 🔬 Automatic SPICE simulation — generates testbenches for detected subcircuits, validates filter frequencies, opamp gains, divider ratios. ngspice, LTspice, Xyce. |
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

Requires ngspice, LTspice, or Xyce (auto-detected). Without one, simulation is skipped — the rest of the analysis still works. For the full methodology — see **[SPICE Integration Guide](spice-integration.md)**.

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
5. **Review** — agent cross-references analysis + simulation + datasheets
6. **Source** components from DigiKey/Mouser (prototype) or LCSC (production)
7. **Export** BOM + per-supplier order files for your assembler
8. **Order** from JLCPCB or PCBWay

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
| Components parsed | 312,956 |
| Nets traced | 531,418 |
| SPICE subcircuit simulations | 30,646 across 17 types |
| Regression assertions | 294,883 at 99.8% pass rate |
| Bugfix regression guards | 77 (100% pass — no fixed bugs have returned) |
| Closed analyzer issues | 186 |

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
