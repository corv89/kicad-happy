---
name: kicad
description: Analyze KiCad EDA projects and PDF schematics — schematics, PCB layouts, Gerbers, footprints, symbols, design rules, netlists. Review designs for bugs, suggest improvements, extract BOMs, trace nets, cross-reference schematic to PCB, verify DRC/ERC, check DFM, analyze power trees and regulator circuits. Also analyze PDF schematics from dev boards, reference designs, eval kits, and datasheets — extract subcircuits, component values, and connectivity for incorporation into KiCad projects. Supports KiCad 5–9. Use whenever the user mentions KiCad files (.kicad_sch, .kicad_pcb, .kicad_pro), PCB design review, schematic analysis, PDF schematics, reference designs, Gerber files, DRC/ERC, netlist issues, BOM extraction, signal tracing, power budget, design for manufacturing, or wants to understand, debug, compare, or review any hardware design. Also use when the user says things like "check my board", "review before fab", "what's wrong with my schematic", "is this design ready to order", "check my power supply", "verify this motor driver circuit", or asks about any electronics/PCB design topic.
---

# KiCad Project Analysis Skill

## Related Skills

| Skill | Purpose |
|-------|---------|
| `bom` | BOM extraction, enrichment, ordering, and export workflows |
| `digikey` | Search DigiKey for parts (prototype sourcing) |
| `mouser` | Search Mouser for parts (secondary prototype source) |
| `lcsc` | Search LCSC for parts (production sourcing, JLCPCB) |
| `jlcpcb` | PCB fabrication & assembly ordering |
| `pcbway` | Alternative PCB fabrication & assembly |

**Handoff guidance:** Use this skill to parse schematics/PCBs and extract structured data. Hand off to `bom` for BOM enrichment, pricing, and ordering. Hand off to `digikey`/`mouser`/`lcsc` for part searches and datasheet fetching. Hand off to `jlcpcb`/`pcbway` for fabrication ordering and DFM rule validation.

## PDF Schematic Analysis

This skill also handles **PDF schematics** — reference designs, dev board schematics, eval board docs, application notes, and datasheet typical-application circuits. Common use cases:

- Analyze a manufacturer's reference design to understand the circuit
- Extract a subcircuit (power supply, USB interface, sensor front-end) to incorporate into your own KiCad design
- Compare a PDF reference design against your own schematic
- Extract a full BOM from a PDF schematic
- Validate component values in a PDF against current datasheets

**Workflow:** Read the PDF pages visually → identify components and connections → extract structured data → translate to KiCad symbols and nets → validate against datasheets.

For the full methodology — component extraction, notation conventions, net mapping, subcircuit extraction, KiCad translation, and validation — read `references/pdf-schematic-extraction.md`.

For deep validation of extracted circuits against datasheets (verifying values, checking patterns, detecting errors), use the methodology in `references/schematic-analysis.md`.

## Analysis Scripts

This skill includes Python scripts that extract comprehensive structured JSON from KiCad files in a single pass. Run these first, then reason about the output — avoid generating ad-hoc parsing scripts.

In all commands below, `<skill-path>` refers to this skill's base directory (shown at the top of this file when loaded).

### Schematic Analyzer
```bash
python3 <skill-path>/scripts/analyze_schematic.py <file.kicad_sch>
```
Outputs structured JSON (~60-220KB depending on board complexity) with:
- **Components & BOM**: inventory with reference, value, footprint, lib_id, type classification, MPN, datasheet; deduplicated BOM with quantities
- **Nets**: full connectivity map with pin-to-net mapping, wire counts, no-connects
- **Signal analysis** (automated subcircuit detection):
  - Power regulators — LDO/switching/inverting topology, Vout estimation via datasheet-verified Vref lookup (~60 families) with heuristic fallback, `vref_source` and `vout_net_mismatch` fields
  - Voltage dividers, RC/LC filters (cutoff frequency), feedback networks, crystal circuits (load cap analysis)
  - Op-amp circuits (configuration, gain), transistor circuits (net-name-aware load classification: motor/heater/fan/solenoid/valve/pump/relay/speaker/buzzer/lamp)
  - Bridge circuits (H-bridge, 3-phase, cross-sheet detection), protection devices (ESD/TVS), current sense, decoupling analysis
  - Domain-specific: RF chains, BMS, Ethernet, memory interfaces, key matrices, isolation barriers
