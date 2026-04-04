---
name: kidoc
description: >-
  Generate engineering documentation from KiCad projects — Hardware Design
  Descriptions (HDD), CE Technical Files, Interface Control Documents (ICD),
  Design Review Packages, and Manufacturing Transfer Packages. Renders
  publication-quality schematic SVGs with subsystem cropping and analysis
  overlays, block diagrams (power trees, bus topologies, architecture),
  and structured markdown scaffolds with pre-filled tables, images, and
  narrative placeholders. Optional PDF/DOCX output via a project-local
  virtual environment. Markdown is the source of truth — human-editable,
  version-controllable, and survives regeneration. Use this skill when
  the user says "generate documentation", "create report", "hardware
  design document", "HDD", "CE technical file", "design review package",
  "ICD", "engineering documentation", "render schematic to SVG",
  "generate block diagram", "create manufacturing package", or wants
  publication-quality documents from their KiCad project.
---

# kidoc — Engineering Documentation Skill

Generate professional engineering documentation from KiCad project files and kicad-happy analysis outputs.

## Related Skills

| Skill | Purpose |
|-------|---------|
| `kicad` | Schematic/PCB/Gerber analysis — produces the analyzer JSON this skill consumes |
| `emc` | EMC pre-compliance analysis — findings appear in EMC sections |
| `spice` | SPICE simulation — results appear in analog design sections |
| `bom` | BOM management — BOM data appears in BOM summary sections |

**Handoff guidance:** Run the `kicad` skill's analyzers first, then `emc` and `spice` if available. This skill consumes their JSON output to populate document sections.

## Requirements

- **Python 3.8+** — stdlib only for schematic rendering and scaffold generation
- **KiCad schematic file** (`.kicad_sch`) — for SVG rendering
- **Analysis JSON files** — from `analyze_schematic.py`, `analyze_pcb.py`, `analyze_emc.py`, etc.
- **Optional:** `reportlab`, `svglib`, `python-docx` for PDF/DOCX output (auto-installed in project-local venv)

## Workflow

### Step 1: Render schematics to SVG

```bash
python3 skills/kidoc/scripts/kidoc_render.py <file.kicad_sch> --output reports/cache/schematic/
```

Options:
- `--crop R1,R2,C1` — crop SVG to bounding box around specified components
- `--overlay analysis.json` — annotate with voltage/frequency values from analysis
- `--padding 5.0` — padding around crop region (mm)

### Step 2: Generate markdown scaffold (Phase 3)

```bash
python3 skills/kidoc/scripts/kidoc_scaffold.py --project-dir . --type hdd --output reports/HDD.md
```

### Step 3: Fill narratives

Claude or the user fills `<!-- NARRATIVE: section_name -->` placeholders in the markdown.

### Step 4: Render to PDF/DOCX (Phase 4)

```bash
python3 skills/kidoc/scripts/kidoc_generate.py --project-dir . --format pdf
```

## Output Formats

- **SVG** — publication-quality vector schematics, block diagrams, charts
- **Markdown** — source of truth, human-editable, version-controllable
- **PDF** — via ReportLab + svglib (vector SVG embedding)
- **DOCX** — via python-docx (SVGs rasterized to 300 DPI PNG)
- **ODT** — via odfpy (OpenDocument, for LibreOffice/OpenOffice)

## Document Types

| Type | Name | Sections |
|------|------|----------|
| `hdd` | Hardware Design Description | System overview, power, signals, analog, thermal, EMC, PCB, BOM, compliance |
| `ce_technical_file` | CE Technical File | Essential requirements, harmonized standards, risk assessment, DoC |
| `design_review` | Design Review Package | Findings summary, critical issues, action items |
| `icd` | Interface Control Document | Connector details, signal definitions, electrical characteristics |
| `manufacturing` | Manufacturing Transfer Package | Fab notes, assembly instructions, test procedures |

## Configuration

Report settings live in `.kicad-happy.json` under the `"reports"` key. See `TODO-kidoc-skill.md` for the full schema.

## Limitations

- Schematic SVG renderer supports `.kicad_sch` (KiCad 6+) only — no legacy `.sch`
- PCB 3D renders require kicad-cli (auto-detected, gracefully skipped if unavailable)
- PDF/DOCX generation requires pip-installable dependencies (auto-managed in `reports/.venv/`)
- Narrative sections require Claude or manual authoring — the scaffold provides structure and data, not prose
