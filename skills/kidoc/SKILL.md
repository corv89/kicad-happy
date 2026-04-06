---
name: kidoc
description: >-
  Generate professional engineering documentation from KiCad projects —
  Hardware Design Descriptions (HDD), CE Technical Files, Interface Control
  Documents (ICD), Design Review Packages, and Manufacturing Transfer
  Packages. Auto-runs schematic, PCB, EMC, and thermal analyses; renders
  publication-quality schematic and PCB SVGs with subsystem cropping, focus
  dimming, net highlighting, and pin-net annotation; generates power tree,
  bus topology, and architecture block diagrams. Produces styled PDF output
  with cover pages, table of contents, and vector SVG embedding. Markdown is
  the source of truth — human-editable, version-controllable, survives
  regeneration. Use this skill when the user says "generate documentation",
  "create report", "hardware design document", "HDD", "CE technical file",
  "design review package", "ICD", "engineering documentation", "render
  schematic to SVG", "render layout", "generate block diagram", "create
  manufacturing package", "generate PDF", "power analysis", "EMC report",
  "schematic review", or "custom report".
---

# kidoc — Engineering Documentation Skill

Generate professional engineering documentation from KiCad project files.

## Quick Start

One command generates the full scaffold — analyses, diagrams, renders, and markdown are all produced automatically:

```bash
python3 skills/kidoc/scripts/kidoc_scaffold.py \
  --project-dir /path/to/kicad/project \
  --type hdd \
  --output reports/HDD.md
```

This auto-detects `.kicad_sch` and `.kicad_pcb` files, runs schematic/PCB/EMC/thermal analyses, generates block diagrams and schematic SVG renders, and produces a structured markdown scaffold with pre-filled data tables and narrative placeholders.

To produce a PDF:

```bash
python3 skills/kidoc/scripts/kidoc_generate.py \
  --project-dir /path/to/kicad/project \
  --doc reports/HDD.md \
  --format pdf
```

Creates `reports/.venv/` automatically on first run (PDF/DOCX/ODT only — HTML is zero-dep).

## Workflow

1. **Generate scaffold** — `kidoc_scaffold.py` auto-runs all available analyses, renders schematics, generates diagrams, and writes the markdown scaffold.
2. **Fill narratives** — Claude reads the scaffold and writes engineering prose for each `<!-- NARRATIVE: section_name -->` placeholder. The engineer reviews and edits.
3. **Regenerate** — On re-run, data sections between `<!-- AUTO-START -->` / `<!-- AUTO-END -->` markers update from fresh analysis; user-written narrative content is preserved.
4. **Render output** — `kidoc_generate.py` produces PDF, HTML, DOCX, or ODT.

## Document Types

| Type | Name | Key Sections |
|------|------|-------------|
| `hdd` | Hardware Design Description | System overview, power, signals, analog, thermal, EMC, PCB, mechanical, BOM, test, compliance |
| `ce_technical_file` | CE Technical File | Product ID, essential requirements, harmonized standards, risk assessment, Declaration of Conformity |
| `design_review` | Design Review Package | Review summary (cross-analyzer scores), findings, action items |
| `icd` | Interface Control Document | Interface list, per-connector pinout details, electrical characteristics |
| `manufacturing` | Manufacturing Transfer Package | Assembly overview, PCB fab notes, assembly instructions, test procedures |
| `schematic_review` | Schematic Review Report | System overview, power, signals, analog, BOM, schematic appendix |
| `power_analysis` | Power Analysis Report | Power design, thermal, EMC, BOM |
| `emc_report` | EMC Pre-Compliance Report | EMC analysis, compliance, schematic appendix |

## Custom Reports

Use `--spec` to generate reports with arbitrary section ordering:

```bash
python3 skills/kidoc/scripts/kidoc_scaffold.py \
  --project-dir . --spec my-report.json --output reports/custom.md
```

Spec format (JSON):

```json
{
  "type": "custom",
  "title": "USB Interface Analysis",
  "sections": [
    {"id": "front_matter", "type": "front_matter"},
    {"id": "signal_interfaces", "type": "signal_interfaces"},
    {"id": "bom", "type": "bom_summary"}
  ]
}
```

Each section's `type` must match a known section type (same names used in the document types table above). The `id` field is a unique key for that section instance.

To see the full default spec for any built-in type:

```bash
python3 skills/kidoc/scripts/kidoc_spec.py --expand hdd
python3 skills/kidoc/scripts/kidoc_spec.py --list
```

The `--spec` flag also works with `kidoc_generate.py` (uses the spec title as fallback project name).

## Schematic Rendering

