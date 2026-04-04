#!/usr/bin/env python3
"""KiCad schematic SVG renderer.

Renders ``.kicad_sch`` files to publication-quality SVG output.  Supports
full sheet rendering, subsystem cropping around specific components, and
analysis overlay annotations.

Usage:
    python3 kidoc_render.py <file.kicad_sch> --output <dir>
    python3 kidoc_render.py <file.kicad_sch> --output crop.svg --crop R1,R2,C1
    python3 kidoc_render.py <file.kicad_sch> --overlay analysis.json --output out/

Zero external dependencies — Python 3.8+ stdlib only (+ kicad skill's sexp_parser).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# Cross-skill imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_kicad_scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '..', '..', 'kicad', 'scripts')
if os.path.isdir(_kicad_scripts):
    sys.path.insert(0, os.path.abspath(_kicad_scripts))

from sexp_parser import (parse_file, find_all, find_first, get_value,
                          get_at, get_property, get_properties)
from svg_builder import SvgBuilder, three_point_arc, _f
from sch_graphics import (
    extract_symbol_graphics, parse_stroke, parse_fill, parse_effects,
    StrokeStyle, FillStyle, TextEffects, SymbolGraphics,
    RectGraphic, CircleGraphic, ArcGraphic, PolylineGraphic,
    BezierGraphic, TextGraphic, PinGraphic,
)
from color_theme import (
    WIRE_COLOR, BUS_COLOR, JUNCTION_COLOR, NO_CONNECT_COLOR,
    COMPONENT_OUTLINE_COLOR, COMPONENT_FILL_COLOR, PIN_COLOR,
    LABEL_COLOR, GLOBAL_LABEL_COLOR, HIER_LABEL_COLOR, POWER_SYMBOL_COLOR,
    REFERENCE_COLOR, VALUE_COLOR, TEXT_COLOR,
    SHEET_BORDER_COLOR, SHEET_FILL_COLOR, SHEET_LABEL_COLOR,
    BACKGROUND_COLOR, DRAWING_SHEET_COLOR,
    DEFAULT_STROKE_WIDTH, WIRE_STROKE_WIDTH, BUS_STROKE_WIDTH, PIN_STROKE_WIDTH,
    JUNCTION_RADIUS, NO_CONNECT_SIZE, PIN_INVERTED_RADIUS,
    DEFAULT_FONT_SIZE, DEFAULT_FONT_FAMILY,
    PAPER_SIZES, DASH_PATTERNS,
)


# ======================================================================
# Component extraction (simplified for rendering — mirrors analyze_schematic)
# ======================================================================

def _extract_components_for_render(root: list) -> list[dict]:
    """Extract placed component instances with placement data."""
    components = []
    for sym in root:
        if not isinstance(sym, list) or not sym or sym[0] != 'symbol':
            continue
        if len(sym) > 1 and isinstance(sym[1], str):
            continue  # lib_symbol definition, not placed instance

        lib_id = get_value(sym, 'lib_id') or ''
        lib_name = get_value(sym, 'lib_name') or ''
        if not lib_id:
            continue

        at = get_at(sym)
        x, y, angle = at if at else (0, 0, 0)

        mirror_node = find_first(sym, 'mirror')
        mirror_x = 'x' in mirror_node if mirror_node else False
        mirror_y = 'y' in mirror_node if mirror_node else False

        unit_node = find_first(sym, 'unit')
        unit = int(unit_node[1]) if unit_node and len(unit_node) > 1 else 1

        ref = get_property(sym, 'Reference') or ''
        value = get_property(sym, 'Value') or ''

        # Property visibility and positions for rendering
        props = []
        for child in sym:
            if isinstance(child, list) and child[0] == 'property' and len(child) >= 3:
                prop_name = child[1]
                prop_val = child[2]
                prop_at = get_at(child)
                prop_effects = parse_effects(child)
                props.append({
                    'name': prop_name, 'value': str(prop_val),
                    'at': prop_at, 'effects': prop_effects,
                })

        # Check DNP
        dnp = any(isinstance(c, str) and c == 'dnp' for c in sym)

        components.append({
            'lib_id': lib_id, 'lib_name': lib_name,
            'x': x, 'y': y, 'angle': angle,
            'mirror_x': mirror_x, 'mirror_y': mirror_y,
            'unit': unit, 'ref': ref, 'value': value,
            'properties': props, 'dnp': dnp,
            '_node': sym,  # keep reference for property extraction
        })
    return components


def _extract_wires(root: list) -> list[dict]:
    """Extract wire segments."""
    wires = []
    for child in root:
        if isinstance(child, list) and child and child[0] == 'wire':
            pts = find_first(child, 'pts')
            if pts:
                xys = [c for c in pts if isinstance(c, list) and c[0] == 'xy']
                if len(xys) >= 2:
                    wires.append({
                        'x1': float(xys[0][1]), 'y1': float(xys[0][2]),
                        'x2': float(xys[1][1]), 'y2': float(xys[1][2]),
                    })
    return wires


def _extract_buses(root: list) -> list[dict]:
    """Extract bus segments."""
    buses = []
    for child in root:
        if isinstance(child, list) and child and child[0] == 'bus':
            pts = find_first(child, 'pts')
            if pts:
                xys = [c for c in pts if isinstance(c, list) and c[0] == 'xy']
                if len(xys) >= 2:
                    buses.append({
                        'x1': float(xys[0][1]), 'y1': float(xys[0][2]),
                        'x2': float(xys[1][1]), 'y2': float(xys[1][2]),
                    })
    return buses


def _extract_bus_entries(root: list) -> list[dict]:
    """Extract bus entry stubs."""
    entries = []
    for child in root:
        if isinstance(child, list) and child and child[0] == 'bus_entry':
            at = get_at(child)
            size_node = find_first(child, 'size')
            if at and size_node and len(size_node) >= 3:
                entries.append({
                    'x': at[0], 'y': at[1],
                    'dx': float(size_node[1]), 'dy': float(size_node[2]),
                })
    return entries


def _extract_junctions(root: list) -> list[dict]:
    """Extract junction dots."""
    junctions = []
    for child in root:
        if isinstance(child, list) and child and child[0] == 'junction':
            at = get_at(child)
            if at:
                junctions.append({'x': at[0], 'y': at[1]})
    return junctions


def _extract_no_connects(root: list) -> list[dict]:
    """Extract no-connect markers."""
    ncs = []
    for child in root:
        if isinstance(child, list) and child and child[0] == 'no_connect':
            at = get_at(child)
            if at:
                ncs.append({'x': at[0], 'y': at[1]})
    return ncs


def _extract_labels(root: list) -> list[dict]:
    """Extract all label types."""
    labels = []
    for tag in ('label', 'global_label', 'hierarchical_label'):
        for child in root:
            if isinstance(child, list) and child and child[0] == tag and len(child) >= 2:
                name = child[1] if isinstance(child[1], str) else ''
                at = get_at(child)
                if not at:
                    continue
                shape = get_value(child, 'shape') or ''
                effects = parse_effects(child)
                labels.append({
                    'name': name, 'type': tag,
                    'x': at[0], 'y': at[1], 'angle': at[2],
                    'shape': shape, 'effects': effects,
                })
    return labels


def _extract_text_annotations(root: list) -> list[dict]:
    """Extract free-form text and text boxes."""
    annotations = []
    for child in root:
        if isinstance(child, list) and child:
            if child[0] == 'text' and len(child) >= 2 and isinstance(child[1], str):
                at = get_at(child)
                if at:
                    annotations.append({
                        'text': child[1], 'x': at[0], 'y': at[1],
                        'angle': at[2], 'effects': parse_effects(child),
                    })
    return annotations


def _extract_sheet_boxes(root: list) -> list[dict]:
    """Extract hierarchical sheet instances (the boxes drawn on the schematic)."""
    sheets = []
    for child in root:
        if isinstance(child, list) and child and child[0] == 'sheet':
            at = get_at(child)
            size_node = find_first(child, 'size')
            if not at or not size_node or len(size_node) < 3:
                continue
            name = ''
            filename = ''
            for prop in child:
                if isinstance(prop, list) and prop[0] == 'property' and len(prop) >= 3:
                    if prop[1] == 'Sheetname':
                        name = str(prop[2])
                    elif prop[1] == 'Sheetfile':
                        filename = str(prop[2])
            # Extract sheet pins
            pins = []
            for pin in find_all(child, 'pin'):
                if len(pin) >= 3:
                    pin_name = pin[1] if isinstance(pin[1], str) else ''
                    pin_shape = pin[2] if len(pin) > 2 and isinstance(pin[2], str) else ''
                    pin_at = get_at(pin)
                    if pin_at:
                        pins.append({
                            'name': pin_name, 'shape': pin_shape,
                            'x': pin_at[0], 'y': pin_at[1], 'angle': pin_at[2],
                        })
            sheets.append({
                'x': at[0], 'y': at[1],
                'w': float(size_node[1]), 'h': float(size_node[2]),
                'name': name, 'filename': filename, 'pins': pins,
            })
    return sheets


def _get_paper_size(root: list) -> tuple[float, float]:
    """Get paper size from the schematic's (paper ...) node."""
    paper_node = find_first(root, 'paper')
    if paper_node and len(paper_node) >= 2:
        paper_name = paper_node[1]
        if isinstance(paper_name, str):
            # Named size
            size = PAPER_SIZES.get(paper_name)
            if size:
                return size
            # Check for custom "User WxH"
            if paper_name == 'User' and len(paper_node) >= 4:
                try:
                    return float(paper_node[2]), float(paper_node[3])
                except (ValueError, TypeError):
                    pass
    return PAPER_SIZES['A4']  # default


