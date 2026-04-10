# Changelog

All notable changes to kicad-happy are documented here.

This project follows [Semantic Versioning](https://semver.org/). Each release is validated against a [corpus of 5,800+ real-world KiCad projects](VALIDATION.md) before tagging.

---

## v1.2 (unreleased)

**Theme: Trust + Reach** — 102 commits making the engine trustworthy to teams and reachable from both platforms.

### New skill: kidoc (beta)

Professional engineering documentation from KiCad projects. Auto-runs all analyses, renders schematics and PCB layouts, generates publication-quality figures, and produces markdown scaffolds with auto-updating data sections and narrative placeholders. Early skill with rough edges — actively developed.

- 8 report types: Hardware Design Description, CE Technical File, Design Review, Interface Control Document, Manufacturing Transfer, Schematic Review, Power Analysis, EMC Report
- Custom reports via `--spec` JSON files
- Output formats: PDF (ReportLab), DOCX (python-docx), ODT (odfpy), HTML, Markdown
- Schematic SVG renderer with KiCad-parity colors, font scaling, pin text, net annotations, crop/focus/highlight
- PCB layout renderer with 6 layer presets, net highlighting, crop, annotations
- 12 publication-quality figure generators: power tree, architecture, bus topology, connector pinouts, thermal margin, EMC severity, SPICE validation, Monte Carlo distributions
- Datasheet integration: comparison tables, pin audits, spec summaries
- Narrative engine: per-section context builder with writing guidance
- Hash-based figure caching — unchanged data skips re-render

### New detectors (15 domain-specific)

Extracted domain-specific detectors into `domain_detectors.py` (~4,500 LOC) alongside core `signal_detectors.py` (~3,100 LOC). 40+ total schematic detectors (was 25 in v1.0).

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
| LVDS interfaces | FPD-Link, DS90, SN65LVDS families with serializer/deserializer classification |

### First-class Codex support

- `.agents/skills/` with symlinks to all 11 skills for auto-discovery
- `.agents/plugins/marketplace.json` for Codex marketplace browsing
- Enriched `.codex-plugin/plugin.json` with full metadata
- Agent-neutral language across all SKILL.md files and references
- README presents Claude Code and Codex as equal install paths
- GitHub Action docs cover both `claude-code-action` and `codex-action`

### Project config and suppressions

- `.kicad-happy.json` project config: compliance target, derating profile, preferred suppliers, board class, rail overrides, BOM conventions
- Per-finding suppressions with reasons: `suppress: [{rule: "DC-001", ref: "C5", reason: "intentional"}]`
- Suppressed findings listed but marked, not hidden; active vs suppressed counts in summary
- Cascading config: project-level merges with user-level `~/.kicad-happy.json`
- Design intent auto-detection (hobby/consumer/industrial/medical/automotive/aerospace)
- IPC class detection from fab notes with class-aware DFM thresholds

### Report improvements

- **Per-finding confidence labels**: deterministic, datasheet-backed, heuristic, AI-inferred
- **Missing information section**: separates "I don't know" from "there's a problem"
- **Top-risk summary**: top 3 respin risks, bring-up blockers, and manufacturing blockers
- **Fabrication release gate**: 8-category "ready for fab?" check (routing, BOM, DFM, documentation, schematic-PCB consistency, Gerbers, thermal, EMC)

### Schematic-to-PCB cross-verification

New `cross_verify.py` with 7 cross-checks:
- Component reference bidirectional matching (orphans, missing, value mismatches, DNP conflicts)
- Differential pair length matching with per-protocol tolerances (USB 2mm, Ethernet 5mm, HDMI 1mm)
- Differential pair intra-pair skew check per protocol
- Power trace width assessment per regulator output rail
- Decoupling cap placement distance cross-check
- Bus routing advisory (signal lengths, SPI clock-to-data skew)
- Thermal via adequacy check

### Protocol electrical parameter checks

Complete coverage across all major protocols:
- **I2C**: Pull-up rise time validation, speed mode assessment, open-drain VOL compatibility, bus current budget
- **SPI**: Chip select conflict detection, device loading advisory, signal integrity (series termination)
- **UART**: TX/RX crossover verification, RS-232 transceiver detection with charge pump cap check
- **USB**: CC resistor validation (5.1k sink, source levels), D+/D- series resistors, VBUS capacitor sizing
- **Ethernet**: Bob Smith termination detection, magnetics/impedance advisory
- **HDMI**: 100ohm TMDS differential termination check
- **CAN**: 120ohm termination detection

### What-if enhancements

- **Sweep tables**: `R5=1k,2.2k,4.7k,10k` (comma list) and `R5=1k..100k:10` (log range) with markdown table output
- **Tolerance analysis**: `R5=4.7k+-5%` worst-case corner analysis (2^N combinations)
- **Fix suggestions**: `--fix voltage_dividers[0] --target 3.3` with E12/E24/E96 snapping
- **EMC impact preview**: `--emc` runs analyze_emc.py on patched JSON, diffs findings
- **PCB parasitic awareness**: `--pcb` with auto-discovery, trace R/L injection, footprint compatibility

### Detection schema

Centralized all per-detection-type metadata into `detection_schema.py`. Eliminated 4 hard-coded consumer-side registries (`_DERIVED_FIELDS`, `_recalc_derived`, `SIGNAL_REGISTRY`, `PRIMARY_METRIC`). Adding a new detection type is now 1 schema entry instead of 4-file edits.

### Diff analysis improvements

- **Cache integration**: `--analysis-dir` / `--run` for diffing runs from analysis cache
- **Multi-run trends**: `--trend N` shows metric evolution across last N runs
- **Change attribution**: "cutoff_hz changed because R5 went from 1k to 4.7k"
- **Regression detection**: flags new ERC warnings, removed protections, SPICE pass-to-fail, EMC score increases
- **Stable detection IDs**: hash-based `detection_id` on every signal detection for ref-renumbering resilience

### Analysis enrichment (complete)

Phase 1-4 enrichment across schematic, PCB, and EMC outputs:
- Bus electrical parameters: I2C speed mode, voltage, pull-up ohms; CAN termination; bus device dicts with controller field
- Power dissipation for switching regulators (buck/boost/buck-boost with efficiency estimates)
- Crystal load cap validation (target, error%, ok/marginal/out_of_spec)
- ESD device details on connector audit entries
- Decoupling proximity matrix in PCB output
- Switching loop area pre-computation in PCB output (via --schematic flag)
- EMC category summary pre-rollup

### Datasheet verification bridge

New `datasheet_verify.py` bridges extracted datasheet data with schematic analysis:
- Pin voltage abs_max violation (CRITICAL) and operating range exceeded (HIGH/MEDIUM)
- Missing required external components per datasheet pin specs
- Per-IC decoupling verification against application circuit recommendations
- Activates automatically when `datasheets/extracted/` cache exists

### Professional quick wins

- Fab notes completeness check (IPC class, surface finish, thickness, copper weight, material)
- Silkscreen completeness audit (revision, board name, ref visibility, connector labels, polarity)
- BOM lock verification (MPN coverage %, missing MPNs, generic values)
- Connector ground pin distribution (flag >4 signal pins per ground)
- Certification requirement identification (FCC/CE/IEC/UL from detected RF, battery, USB, Ethernet, high voltage)

### Analysis cache integration

All analyzers now support `--analysis-dir` for automatic cache management:
- Timestamped run folders with manifest tracking
- Copy-forward of unchanged outputs between runs
- Automatic new-run vs overwrite-current decision based on diff severity
- Pre-analysis datasheet sync prompt in skill workflow

### Sub-sheet detection (KH-228)

Detection rate improved from 34% to 99% using `.kicad_pro` stem matching as primary heuristic. Zero false positives on root schematics.

### Registry trust & CI

- GitHub Actions CI workflow (py_compile on Python 3.8 + 3.12)
- CODE_OF_CONDUCT.md (Contributor Covenant v2.1)
- Dependabot for GitHub Actions version tracking
- SECURITY.md moved to `.github/` for scanner compatibility
- Security architecture documentation in SKILL.md (Snyk W011 mitigation)

### Additional analysis improvements

- **Hierarchical context for sub-sheets**: automatic root schematic discovery and cross-sheet net resolution when analyzing individual sub-sheets
- **Sleep current estimation**: realistic vs worst-case analysis, per-rail breakdown with EN pin detection and GPIO state inference
- **Keepout zone analysis**: surface area calculation, ESD IC decoupling proximity checks, touch pad GND clearance verification
- **Lifecycle audit integration**: wired into analyzer via `--lifecycle` flag, queries 4 distributor APIs
- **Technical debt cleanup**: shared detector helpers (`detector_helpers.py`), hoisted 40+ deferred imports, consolidated duplicate calculations, tightened exception handling
- **`.kicad_pro` / `.kicad_dru` / library table parsing**: net classes, design rules, text variables from project files

### E-series standard values

- E12, E24, E96 decade tables in `kicad_utils.py`
- `snap_to_e_series()` function for component value snapping
- Used by what-if fix suggestions and EMC decoupling recommendations

### Bugfixes (25 issues)

KH-194 through KH-228 — most discovered via automated Layer 3 LLM batch review:

- KH-194: ESD audit "can" word boundary matching "scan"
- KH-195: USBPDSINK01 assertion update for PD controller detection
- KH-196: Bare capacitor values parsed as Farads in inrush/PDN calculations
- KH-197: Key matrix topology false positives (19 boards fixed)
- KH-198: LC filter reference collision in multi-project schematics
- KH-199/200: None rail names crash power_tree and narrative
- KH-204: power_rails uses UUID sheet paths instead of human-readable names
- KH-206: Global labels with different names merged into one net
- KH-207: Legacy KiCad 5 matrix decomposition producing wrong pin positions
- KH-208: Component type classification ignoring lib_id for Connector/Sensor/Motor/CircuitBreaker
- KH-209: Power rails with nnVn naming pattern (3V3, 12V0) classified as signal
- KH-210: SPI chip select detection missing CSN/NCS/SSEL patterns
- KH-211: pin_nets filtering out unnamed nets (hiding sub-sheet connections)
- KH-212: Bare capacitor values <1.0 parsed as Farads instead of microfarads
- KH-213: P-MOSFET detection missing PMOS/P-MOS/P-MOSFET keyword variants
- KH-214: INA2xx power monitors misclassified as opamp circuits
- KH-215: LM2576/LM2596 switching bucks classified as LDO
- KH-216: Multi-unit IC pin_nets showing wrong unit's pins
- KH-217: Crystal frequency parsing case-sensitive (kHZ/MHZ not matched)
- KH-218: Vref heuristic wrong for TPS62912, TPS73601, LM22676
- KH-219: Load switches classified as LDO topology
- KH-220: Active oscillators with custom lib symbols misclassified as connector
- KH-221: Opamp TIA feedback classified as compensator; false voltage dividers
- KH-222: Multi-unit symbol duplication in led_audit/sleep_current/usb_compliance
- KH-223: Power sequencing cascade not resolved (overbar pin name matching)
- KH-224: Multi-unit IC power_domains only showing one unit's rails
- KH-225: Charge pump LM2664 classified as LDO (now charge_pump topology)
- KH-226: NUCLEO dev board module classified as switching regulator
- KH-227: Logic gates misclassified as level_shifter_ic
- KH-228: detect_sub_sheet only identifying 34% of sub-sheets
- AP63357/AP632xx Vref entries added (0.8V)
- EMC IO-001 jumper false positive exclusion

### Validation

- 681,000+ schematic + 517,000+ EMC regression assertions at 100% pass rate
- 5,829 repos, 40+ schematic detectors, 42 EMC rules, 17 SPICE subcircuit types
- 400+ unit tests across 22 test files
- 0 open issues at release
- 102 commits since v1.1.0

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