- **Power analysis**: PDN impedance (1kHz–1GHz with MLCC parasitics), power budget, power sequencing (EN/PG chains), sleep current audit (resistive paths + regulator Iq with EN detection), voltage derating, inrush estimation
- **Design analysis**: ERC warnings, power domains, bus detection (I2C/SPI/UART/CAN with COPI/CIPO/SDI/SDO), differential pairs (suffix-pair matching for USB/LVDS/Ethernet/HDMI/MIPI/PCIe/SATA/CAN/RS-485), cross-domain signals (voltage equivalence), BOM optimization, test coverage, assembly complexity, USB compliance
- **Quality checks**: annotation completeness, label validation, PWR_FLAG audit, footprint filter validation, sourcing audit, property pattern audit
- **Structural**: MCU alternate pin summary, ground domain classification, bus topology, wire geometry, spatial clustering, pin coverage, hierarchical label validation

Supports modern `.kicad_sch` (KiCad 6+) and legacy `.sch` (KiCad 4/5). Hierarchical designs parsed recursively.

**Legacy format limitations:** For KiCad 5 legacy `.sch` files, the analyzer provides **component and net extraction only** — no pin-to-net mapping, no signal analysis, no subcircuit detection. When signal analysis is missing from the output, use supplementary data sources to fill the gaps — see the section below.

### Supplementary Data for Legacy Designs

When `analyze_schematic.py` returns incomplete data (typically legacy `.sch` format — missing pin-to-net mapping, signal analysis, and subcircuit detection), use additional project files to recover full analysis capability. The most valuable source is the `.net` netlist file, which provides explicit pin-to-net mapping that closes the signal analysis gap entirely.

For detailed parsing instructions, data recovery workflows, and a priority matrix of supplementary sources (netlist, cache library, PCB cross-reference, PDF exports), read `references/supplementary-data-sources.md`.

**Mandatory verification after every run:** The analyzer can silently produce incorrect results — wrong voltage estimates from Vref assumptions, missing MPNs due to non-standard property names, PWR_FLAGs mapped to wrong nets. These don't cause script errors but lead to wrong conclusions. After every run, read the raw `.kicad_sch` file and perform these checks:

1. **Component count** — grep for placed `(symbol (lib_id ...))` blocks, subtract power symbols. Must match analyzer count exactly.
2. **IC spot-check** — verify 3-5 key ICs: lib_id, value, footprint, pin count against raw file.
3. **Net trace** — pick 2-3 important nets and trace them manually through wires/labels. Verify the analyzer's pin list is complete.
4. **Regulator Vout** — for each detected regulator, check the `vref_source` field. When `"lookup"`, the Vref comes from a datasheet-verified table (~60 families). When `"heuristic"`, the Vref is a guess — verify against the actual part's datasheet. The `vout_net_mismatch` field flags cases where the estimated Vout differs >15% from the output rail name voltage.
5. **Hierarchical connectivity** — on multi-sheet designs, verify that root-level wires between sheet pin stubs are reflected in the net data. Sheet pin stubs are now extracted as hierarchical labels, but complex wiring topologies at the root level may still have edge cases.

See `references/schematic-analysis.md` Step 2 for the full verification checklist. If the script fails or returns unexpected results, see `references/manual-schematic-parsing.md` for the complete fallback methodology.

### PCB Layout Analyzer
```bash
python3 <skill-path>/scripts/analyze_pcb.py <file.kicad_pcb>
python3 <skill-path>/scripts/analyze_pcb.py <file.kicad_pcb> --proximity  # add crosstalk analysis
```
Outputs structured JSON (~50-300KB depending on board complexity) with:
- **Core**: footprint inventory (pads, courtyards, net assignments, extended attrs, schematic cross-reference), track/via statistics, zone summaries, board outline/dimensions, routing completeness
- **Via analysis**: type breakdown (through/blind/micro), annular ring checks, via-in-pad detection, BGA/QFN fanout patterns, current capacity, stitching via identification, tenting
- **Signal integrity**: per-net trace length, layer transition tracking (ground return paths), trace proximity/crosstalk (with `--proximity`)
- **Power & thermal**: current capacity per net, power net routing summary, ground domain identification (AGND/DGND), zone stitching via density, thermal pad detection and via counting
- **Manufacturing**: placement analysis (courtyard overlaps, edge clearance), decoupling cap distances, DFM scoring (JLCPCB standard/advanced tier), tombstoning risk (0201/0402 thermal asymmetry), thermal pad via adequacy, silkscreen documentation audit

Add `--full` to include individual track/via coordinates. Supports KiCad 5 legacy format.

