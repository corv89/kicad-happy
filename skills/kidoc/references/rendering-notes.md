# Schematic SVG Rendering Notes

## Coordinate System

KiCad uses two coordinate conventions that must be reconciled:

- **Schematic placement** (`.kicad_sch` top level): millimetres, Y-down (screen convention)
- **Symbol library internals** (`lib_symbols`): millimetres, Y-up (math convention)

The transform pipeline for each symbol graphic primitive:

1. **Mirror** (before rotation): if `mirror_x`, negate Y; if `mirror_y`, negate X
2. **Rotate**: standard 2D rotation by component angle (CCW positive, degrees)
3. **Translate + Y-flip**: `abs_x = cx + rpx`, `abs_y = cy - rpy`

This matches `compute_pin_positions()` in `analyze_schematic.py`.

## SVG Constraints (svglib compatibility)

The SVGs must be parseable by svglib for downstream PDF embedding via ReportLab.
svglib has limited SVG support, so we constrain output to:

- Inline style attributes only (no `<style>` blocks, no CSS classes)
- No gradients (`<linearGradient>`, `<radialGradient>`)
- No masks or clipping (beyond simple rectangles)
- No `<use>` / `<defs>` reuse (each element rendered inline)
- No `<foreignObject>`
- Basic shapes: `<line>`, `<rect>`, `<circle>`, `<ellipse>`, `<polyline>`, `<polygon>`, `<path>`, `<text>`, `<g>`

## Arc Conversion

KiCad arcs are defined by 3 points (start, mid, end).  SVG arcs use the
centre-point parameterisation (`A rx,ry rotation large-arc-flag sweep-flag x,y`).

Conversion in `svg_builder.three_point_arc()`:
1. Find circle centre from 3 points via perpendicular bisector intersection
2. Compute radius as distance from centre to any point
3. Determine sweep direction from cross product of (start→mid) × (start→end)
4. Determine large-arc flag by checking if midpoint lies on the minor arc

## Color Theme

Colors are hardcoded in `color_theme.py` to match KiCad's default theme.
Component outlines are dark red (#840000), fills are light yellow (#ffffc8),
wires are green (#00841a), pins are dark red, labels are green.
