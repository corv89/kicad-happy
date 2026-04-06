# kidoc Scripts — Developer Reference

Engineering documentation generation scripts.

| Script | Input | Purpose | Deps |
|--------|-------|---------|------|
| `kidoc_render.py` | `.kicad_sch` | Schematic SVG renderer — full sheets, subsystem crops, overlays | zero-dep |
| `kidoc_diagrams.py` | analysis JSON | Block diagrams — power tree, bus topology, architecture | zero-dep |
| `kidoc_scaffold.py` | analysis JSON + config | Markdown scaffold with AUTO markers and NARRATIVE placeholders | zero-dep |
| `kidoc_generate.py` | markdown | Orchestrator — dispatches to venv for PDF/DOCX | zero-dep |
| `kidoc_pdf.py` | markdown | PDF via ReportLab + svglib (vector SVG embedding) | venv |
| `kidoc_html.py` | markdown | Self-contained HTML with inline SVGs | zero-dep |
| `kidoc_docx.py` | markdown | DOCX via python-docx + Pillow | venv |
| `kidoc_odt.py` | markdown | ODT via odfpy + Pillow | venv |
| `kidoc_md_parser.py` | — | Shared markdown to element list parser | zero-dep |
| `kidoc_sections.py` | — | Section content generators for scaffold | zero-dep |
| `kidoc_tables.py` | — | Markdown table formatting + unit formatters | zero-dep |
| `kidoc_templates.py` | — | Document type definitions (5 types) | zero-dep |
| `kidoc_venv.py` | — | Project-local venv bootstrap | zero-dep |
| `kicad_cli.py` | — | kicad-cli auto-detection (PATH, flatpak, macOS, Windows) | zero-dep |
| `svg_to_png.py` | SVG | Pillow-based SVG rasterizer (no Cairo) | Pillow |
| `sch_graphics.py` | — | Symbol body graphics extraction | zero-dep |
| `svg_builder.py` | — | SVG element builder (xml.etree.ElementTree) | zero-dep |
| `color_theme.py` | — | KiCad default schematic color constants | zero-dep |
| `requirements.txt` | — | Pinned deps for reports/.venv/ | — |

## kidoc_render.py

Renders `.kicad_sch` files to publication-quality SVG.  Uses `sexp_parser.py` from the kicad skill for S-expression parsing.

```bash
# Render full sheet
python3 kidoc_render.py design.kicad_sch --output reports/figures/schematics/

# Crop to specific components
python3 kidoc_render.py design.kicad_sch --crop R1,R2,C1 --output crop.svg

# With analysis overlays
python3 kidoc_render.py design.kicad_sch --overlay analysis.json --output out/
```

### Rendering pipeline

1. Parse `.kicad_sch` via `sexp_parser.parse_file()`
2. Extract symbol graphics (body shapes, pins) via `sch_graphics.extract_symbol_graphics()`
3. Extract connectivity (wires, labels, junctions, etc.) from the schematic
4. Render all elements to SVG in back-to-front order
5. Optionally crop to bounding box or add analysis overlays

### Coordinate system

- KiCad schematic placement: millimetres, Y-down
- Symbol library internals: millimetres, Y-up (math convention)
- SVG output: millimetres, Y-down
- Transform: mirror → rotate → translate with Y-axis flip (`abs_y = cy - rpy`)

## svg_builder.py

Produces svglib-compatible SVGs for downstream PDF embedding.  Constraints: inline styles only, no CSS classes, no gradients, no `<use>` elements.
