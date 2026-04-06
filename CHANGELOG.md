# Changelog

All notable changes to kicad-happy are documented here.

This project follows [Semantic Versioning](https://semver.org/). Each release is validated against a [corpus of 5,800+ real-world KiCad projects](VALIDATION.md) before tagging.

---

## v1.2 (unreleased)

**Theme: Trust + Reach** — makes the engine trustworthy to teams and reachable from both platforms.

### New skill: kidoc

Professional engineering documentation from KiCad projects. Auto-runs all analyses, renders schematics and PCB layouts, generates publication-quality figures, and produces markdown scaffolds with auto-updating data sections and narrative placeholders.

- 8 report types: Hardware Design Description, CE Technical File, Design Review, Interface Control Document, Manufacturing Transfer, Schematic Review, Power Analysis, EMC Report
- Custom reports via `--spec` JSON files
- Output formats: PDF (ReportLab), HTML, Markdown
- Schematic SVG renderer with KiCad-parity colors, font scaling, pin text, net annotations, crop/focus/highlight
- PCB layout renderer with 6 layer presets, net highlighting, crop, annotations
- Publication-quality figures: power tree, architecture, bus topology, connector pinouts
- Matplotlib charts: thermal margin, EMC severity, SPICE validation, Monte Carlo distributions
- Datasheet integration: comparison tables, pin audits, spec summaries
- Narrative engine: per-section context builder with writing guidance

### New detectors (15 domain-specific)

Extracted domain-specific detectors into `domain_detectors.py` (~4,400 LOC) alongside core `signal_detectors.py` (~2,960 LOC). 40 total schematic detectors (was 25 in v1.0).

| Detector | What it finds |
|----------|---------------|
| ESD protection audit | Cross-references every external connector with TVS/ESD devices; flags unprotected pins |
| USB-C CC validation | Verifies 5.1k pull-downs on CC1/CC2; detects PD controller ICs as alternative |
| Debug interfaces | Detects SWD/JTAG connectors, verifies MCU connections |
| Power path / load switches | Load switch ICs, ideal diode / power MUX, USB PD controllers |
| ADC signal conditioning | Voltage references, anti-alias filters, input scaling |
| Reset / supervisor | Supervisor ICs, watchdog timers, RC reset circuits |
| Clock distribution | PLL / clock generators, oscillator outputs, reference crystal matching |
| Display / touch | Display drivers, backlight drivers, touch controller ICs |
| Sensor fusion | IMU / accelerometer / gyro / magnetometer / barometer ICs, interrupt connections |
| Level shifters | TXB/TXS ICs, discrete BSS138-based, voltage domain verification |
| Audio circuits | Amplifier ICs, codec chips, speaker impedance matching |
| LED driver ICs | PWM / matrix / constant-current drivers |
| RTC circuits | RTC ICs, backup battery detection, crystal pairing |
| LED lighting audit | Chain tracing (5 hops), current limiting resistor verification, multi-pin exclusion |
| Thermocouple / RTD | Thermocouple amplifiers, RTD interfaces, cold junction compensation |
| Power sequencing | Power-good daisy chains, enable chain validation, cross-rail dependencies |

### First-class Codex support

- `.agents/skills/` with symlinks to all 11 skills for auto-discovery
- `.agents/plugins/marketplace.json` for Codex marketplace browsing
- Enriched `.codex-plugin/plugin.json` with full metadata
- Agent-neutral language across all SKILL.md files and references (no more `WebFetch`/`WebSearch`/`Claude reads` — works with any LLM agent)
- README presents Claude Code and Codex as equal install paths
- GitHub Action docs cover both `claude-code-action` and `codex-action`

### Project config and suppressions

- `.kicad-happy.yml` project config: compliance target, derating profile, preferred suppliers, board class, rail overrides
- Per-finding suppressions with reasons: `suppress: [{rule: "DC-001", ref: "C5", reason: "intentional"}]`
- Suppressed findings listed but marked, not hidden; active vs suppressed counts in summary
- Cascading config: project defaults merged with per-analysis overrides

### Report improvements

- **Per-finding confidence labels**: deterministic, datasheet-backed, heuristic, AI-inferred
- **Missing information section**: separates "I don't know" from "there's a problem" (missing MPNs, datasheets, unsupported families)
- **Top-risk summary**: top 3 respin risks, bring-up blockers, and manufacturing blockers in report header
- **Fabrication release gate**: dedicated "ready for fab?" check (schematic-PCB consistency, Gerber freshness, BOM completeness, assembly file consistency, fab-house compatibility)

### Bugfixes

- KH-194: ESD audit "can" word boundary matching "scan"
- KH-195: USBPDSINK01 assertion update for PD controller detection
- KH-196: Bare capacitor values parsed as Farads in inrush/PDN calculations
- KH-197: Key matrix topology false positives (19 boards fixed)
- KH-198: LC filter reference collision in multi-project schematics

### Validation

- 428,291 regression assertions at 100% pass rate
- 1,036 repos, 40 schematic detectors, 42 EMC rules
- 270 unit tests, 0 open issues

---

## v1.1.0 — 2026-04-02

**EMC Pre-Compliance + Analysis Toolkit**

### New skill: EMC pre-compliance

42 rule checks across 17 categories predicting EMC test failures from schematic and PCB data. SPICE-enhanced when ngspice is available. Covers FCC, CISPR, automotive, and military standards.

| Category | Rule IDs |
|----------|----------|
| Ground plane integrity | GP-001, GP-002 |
| Decoupling | DC-001 through DC-005 |
| I/O filtering | IO-001 through IO-003 |
| Switching harmonics | SW-001, SW-002 |
| Clock routing | CK-001 through CK-004 |
| Differential pairs | DP-001, DP-002 |
| PDN impedance | PD-001 through PD-004 |
| ESD paths | ES-001 |
| Via stitching | VS-001 |
| Board edge radiation | BE-001 |
| Thermal-EMC coupling | TE-001 |
| Shielding | SH-001 |
| Crosstalk | XT-001, XT-002 |
| Connector filtering | CF-001 |
| Return path continuity | RP-001 |
| Cavity resonance | CR-001 |
| Component placement | CP-001 |

SPICE enhancements: lumped and distributed PDN impedance sweep, EMI filter insertion loss verification, switching harmonic FFT via Goertzel algorithm, capacitor suggestion verification.

### New analysis tools

- **Monte Carlo tolerance analysis** — `--monte-carlo N` runs N simulations with randomized component values. Reports 3-sigma bounds and per-component sensitivity (Pearson r-squared).
- **Design diff** — compares two analysis JSONs, reports component/signal/EMC/SPICE changes. GitHub Action `diff-base: true` for automatic PR comparison.
- **Thermal hotspot estimation** — junction temperature for LDOs, switching regulators, shunt resistors. Package theta-JA lookup, thermal via correction, proximity warnings. 7 rule IDs (TS-001..005, TP-001..002).
- **What-if parameter sweep** — patches component values, recalculates derived fields, optional SPICE re-simulation.

### Plugin distribution

- Published on official Anthropic Claude Code marketplace
- Install: `/plugin marketplace add aklofas/kicad-happy`

### Code audit (22 fixes)

3 critical, 9 high, 6 medium, 4 low severity fixes discovered during comprehensive code audit:

- **Critical**: Trace inductance formula 25x overestimate, circular board bounding box wrong, inner-layer traces mapped to wrong reference plane
- **High**: PDN target impedance 2x too lenient, Goertzel normalization missing 2x factor, two-digit regulator suffix parser (LM2596-12 read as 1.2V), operator precedence in decoupling shared nets, courtyard shapes silently dropped, GP-002 ignoring 2-layer boards, via stitching counting all vias (not just ground), unknown SMPS skipping EMC checks, Tier 2 functions not using AnalysisContext
- **Medium**: No-connect sheet collision, rail voltage estimation duplication, distributed PDN magnitude addition, PCB --full mode re-parsing, zone fill detection KiCad 9/10, layer alias type guard, ground net name matching, SH-001 INFO noise, DC bias derating

### Validation

- 6,853 EMC analyses across 1,035 repos (zero crashes)
- 96 equations verified against primary sources
- 404,558 regression assertions at 100% pass rate
- 30,646 SPICE simulations

---

## v1.0 — 2026-03-31

**First Stable Release**

The first production-ready release. Every piece of the analysis pipeline — schematic parsing, PCB layout review, Gerber verification, SPICE simulation, datasheet cross-referencing, BOM sourcing, and manufacturing prep — built and tested against 1,035 real-world KiCad projects.

### Schematic analysis

- S-expression parser for KiCad 5-10 `.kicad_sch` and legacy `.sch` files
- 25 subcircuit detectors: regulators (buck/boost/LDO), filters (RC/LC/pi/notch), opamps, H-bridges, rectifier bridges, protection circuits, bus protocols, crystal oscillators, current sense, decoupling, voltage dividers
- Mathematical verification: feedback divider calculations, filter cutoff frequencies, power dissipation, bias current paths
- Voltage derating: ceramic (50%), electrolytic (80%), tantalum capacitors; IC absolute max; resistor power. Commercial, military, and automotive profiles.
- Protocol validation: I2C pull-up value and rise time, SPI chip select counts, UART voltage domain crossing, CAN termination
- Op-amp checks: bias current paths, capacitive output loading, high-impedance feedback, unused channels

### PCB layout analysis

- Footprint parsing, track/via/zone analysis, thermal management, DFM scoring
- Thermal via adequacy per pad
- Impedance calculation from stackup parameters
- Differential pair matching and proximity/crosstalk analysis
- Zone stitching, tombstoning risk, courtyard overlap detection

### SPICE simulation

- Auto-generated testbenches for 17 subcircuit types
- Per-part behavioral models (~100 opamps)
- PCB parasitic injection (trace resistance, via inductance)
- Multi-simulator: ngspice, LTspice, Xyce

### Datasheet infrastructure

- Structured extraction cache with quality scoring (5-dimension rubric)
- Heuristic page selection for large PDFs
- DigiKey API as primary datasheet source (direct PDF URLs)
- SPICE spec integration from extracted data

### Component sourcing

- DigiKey (OAuth 2.0), Mouser (API key), LCSC (jlcsearch, no auth), element14/Newark/Farnell
- Per-supplier order file export, pricing comparison
- Datasheet sync: 96% download success rate across corpus

### Manufacturing

- JLCPCB and PCBWay format export (BOM + CPL)
- Design rule validation per fab house
- Basic vs extended parts classification (JLCPCB)
- Rotation offset tables

### Lifecycle audit

- Component EOL/NRND/obsolescence alerts from 4 distributor APIs
- Temperature grade auditing (commercial/industrial/automotive/military)
- Alternative part suggestions

### Gerber verification

- Layer identification, alignment checks, drill analysis
- Zip archive scanning
- Mixed plating detection, NPTH classification

### GitHub Action

- Automated PR reviews on KiCad file changes
- Two-tier: deterministic analysis (free) + optional AI review via Claude
- Commit status checks with findings summary

### KiCad support

- KiCad 5, 6, 7, 8, 9, 10
- Legacy `.sch` format
- Single-sheet and multi-sheet hierarchical designs
- Integer and string net ID formats (KiCad 10 change)

### Validation

- 1,035 repos, 6,845 schematic files, 3,498 PCB files, 1,050 Gerber directories
- 312,956 components parsed, 531,418 nets traced
- 294,000+ regression assertions at 100% pass rate
- 30,646 SPICE simulations across 17 subcircuit types