# ======================================================================
# Rendering functions
# ======================================================================

def _stroke_width(stroke: StrokeStyle, default: float = DEFAULT_STROKE_WIDTH) -> float:
    """Resolve stroke width, using default when KiCad specifies 0."""
    return stroke.width if stroke.width > 0 else default


def _stroke_color(stroke: StrokeStyle, default: str) -> str:
    """Resolve stroke color from StrokeStyle or use themed default."""
    if stroke.color:
        r, g, b, _a = stroke.color
        return f"#{r:02x}{g:02x}{b:02x}"
    return default


def _fill_color(fill: FillStyle, outline_color: str, bg_color: str) -> str:
    """Resolve fill color based on fill type."""
    if fill.fill_type == 'outline':
        return outline_color
    if fill.fill_type == 'background':
        return bg_color
    if fill.color:
        r, g, b, _a = fill.color
        return f"#{r:02x}{g:02x}{b:02x}"
    return 'none'


def _dash_pattern(stroke: StrokeStyle) -> str | None:
    """Get SVG dash pattern from stroke type."""
    return DASH_PATTERNS.get(stroke.type)


def _transform_point(px: float, py: float, angle: float,
                     mirror_x: bool, mirror_y: bool,
                     cx: float, cy: float) -> tuple[float, float]:
    """Transform a symbol-local point to schematic coordinates.

    Replicates the transform from compute_pin_positions:
    1. Mirror (before rotation)
    2. Rotate
    3. Translate + Y-axis flip (symbol Y-up → schematic Y-down)
    """
    if mirror_x:
        py = -py
    if mirror_y:
        px = -px
    if angle != 0:
        rad = math.radians(angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        rpx = px * cos_a - py * sin_a
        rpy = px * sin_a + py * cos_a
    else:
        rpx, rpy = px, py
    # Y-axis flip: symbol coords are math-up, schematic is screen-down
    return cx + rpx, cy - rpy


def render_component(svg: SvgBuilder, comp: dict,
                     sym_graphics: SymbolGraphics,
                     lib_symbols: dict | None = None) -> None:
    """Render a single placed component."""
    cx, cy = comp['x'], comp['y']
    angle = comp['angle']
    mx, my = comp['mirror_x'], comp['mirror_y']
    unit = comp.get('unit', 1) or 1

    # Collect graphics for this unit: unit-specific + shared (unit 0)
    all_graphics = list(sym_graphics.unit_graphics.get(unit, []))
    if 0 in sym_graphics.unit_graphics:
        all_graphics.extend(sym_graphics.unit_graphics[0])

    for g in all_graphics:
        if isinstance(g, PinGraphic):
            _render_pin(svg, g, cx, cy, angle, mx, my,
                        sym_graphics.pin_names_visible,
                        sym_graphics.pin_numbers_visible,
                        sym_graphics.pin_name_offset)
        elif isinstance(g, RectGraphic):
            _render_rect(svg, g, cx, cy, angle, mx, my)
        elif isinstance(g, CircleGraphic):
            _render_circle(svg, g, cx, cy, angle, mx, my)
        elif isinstance(g, ArcGraphic):
            _render_arc(svg, g, cx, cy, angle, mx, my)
        elif isinstance(g, PolylineGraphic):
            _render_polyline(svg, g, cx, cy, angle, mx, my)
        elif isinstance(g, BezierGraphic):
            _render_bezier(svg, g, cx, cy, angle, mx, my)
        elif isinstance(g, TextGraphic):
            _render_symbol_text(svg, g, cx, cy, angle, mx, my)

    # Render property text (Reference, Value) at their specified positions
    _render_properties(svg, comp)


def _render_rect(svg: SvgBuilder, g: RectGraphic,
                 cx: float, cy: float, angle: float,
                 mx: bool, my: bool) -> None:
    """Render a rectangle primitive."""
    p1 = _transform_point(g.x1, g.y1, angle, mx, my, cx, cy)
    p2 = _transform_point(g.x2, g.y2, angle, mx, my, cx, cy)
    x = min(p1[0], p2[0])
    y = min(p1[1], p2[1])
    w = abs(p2[0] - p1[0])
    h = abs(p2[1] - p1[1])
    stroke_c = _stroke_color(g.stroke, COMPONENT_OUTLINE_COLOR)
    fill_c = _fill_color(g.fill, stroke_c, COMPONENT_FILL_COLOR)
    sw = _stroke_width(g.stroke)
    svg.rect(x, y, w, h, stroke=stroke_c, fill=fill_c, stroke_width=sw)


def _render_circle(svg: SvgBuilder, g: CircleGraphic,
                   cx: float, cy: float, angle: float,
                   mx: bool, my: bool) -> None:
    """Render a circle primitive."""
    center = _transform_point(g.cx, g.cy, angle, mx, my, cx, cy)
    stroke_c = _stroke_color(g.stroke, COMPONENT_OUTLINE_COLOR)
    fill_c = _fill_color(g.fill, stroke_c, COMPONENT_FILL_COLOR)
    sw = _stroke_width(g.stroke)
    svg.circle(center[0], center[1], g.radius,
               stroke=stroke_c, fill=fill_c, stroke_width=sw)


def _render_arc(svg: SvgBuilder, g: ArcGraphic,
                cx: float, cy: float, angle: float,
                mx: bool, my: bool) -> None:
    """Render a 3-point arc."""
    s = _transform_point(g.sx, g.sy, angle, mx, my, cx, cy)
    m = _transform_point(g.mx, g.my, angle, mx, my, cx, cy)
    e = _transform_point(g.ex, g.ey, angle, mx, my, cx, cy)
    ac, ay_c, r, large_arc, sweep = three_point_arc(s[0], s[1], m[0], m[1], e[0], e[1])
    stroke_c = _stroke_color(g.stroke, COMPONENT_OUTLINE_COLOR)
    fill_c = _fill_color(g.fill, stroke_c, COMPONENT_FILL_COLOR)
    sw = _stroke_width(g.stroke)
    svg.arc(s[0], s[1], e[0], e[1], r, large_arc, sweep,
            stroke=stroke_c, fill=fill_c, stroke_width=sw)


def _render_polyline(svg: SvgBuilder, g: PolylineGraphic,
                     cx: float, cy: float, angle: float,
                     mx: bool, my: bool) -> None:
    """Render a polyline primitive."""
    points = [_transform_point(px, py, angle, mx, my, cx, cy)
              for px, py in g.points]
    if not points:
        return
    stroke_c = _stroke_color(g.stroke, COMPONENT_OUTLINE_COLOR)
    fill_c = _fill_color(g.fill, stroke_c, COMPONENT_FILL_COLOR)
    sw = _stroke_width(g.stroke)
    # If fill is set and the polyline closes back to start, render as polygon
    closed = (fill_c != 'none' and len(points) >= 3
              and abs(points[0][0] - points[-1][0]) < 0.01
              and abs(points[0][1] - points[-1][1]) < 0.01)
    svg.polyline(points, stroke=stroke_c, fill=fill_c,
                 stroke_width=sw, closed=closed)


def _render_bezier(svg: SvgBuilder, g: BezierGraphic,
                   cx: float, cy: float, angle: float,
                   mx: bool, my: bool) -> None:
    """Render a cubic bezier curve."""
    points = [_transform_point(px, py, angle, mx, my, cx, cy)
              for px, py in g.points]
    if not points:
        return
    stroke_c = _stroke_color(g.stroke, COMPONENT_OUTLINE_COLOR)
    fill_c = _fill_color(g.fill, stroke_c, COMPONENT_FILL_COLOR)
    sw = _stroke_width(g.stroke)
    svg.bezier(points, stroke=stroke_c, fill=fill_c, stroke_width=sw)


def _render_symbol_text(svg: SvgBuilder, g: TextGraphic,
                        cx: float, cy: float, angle: float,
                        mx: bool, my: bool) -> None:
    """Render a text element inside a symbol body."""
    if g.effects.hidden:
        return
    pos = _transform_point(g.x, g.y, angle, mx, my, cx, cy)
    h_just = g.effects.h_justify
    anchor = {'left': 'start', 'right': 'end'}.get(h_just, 'middle')
    svg.text(pos[0], pos[1], g.text,
             font_size=g.effects.height,
             font_family=DEFAULT_FONT_FAMILY,
             anchor=anchor,
             dominant_baseline='central',
             fill=TEXT_COLOR,
             bold=g.effects.bold, italic=g.effects.italic)


def _render_pin(svg: SvgBuilder, pin: PinGraphic,
                cx: float, cy: float, comp_angle: float,
                mx: bool, my: bool,
                names_visible: bool, numbers_visible: bool,
                name_offset: float) -> None:
    """Render a single pin: line, shape indicator, name, number."""
    # Pin position is the connection point (tip).  The body end is
    # offset by -length along the pin's own angle.
    pin_angle = pin.angle  # degrees, in symbol-local space
    length = pin.length

    # Pin tip (wire connection point) in symbol coords
    tip_x, tip_y = pin.x, pin.y
    # Pin body end: angle points toward the body, so body = tip + length*direction
    body_dx = length * math.cos(math.radians(pin_angle))
    body_dy = length * math.sin(math.radians(pin_angle))
    body_x = tip_x + body_dx
    body_y = tip_y + body_dy

    # Transform to schematic coords
    tip = _transform_point(tip_x, tip_y, comp_angle, mx, my, cx, cy)
    body = _transform_point(body_x, body_y, comp_angle, mx, my, cx, cy)

    # Draw pin line
    svg.line(tip[0], tip[1], body[0], body[1],
             stroke=PIN_COLOR, stroke_width=PIN_STROKE_WIDTH)

    # Pin shape indicator at body end
    if pin.shape == 'inverted' or pin.shape == 'inverted_clock':
        # Small circle at body end
        # Place circle between body and tip, touching body
        dx = tip[0] - body[0]
        dy = tip[1] - body[1]
        dist = math.hypot(dx, dy)
        if dist > 0:
            nx, ny = dx / dist, dy / dist
            svg.circle(body[0] + nx * PIN_INVERTED_RADIUS,
                       body[1] + ny * PIN_INVERTED_RADIUS,
                       PIN_INVERTED_RADIUS,
                       stroke=PIN_COLOR, fill='none',
                       stroke_width=PIN_STROKE_WIDTH)

    if pin.shape in ('clock', 'inverted_clock', 'edge_clock_high'):
        # Clock wedge: two short lines forming '>' at body end
        dx = tip[0] - body[0]
        dy = tip[1] - body[1]
        dist = math.hypot(dx, dy)
        if dist > 0:
            nx, ny = dx / dist, dy / dist
            perp_x, perp_y = -ny, nx
            wedge_size = PIN_INVERTED_RADIUS * 1.5
            bx, by = body[0], body[1]
            svg.line(bx - perp_x * wedge_size, by - perp_y * wedge_size,
                     bx + nx * wedge_size, by + ny * wedge_size,
                     stroke=PIN_COLOR, stroke_width=PIN_STROKE_WIDTH)
            svg.line(bx + perp_x * wedge_size, by + perp_y * wedge_size,
                     bx + nx * wedge_size, by + ny * wedge_size,
                     stroke=PIN_COLOR, stroke_width=PIN_STROKE_WIDTH)

    # Pin name and number text
    if pin.name_effects.hidden:
        names_visible = False
    if pin.number_effects.hidden:
        numbers_visible = False

    if names_visible and pin.name and pin.name != '~':
        _render_pin_text(svg, pin.name, body, tip, name_offset,
                         pin.name_effects, is_name=True)
    if numbers_visible and pin.number:
        _render_pin_text(svg, pin.number, body, tip, 0,
                         pin.number_effects, is_name=False)


def _render_pin_text(svg: SvgBuilder, text: str,
                     body: tuple[float, float], tip: tuple[float, float],
                     offset: float, effects: TextEffects,
                     is_name: bool) -> None:
    """Render pin name or number text along the pin."""
    dx = tip[0] - body[0]
    dy = tip[1] - body[1]
    dist = math.hypot(dx, dy)
    if dist < 0.01:
        return

    # Pin direction unit vector (body → tip)
    nx, ny = dx / dist, dy / dist

    font_size = effects.height if effects.height > 0 else DEFAULT_FONT_SIZE

    if is_name:
        # Name is placed outside the body, past the body end with clearance
        tx = body[0] - nx * (offset + 0.4)
        ty = body[1] - ny * (offset + 0.4)
        # Anchor depends on pin direction
        anchor = 'end' if abs(nx) > 0.5 and nx > 0 else 'start'
        if abs(nx) < 0.1:
            anchor = 'middle'
    else:
        # Number is placed along the pin, biased toward body end (70% from tip)
        tx = tip[0] + (body[0] - tip[0]) * 0.7
        ty = tip[1] + (body[1] - tip[1]) * 0.7
        anchor = 'middle'
        # Offset perpendicular to the pin line to clear the wire
        perp_x, perp_y = -ny, nx
        tx += perp_x * font_size * 0.8
        ty += perp_y * font_size * 0.8

    svg.text(tx, ty, text,
             font_size=font_size,
             font_family=DEFAULT_FONT_FAMILY,
             anchor=anchor,
             dominant_baseline='central',
             fill=PIN_COLOR,
             bold=effects.bold, italic=effects.italic)


def _render_properties(svg: SvgBuilder, comp: dict) -> None:
    """Render visible properties (Reference, Value) at their specified positions."""
    for prop in comp.get('properties', []):
        effects = prop.get('effects')
        if not effects or effects.hidden:
            continue
        at = prop.get('at')
        if not at:
            continue
        name = prop['name']
        if name not in ('Reference', 'Value'):
            continue

        color = REFERENCE_COLOR if name == 'Reference' else VALUE_COLOR
        h_just = effects.h_justify
        anchor = {'left': 'start', 'right': 'end'}.get(h_just, 'middle')
        font_size = effects.height if effects.height > 0 else DEFAULT_FONT_SIZE

        # KiCad CLI renders all text horizontal — match that behavior
        svg.text(at[0], at[1], prop['value'],
                 font_size=font_size,
                 font_family=DEFAULT_FONT_FAMILY,
                 anchor=anchor,
                 dominant_baseline='central',
                 fill=color,
                 bold=effects.bold, italic=effects.italic)


# ======================================================================
# Sheet-level rendering
# ======================================================================

def render_sheet(svg: SvgBuilder, root: list, sym_graphics: dict,
                 paper_w: float, paper_h: float) -> None:
    """Render a complete schematic sheet."""
    # Extract all elements
    components = _extract_components_for_render(root)
    wires = _extract_wires(root)
    buses = _extract_buses(root)
    bus_entries = _extract_bus_entries(root)
    junctions = _extract_junctions(root)
    no_connects = _extract_no_connects(root)
    labels = _extract_labels(root)
    texts = _extract_text_annotations(root)
    sheets = _extract_sheet_boxes(root)

    # Background
    svg.rect(0, 0, paper_w, paper_h, fill=BACKGROUND_COLOR, stroke='none')

    # Sheet border
    margin = 5.0
    svg.rect(margin, margin, paper_w - 2 * margin, paper_h - 2 * margin,
             stroke=DRAWING_SHEET_COLOR, fill='none', stroke_width=0.2)

    # Render order (back to front)
    # 1. Bus wires
    for bus in buses:
        svg.line(bus['x1'], bus['y1'], bus['x2'], bus['y2'],
                 stroke=BUS_COLOR, stroke_width=BUS_STROKE_WIDTH)

    # 2. Bus entries
    for entry in bus_entries:
        svg.line(entry['x'], entry['y'],
                 entry['x'] + entry['dx'], entry['y'] + entry['dy'],
                 stroke=BUS_COLOR, stroke_width=BUS_STROKE_WIDTH)

    # 3. Wires
    for wire in wires:
        svg.line(wire['x1'], wire['y1'], wire['x2'], wire['y2'],
                 stroke=WIRE_COLOR, stroke_width=WIRE_STROKE_WIDTH)

    # 4. Junctions
    for j in junctions:
        svg.circle(j['x'], j['y'], JUNCTION_RADIUS,
                   fill=JUNCTION_COLOR, stroke='none')

    # 5. No-connects
    s = NO_CONNECT_SIZE
    for nc in no_connects:
        svg.line(nc['x'] - s, nc['y'] - s, nc['x'] + s, nc['y'] + s,
                 stroke=NO_CONNECT_COLOR, stroke_width=DEFAULT_STROKE_WIDTH)
        svg.line(nc['x'] - s, nc['y'] + s, nc['x'] + s, nc['y'] - s,
                 stroke=NO_CONNECT_COLOR, stroke_width=DEFAULT_STROKE_WIDTH)

    # 6. Hierarchical sheet boxes
    for sheet in sheets:
        svg.rect(sheet['x'], sheet['y'], sheet['w'], sheet['h'],
                 stroke=SHEET_BORDER_COLOR, fill=SHEET_FILL_COLOR,
                 stroke_width=DEFAULT_STROKE_WIDTH)
        svg.text(sheet['x'] + 1, sheet['y'] + sheet['h'] + 1.5,
                 sheet['name'],
                 font_size=DEFAULT_FONT_SIZE,
                 fill=SHEET_BORDER_COLOR, bold=True)

    # 7. Components
    for comp in components:
        lib_id = comp['lib_name'] or comp['lib_id']
        sg = sym_graphics.get(lib_id) or sym_graphics.get(comp['lib_id'])
        if sg:
            render_component(svg, comp, sg)

    # 8. Labels
    for lbl in labels:
        _render_label(svg, lbl)

    # 9. Text annotations
    for txt in texts:
        if txt.get('effects') and txt['effects'].hidden:
            continue
        effects = txt.get('effects') or parse_effects([])
        font_size = effects.height if effects.height > 0 else DEFAULT_FONT_SIZE
        svg.text(txt['x'], txt['y'], txt['text'],
                 font_size=font_size,
                 fill=TEXT_COLOR,
                 bold=effects.bold, italic=effects.italic)


def _render_label(svg: SvgBuilder, lbl: dict) -> None:
    """Render a label (local, global, hierarchical)."""
    x, y = lbl['x'], lbl['y']
    name = lbl['name']
    ltype = lbl['type']
    effects = lbl.get('effects')
    font_size = (effects.height if effects and effects.height > 0
                 else DEFAULT_FONT_SIZE)

    if ltype == 'label':
        # Local label: horizontal text, offset slightly from wire
        offset = font_size * 0.15
        svg.text(x, y - offset, name, font_size=font_size, fill=LABEL_COLOR,
                 dominant_baseline='auto')
    elif ltype == 'global_label':
        # Global label: text inside a flag shape
        _render_flag_label(svg, x, y, name, lbl.get('shape', ''),
                           font_size, GLOBAL_LABEL_COLOR, lbl.get('angle', 0))
    elif ltype == 'hierarchical_label':
        _render_flag_label(svg, x, y, name, lbl.get('shape', ''),
                           font_size, HIER_LABEL_COLOR, lbl.get('angle', 0))


def _render_flag_label(svg: SvgBuilder, x: float, y: float, name: str,
                       shape: str, font_size: float, color: str,
                       angle: float) -> None:
    """Render a global/hierarchical label with its flag shape."""
    # Approximate text width
    text_w = len(name) * font_size * 0.6
    pad = font_size * 0.3
    h = font_size * 1.4
    flag_w = font_size * 0.7

    # Build flag outline points based on shape
    # All shapes are oriented rightward, then rotated
    half_h = h / 2
    if shape == 'input':
        pts = [(0, -half_h), (text_w + 2 * pad, -half_h),
               (text_w + 2 * pad + flag_w, 0),
               (text_w + 2 * pad, half_h), (0, half_h)]
    elif shape == 'output':
        pts = [(flag_w, -half_h), (text_w + 2 * pad + flag_w, -half_h),
               (text_w + 2 * pad + flag_w, half_h),
               (flag_w, half_h), (0, 0)]
    elif shape == 'bidirectional':
        pts = [(flag_w, -half_h), (text_w + 2 * pad, -half_h),
               (text_w + 2 * pad + flag_w, 0),
               (text_w + 2 * pad, half_h),
               (flag_w, half_h), (0, 0)]
    else:
        # passive, tri_state, or unknown: rectangle
        pts = [(0, -half_h), (text_w + 2 * pad, -half_h),
               (text_w + 2 * pad, half_h), (0, half_h)]

    # Rotate points around origin by label angle, then translate
    if angle:
        rad = math.radians(angle)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        pts = [(px * cos_a - py * sin_a, px * sin_a + py * cos_a)
               for px, py in pts]

    # Translate to label position
    pts = [(px + x, py + y) for px, py in pts]

    svg.polyline(pts, stroke=color, fill=BACKGROUND_COLOR,
                 stroke_width=DEFAULT_STROKE_WIDTH, closed=True)

    # Text inside the flag — rotated with the shape
    text_x = x + pad
    text_y = y
    if angle:
        rad = math.radians(angle)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        text_x = x + pad * cos_a
        text_y = y + pad * sin_a

    # KiCad CLI renders all text horizontal
    svg.text(text_x, text_y, name, font_size=font_size, fill=color,
             dominant_baseline='central')


# ======================================================================
# Crop and overlay
# ======================================================================

def compute_crop_bbox(components: list[dict], crop_refs: list[str],
                      wires: list[dict], padding: float = 5.0
                      ) -> tuple[float, float, float, float] | None:
    """Compute bounding box around specified component references.

    Returns (min_x, min_y, width, height) or None if no refs found.
    """
    xs, ys = [], []
    crop_set = set(crop_refs)

    for comp in components:
        if comp['ref'] in crop_set:
            xs.append(comp['x'])
            ys.append(comp['y'])
            # Include approximate symbol extent (rough estimate)
            xs.extend([comp['x'] - 10, comp['x'] + 10])
            ys.extend([comp['y'] - 10, comp['y'] + 10])

    if not xs:
        return None

    min_x = min(xs) - padding
    min_y = min(ys) - padding
    max_x = max(xs) + padding
    max_y = max(ys) + padding
    return min_x, min_y, max_x - min_x, max_y - min_y


def render_overlays(svg: SvgBuilder, overlay_data: dict,
                    components: list[dict]) -> None:
    """Render analysis overlay annotations from signal_analysis data."""
    signal_analysis = overlay_data.get('signal_analysis', {})
    comp_map = {c['ref']: c for c in components}

    for det_type, detections in signal_analysis.items():
        if not isinstance(detections, list):
            continue
        for det in detections:
            if not isinstance(det, dict):
                continue
            _render_detection_overlay(svg, det_type, det, comp_map)


def _render_detection_overlay(svg: SvgBuilder, det_type: str,
                              det: dict, comp_map: dict) -> None:
    """Render a single detection annotation."""
    # Find a component to anchor the annotation
    refs = det.get('components', []) or det.get('refs', [])
    if not refs:
        return
    anchor_ref = refs[0]
    anchor = comp_map.get(anchor_ref)
    if not anchor:
        return

    # Build annotation text based on detection type
    text = _format_detection_text(det_type, det)
    if not text:
        return

    # Position: offset slightly from the anchor component
    ax, ay = anchor['x'] + 5, anchor['y'] - 5

    # Annotation bubble: rounded rect + text
    font_size = 1.0
    text_w = len(text) * font_size * 0.55
    pad = 0.5
    svg.rect(ax - pad, ay - font_size * 0.7 - pad,
             text_w + 2 * pad, font_size * 1.4 + 2 * pad,
             stroke='#2060c0', fill='#e8f0ff',
             stroke_width=0.1, rx=0.5)
    svg.text(ax, ay, text, font_size=font_size, fill='#2060c0',
             dominant_baseline='central')


def _format_detection_text(det_type: str, det: dict) -> str:
    """Format annotation text for a detection."""
    if det_type == 'voltage_dividers':
        vout = det.get('v_out') or det.get('output_voltage')
        if vout:
            return f"Vout={vout:.2f}V"
    elif det_type in ('rc_filters', 'lc_filters'):
        fc = det.get('fc_hz') or det.get('cutoff_hz')
        if fc:
            if fc >= 1e6:
                return f"fc={fc/1e6:.1f}MHz"
            elif fc >= 1e3:
                return f"fc={fc/1e3:.1f}kHz"
            else:
                return f"fc={fc:.0f}Hz"
    elif det_type == 'power_regulators':
        vout = det.get('v_out') or det.get('output_voltage')
        if vout:
            return f"{vout:.1f}V"
    elif det_type == 'crystal_circuits':
        freq = det.get('frequency_hz')
        if freq:
            if freq >= 1e6:
                return f"{freq/1e6:.1f}MHz"
            else:
                return f"{freq/1e3:.0f}kHz"
    return ''


# ======================================================================
# Main entry point
# ======================================================================

def render_schematic(sch_path: str, output_dir: str,
                     crop_refs: list[str] | None = None,
                     overlay_json: str | None = None,
                     padding: float = 5.0) -> list[str]:
    """Render a .kicad_sch file to SVG.

    Returns list of output file paths.
    """
    root = parse_file(sch_path)
    paper_w, paper_h = _get_paper_size(root)
    sym_graphics = extract_symbol_graphics(root)

    # Load overlay data if provided
    overlay_data = None
    if overlay_json and os.path.isfile(overlay_json):
        with open(overlay_json) as f:
            overlay_data = json.load(f)

    output_files = []

    # Render the root sheet
    svg = SvgBuilder(paper_w, paper_h)

    render_sheet(svg, root, sym_graphics, paper_w, paper_h)

    # Apply crop if requested
    if crop_refs:
        components = _extract_components_for_render(root)
        wires = _extract_wires(root)
        bbox = compute_crop_bbox(components, crop_refs, wires, padding)
        if bbox:
            svg.set_viewbox(*bbox)

    # Apply overlays
    if overlay_data:
        components = _extract_components_for_render(root)
        render_overlays(svg, overlay_data, components)

    # Determine output path
    os.makedirs(output_dir, exist_ok=True)
    base = Path(sch_path).stem
    out_path = os.path.join(output_dir, f"{base}.svg")
    svg.write(out_path)
    output_files.append(out_path)

    return output_files


def main():
    parser = argparse.ArgumentParser(
        description='Render KiCad schematics to SVG')
    parser.add_argument('schematic', help='Path to .kicad_sch file')
    parser.add_argument('--output', '-o', required=True,
                        help='Output directory (or file path for single SVG)')
    parser.add_argument('--crop', default=None,
                        help='Comma-separated component refs to crop around')
    parser.add_argument('--overlay', default=None,
                        help='Path to analysis JSON for annotation overlays')
    parser.add_argument('--padding', type=float, default=5.0,
                        help='Padding around crop bounding box (mm)')
    args = parser.parse_args()

    crop_refs = args.crop.split(',') if args.crop else None

    # If output looks like a file (has .svg extension), use its directory
    output_dir = args.output
    if output_dir.endswith('.svg'):
        output_dir = os.path.dirname(output_dir) or '.'

    files = render_schematic(
        args.schematic, output_dir,
        crop_refs=crop_refs,
        overlay_json=args.overlay,
        padding=args.padding,
    )

    for f in files:
        print(f, file=sys.stderr)


if __name__ == '__main__':
    main()