**Verify after every run:** Spot-check footprint count against the raw `.kicad_pcb` file, verify 2-3 key footprint pad-net assignments, and confirm board outline dimensions match. If the script fails or returns unexpected results, see `references/manual-pcb-parsing.md` for the complete fallback methodology.

### Gerber & Drill Analyzer
```bash
python3 <skill-path>/scripts/analyze_gerbers.py <gerber_directory/>
```
Outputs: layer identification (X2 attributes), component/net/pin mapping (KiCad 6+ TO attributes), aperture function classification, trace width distribution, board dimensions, drill classification (via/component/mounting), layer completeness, alignment verification, pad type summary (SMD/THT ratio). Add `--full` for complete pin-to-net connectivity dump. ~10KB JSON.

If the script fails or returns unexpected results, see `references/manual-gerber-parsing.md` for the complete fallback methodology for parsing raw Gerber/Excellon files directly.

All scripts output JSON to stdout. Use `--output file.json` to write to a file, `--compact` for single-line JSON.

**Workflow:** When analyzing a KiCad project, scan the project directory for all available file types and run every applicable analyzer — not just the one the user mentioned. A complete analysis uses all the data available:

1. **Scan the project directory** for `.kicad_sch`, `.kicad_pcb`, `.kicad_pro`, gerber directories, and `.net`/`.xml` netlist files
2. **Run all applicable scripts** — if the schematic exists, run `analyze_schematic.py`. If the PCB exists, run `analyze_pcb.py`. If gerbers exist, run `analyze_gerbers.py`. Run them in parallel when possible.
3. **Read the `.kicad_pro`** project file directly (it's JSON) for design rules, net classes, and DRC/ERC settings
4. **Cross-reference outputs** between schematic and PCB (see section below) — this catches the most dangerous bugs (swapped pins, missing nets, footprint mismatches)
5. **Verify each output** against the raw files before using the data in your report
6. **Produce a unified report** covering schematic analysis, PCB layout analysis, and cross-reference findings. See `references/report-generation.md` for the report template.

The more data sources you combine, the more confident the analysis. A schematic-only review misses layout issues; a PCB-only review misses design intent. Always use everything available.

### Schematic + PCB Cross-Reference

When both a schematic and PCB file exist for a project, run both analyzers and cross-reference the outputs for a more thorough analysis:

1. **Component count**: Compare schematic component count (excluding power symbols) against PCB footprint count. Mismatches indicate unplaced or orphaned components.
2. **Net consistency**: Verify schematic net names appear in the PCB net declarations. Missing nets may indicate incomplete routing or schematic changes not yet pushed to the PCB.
3. **Pin-net assignments**: For critical ICs, compare schematic pin-to-net mapping against PCB pad-to-net mapping. Mismatches reveal swapped pins or library errors — these are the most dangerous bugs because they pass DRC/ERC but produce non-functional boards.
4. **Footprint match**: Verify schematic `(property "Footprint" ...)` matches the actual footprint used on the PCB. Package mismatches (e.g., SOT-23 vs SOT-23-5) cause assembly failures.
5. **DNP consistency**: Components marked DNP in the schematic should not have routing on the PCB (or should be flagged for review).
6. **Value/MPN consistency**: Check that component values and MPNs match between schematic and PCB properties.

The PCB analyzer's `sch_path`, `sch_sheetname`, and `sch_sheetfile` fields in each footprint link back to the schematic, enabling automated cross-referencing.

## Reference Files

Detailed methodology and format documentation lives in reference files. Read these as needed — they provide deep-dive content beyond what the scripts output automatically.

| Reference | Lines | When to Read |
|-----------|-------|-------------|
| `schematic-analysis.md` | 1085 | Deep schematic review: datasheet validation, design patterns, error taxonomy, tolerance stacking, GPIO audit, motor control, battery life, supply chain |
| `pcb-layout-analysis.md` | 393 | Advanced PCB: impedance calculations, differential pairs, return paths, copper balance, edge clearance, custom analysis scripts |
| `file-formats.md` | 361 | Manual file inspection: S-expression structure, field-by-field docs for all KiCad file types, version detection |
| `gerber-parsing.md` | 729 | Gerber/Excellon format details, X2 attributes, analysis techniques |
| `pdf-schematic-extraction.md` | 315 | PDF schematic analysis: extraction workflow, notation conventions, KiCad translation |
| `supplementary-data-sources.md` | 301 | Legacy KiCad 5 data recovery: netlist parsing, cache library, PCB cross-reference |
| `net-tracing.md` | 109 | Manual net tracing: coordinate math, Y-axis inversion, rotation transforms |
| `manual-schematic-parsing.md` | 285 | Fallback when schematic script fails |
| `manual-pcb-parsing.md` | 457 | Fallback when PCB script fails |
| `manual-gerber-parsing.md` | 621 | Fallback when Gerber script fails |
| `report-generation.md` | 436 | Report template with all analysis dimensions, analyzer output field reference (schematic/PCB/gerber), severity definitions, writing principles, domain-specific focus areas, known analyzer limitations |

For script internals, data structures, signal analysis patterns, and batch test suite documentation, see `scripts/README.md`.

## File Types Quick Reference

| Extension | Format | Purpose |
|---|---|---|
| `.kicad_pro` | JSON | Project settings, net classes, DRC/ERC severity, BOM fields |
| `.kicad_sch` | S-expr | Schematic sheet (symbols, wires, labels, hierarchy) |
| `.kicad_pcb` | S-expr | PCB layout (footprints, tracks, vias, zones, board outline) |
| `.kicad_sym` | S-expr | Symbol library (schematic symbols with pins, graphics) |
| `.kicad_mod` | S-expr | Single footprint (in `.pretty/` directory) |
| `.kicad_dru` | Custom | Custom design rules (DRC constraints) |
| `fp-lib-table` / `sym-lib-table` | S-expr | Library path tables |
| `.sch` / `.lib` / `.dcm` | Legacy | KiCad 5 schematic, symbol library, descriptions |
| `.net` / `.xml` | S-expr/XML | Netlist export, BOM export |
| `.gbr` / `.g*` / `.drl` | Gerber/Excellon | Manufacturing files (copper, mask, silk, outline, drill) |

For version detection and detailed field-by-field format documentation, read `references/file-formats.md`.

## Analysis Strategies

### Deep Schematic Analysis

For a thorough datasheet-driven schematic review — identifying subcircuits, fetching datasheets, validating component values against manufacturer recommendations, comparing against common design patterns, detecting errors, and suggesting improvements — read `references/schematic-analysis.md`. Use this reference whenever the user asks to review, validate, or analyze a schematic in depth.

**Fetching datasheets**: When the analysis requires datasheet data, use the DigiKey API as the preferred source (see the `digikey` skill) — it returns direct PDF URLs via the `DatasheetUrl` field without web scraping. Search by MPN from the schematic's component properties. Fall back to WebSearch only for parts not on DigiKey.

### Deep PCB Analysis

For advanced layout analysis beyond what the PCB analyzer script provides — impedance calculations from stackup parameters, DRC rule authoring, power electronics design review techniques, differential pair validation, return path analysis, copper balance assessment, board edge clearance rules, and manual script-writing patterns — read `references/pcb-layout-analysis.md`.

Most routine PCB analysis (via types, annular ring, placement, connectivity, thermal vias, current capacity, signal integrity, DFM scoring, tombstoning risk, thermal pad vias) is handled automatically by `analyze_pcb.py`. Use the reference for deeper manual investigation.

### Quick Review Checklists

**Schematic** — verify: decoupling caps on every IC VCC/GND pair, I2C pull-ups, reset pin circuits, unconnected pins have no-connect markers, consistent net naming across sheets, ESD protection on external connectors, power sequencing (EN/PG), adequate bulk capacitance.

**PCB** — verify: power trace widths for current (IPC-2221), via current capacity, creepage/clearance for high voltage, decoupling cap proximity to IC power pins, continuous ground plane (no splits under signals), controlled impedance traces (USB/DDR), board outline closed polygon, silkscreen readability.

**Common bugs**: swapped pins (library errors), missing nets (schematic→PCB sync), wrong footprint (SOT-23 vs SOT-23-5), floating digital inputs, missing bulk caps, reversed polarity, incorrect feedback divider, wrong crystal load caps, USB impedance mismatch, QFN thermal pad missing vias.

### Report Generation

When producing a design review report, read `references/report-generation.md` for the standard report template, severity definitions, writing principles, and domain-specific focus areas. The report format covers: overview, component summary, power tree, analyzer verification (spot-checks), signal/power/design analysis review, quality & manufacturing, prioritized issues table, positive findings, and known analyzer gaps. Always cross-reference analyzer output against the raw schematic before reporting findings.

### Design Comparison
When comparing two designs, diff: component counts/types, net classes/design rules, track widths/via sizes, board dimensions/layer count, power supply topology, KiCad version differences.