```bash
# Full sheet render (root + all sub-sheets)
python3 skills/kidoc/scripts/kidoc_render.py design.kicad_sch --output renders/

# Crop to subsystem bounding box
python3 skills/kidoc/scripts/kidoc_render.py design.kicad_sch --crop R1,R2,C1 --output crop.svg

# Dim everything except focused components (15% opacity)
python3 skills/kidoc/scripts/kidoc_render.py design.kicad_sch --focus R1,R2 --output focus.svg

# Highlight specific nets with color tracing via BFS
python3 skills/kidoc/scripts/kidoc_render.py design.kicad_sch --highlight-nets VCC,GND --output nets.svg

# Annotate pin-level net names at pin tips
python3 skills/kidoc/scripts/kidoc_render.py design.kicad_sch --pin-nets pin_nets.json --output annotated.svg
```

Options compose: `--crop`, `--focus`, `--highlight-nets`, and `--pin-nets` can all be used together. Recursive sub-sheet rendering is automatic.

## PCB Rendering

```bash
python3 skills/kidoc/scripts/pcb_render.py board.kicad_pcb --output renders/ --preset assembly-front
python3 skills/kidoc/scripts/pcb_render.py board.kicad_pcb --output renders/ --preset routing-all \
    --highlight-nets GND,+3V3
python3 skills/kidoc/scripts/pcb_render.py board.kicad_pcb --output renders/ --preset power \
    --crop-refs U1,R1
```

Layer presets:

| Preset | Shows |
|--------|-------|
| `assembly-front` | Front silk, fab, pads, outline |
| `assembly-back` | Back silk, fab, pads, outline (mirrored) |
| `routing-front` | Front copper, pads, vias, outline |
| `routing-back` | Back copper, pads, vias, outline |
| `routing-all` | All copper layers, pads, vias, zones |
| `power` | Power planes, vias, zone outlines |

Additional options: `--highlight-nets`, `--crop-refs`, `--crop x,y,w,h`, `--mirror`, `--overlay annotations.json` (callout boxes with leader lines).

## Block Diagrams

```bash
python3 skills/kidoc/scripts/kidoc_diagrams.py --analysis schematic.json --all --output diagrams/
python3 skills/kidoc/scripts/kidoc_diagrams.py --analysis schematic.json --power-tree --output diagrams/
python3 skills/kidoc/scripts/kidoc_diagrams.py --analysis schematic.json --bus-topology --output diagrams/
python3 skills/kidoc/scripts/kidoc_diagrams.py --analysis schematic.json --architecture --output diagrams/
```

Generated from schematic analysis JSON. Power trees show regulator topology with inductor values, capacitor summaries, and output voltages.

## Output Formats

| Format | SVG Handling | Dependencies |
|--------|-------------|------|
| **Markdown** | Image references | Zero-dep |
| **HTML** | Inlined as vector | Zero-dep |
| **PDF** | Vector via svglib, custom converter fallback, raster fallback | Venv (`reports/.venv/`) |
| **DOCX** | Rasterized to 300 DPI PNG | Venv |
| **ODT** | Rasterized to 300 DPI PNG | Venv |

PDF output includes a styled cover page, table of contents, formatted tables with alternating rows, and vector SVG diagrams.

## Configuration

Report settings live in `.kicad-happy.json` under the `"reports"` key. Config files cascade: `~/.kicad-happy.json` (user-level defaults, e.g. company branding) merges with project-level config.

```jsonc
{
  "project": {
    "name": "Widget Board",
    "number": "HW-2024-042",
    "revision": "1.2",
    "company": "Acme Electronics",
    "author": "Jane Smith",
    "market": "eu"
  },
  "reports": {
    "classification": "Company Confidential",
    "documents": [
      {"type": "hdd", "output": "HDD-{project}-{rev}", "formats": ["pdf", "docx"]}
    ],
    "branding": {
      "logo": "templates/logo.png",
      "header_left": "{company}",
      "header_right": "{number} Rev {rev}"
    }
  }
}
```

## Requirements

- **Python 3.9+** with `python3-venv` (for PDF/DOCX/ODT generation)
- **KiCad schematic file** (`.kicad_sch`, KiCad 6+) — for SVG rendering
- **Optional:** Analysis JSONs are auto-generated from `.kicad_sch`/`.kicad_pcb`; pre-generated JSONs in `reports/cache/analysis/` are used if present. Generated figures (diagrams, schematic SVGs) are placed in `reports/figures/` for git tracking

## Limitations

- Schematic and PCB renderers support KiCad 6+ formats only (`.kicad_sch`, `.kicad_pcb`)
- Narrative sections require Claude or manual authoring — the scaffold provides structure and data, not prose
- SPICE simulation results require manual simulation setup (not auto-run by scaffold)
- PDF vector SVG embedding uses svglib when available; falls back to raster if svglib cannot parse a particular SVG

## Related Skills

| Skill | Relationship |
|-------|-------------|
| `kicad` | Produces schematic/PCB/thermal analysis JSON consumed by scaffolds |
| `emc` | Produces EMC analysis JSON for EMC sections |
| `spice` | SPICE simulation results appear in analog design sections |
| `bom` | BOM data appears in BOM summary sections |

Run the `kicad` skill's analyzers first, then `emc` and `spice` if available. The scaffold auto-runs `kicad` and `emc` analyses when source files are present, so manual pre-analysis is only needed for SPICE.
