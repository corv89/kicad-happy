---
name: kidoc
description: >-
  Generate engineering documentation from KiCad projects — Hardware Design
  Descriptions (HDD), CE Technical Files, Interface Control Documents (ICD),
  Design Review Packages, and Manufacturing Transfer Packages. Renders
  publication-quality schematic SVGs with subsystem cropping, analysis
  overlays, and hierarchical sub-sheet support. Generates block diagrams
  (power trees, bus topologies, architecture) and structured markdown
  scaffolds with pre-filled tables, images, and narrative placeholders.
  Optional PDF/DOCX/ODT output via a project-local virtual environment
  (no global Python changes, no system C libraries). Markdown is the source
  of truth — human-editable, version-controllable, and survives regeneration.
  Use this skill when the user says "generate documentation", "create report",
  "hardware design document", "HDD", "CE technical file", "design review
  package", "ICD", "engineering documentation", "render schematic to SVG",
  "generate block diagram", "create manufacturing package", or wants
  publication-quality documents from their KiCad project.
---

# kidoc — Engineering Documentation Skill

Generate professional engineering documentation from KiCad project files and kicad-happy analysis outputs.

**Architecture:** Markdown-first pipeline. Scripts generate scaffolds with tables/images/data, Claude or the user fills narrative prose, optional PDF/DOCX/ODT rendering via project-local venv. All rendering and scaffold generation is zero-dependency (Python stdlib only). Document rendering deps (ReportLab, python-docx, odfpy, Pillow) install automatically in `reports/.venv/` — never touches global Python.

## Related Skills

| Skill | Purpose |
|-------|---------|
| `kicad` | Schematic/PCB/Gerber analysis — produces the analyzer JSON this skill consumes |
| `emc` | EMC pre-compliance analysis — findings appear in EMC sections |
| `spice` | SPICE simulation — results appear in analog design sections |
| `bom` | BOM management — BOM data appears in BOM summary sections |

**Handoff guidance:** Run the `kicad` skill's analyzers first, then `emc` and `spice` if available. This skill consumes their JSON output to populate document sections.

## Requirements

- **Python 3.9+** with `python3-venv` (for PDF/DOCX/ODT generation)
- **KiCad schematic file** (`.kicad_sch`, KiCad 6+) — for SVG rendering
- **Analysis JSON files** — from `analyze_schematic.py`, `analyze_pcb.py`, `analyze_emc.py`, etc.
- **Optional:** kicad-cli for PCB layer views and 3D renders (auto-detected, gracefully skipped)

## Workflow

### Step 1: Run analysis

```bash
python3 skills/kicad/scripts/analyze_schematic.py design.kicad_sch --output reports/cache/analysis/schematic.json
# Optional:
python3 skills/kicad/scripts/analyze_pcb.py design.kicad_pcb --output reports/cache/analysis/pcb.json
python3 skills/emc/scripts/analyze_emc.py --schematic reports/cache/analysis/schematic.json --output reports/cache/analysis/emc.json
```

### Step 2: Render schematics to SVG

```bash
python3 skills/kidoc/scripts/kidoc_render.py design.kicad_sch --output reports/cache/schematic/
```

Renders root sheet + all hierarchical sub-sheets. Options:
- `--crop R1,R2,C1` — crop SVG to bounding box around specified components
- `--overlay analysis.json` — annotate with voltage/frequency values from analysis

### Step 3: Generate block diagrams

```bash
python3 skills/kidoc/scripts/kidoc_diagrams.py --analysis reports/cache/analysis/schematic.json --all --output reports/cache/diagrams/
```

Generates power tree, bus topology, and architecture block diagrams.

### Step 4: Generate markdown scaffold

```bash
python3 skills/kidoc/scripts/kidoc_scaffold.py --project-dir . --type hdd --output reports/HDD.md
```

Document types: `hdd`, `ce_technical_file`, `design_review`, `icd`, `manufacturing`.

### Step 5: Fill narratives

Claude or the user fills `<!-- NARRATIVE: section_name -->` placeholders in the markdown. Auto-generated content between `<!-- AUTO-START -->` / `<!-- AUTO-END -->` markers is regenerated on re-run; user-written content outside markers is preserved.

### Step 6: Render to PDF/DOCX/ODT

```bash
python3 skills/kidoc/scripts/kidoc_generate.py --project-dir . --format all
```

Creates `reports/.venv/` automatically on first run (not needed for HTML). Formats: `pdf` (default), `html`, `docx`, `odt`, `all`.

## Output Formats

| Format | Library | SVG Handling | Deps |
|--------|---------|-------------|------|
| **SVG** | stdlib xml.etree | Native vector | Zero-dep |
| **Markdown** | stdlib | Image references | Zero-dep |
| **HTML** | stdlib | Inlined as vector (best quality) | Zero-dep |
| **PDF** | ReportLab | Rasterized to 300 DPI PNG via Pillow | Venv |
| **DOCX** | python-docx | Rasterized to 300 DPI PNG via Pillow | Venv |
| **ODT** | odfpy | Rasterized to 300 DPI PNG via Pillow | Venv |

## Document Types

| Type | Name | Key Sections |
|------|------|-------------|
| `hdd` | Hardware Design Description | System overview, power, signals, analog, thermal, EMC, PCB, mechanical, BOM, test, compliance |
| `ce_technical_file` | CE Technical File | Product ID, essential requirements, harmonized standards, risk assessment, Declaration of Conformity |
| `design_review` | Design Review Package | Review summary (cross-analyzer scores), findings, action items |
| `icd` | Interface Control Document | Interface list, per-connector pinout details, electrical characteristics |
| `manufacturing` | Manufacturing Transfer Package | Assembly overview, PCB fab notes, assembly instructions, test procedures |

## Configuration

Report settings live in `.kicad-happy.json` under the `"reports"` key. Config files cascade: `~/.kicad-happy.json` (user-level defaults, e.g. company branding) merges with project-level config (project name, revision, suppressions).

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

## Limitations

- Schematic SVG renderer supports `.kicad_sch` (KiCad 6+) only — no legacy `.sch`
- Title block layout is approximate (doesn't match KiCad pixel-for-pixel)
- PCB 3D renders require kicad-cli (auto-detected from PATH, flatpak, macOS app bundle, or Windows Program Files)
- PDF/DOCX/ODT images are raster (300 DPI PNG), not vector — acceptable quality for engineering docs
- Narrative sections require Claude or manual authoring — the scaffold provides structure and data, not prose
