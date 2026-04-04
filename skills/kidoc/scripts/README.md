# kidoc Scripts — Developer Reference

Engineering documentation generation scripts.

| Script | Input | Purpose |
|--------|-------|---------|
| `kidoc_render.py` | `.kicad_sch` | Schematic SVG renderer — full sheets, subsystem crops, analysis overlays |
| `sch_graphics.py` | — | Symbol body graphics extraction (rects, circles, arcs, polylines, pins) |
| `svg_builder.py` | — | SVG element builder using xml.etree.ElementTree |
| `color_theme.py` | — | KiCad default schematic color constants |

## kidoc_render.py

Renders `.kicad_sch` files to publication-quality SVG.  Uses `sexp_parser.py` from the kicad skill for S-expression parsing.

```bash
# Render full sheet
python3 kidoc_render.py design.kicad_sch --output reports/cache/schematic/

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
