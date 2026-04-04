#!/usr/bin/env python3
"""Block diagram generator for engineering documentation.

Generates power tree, bus topology, and architecture block diagrams
from schematic analysis JSON.  Output is SVG via svg_builder.

Usage:
    python3 kidoc_diagrams.py --analysis schematic.json --all --output reports/cache/diagrams/
    python3 kidoc_diagrams.py --analysis schematic.json --power-tree --output diagrams/
    python3 kidoc_diagrams.py --analysis schematic.json --bus-topology --output diagrams/
    python3 kidoc_diagrams.py --analysis schematic.json --architecture --output diagrams/

Zero external dependencies — Python 3.8+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svg_builder import SvgBuilder


# ======================================================================
# Colors and constants
# ======================================================================

BOX_FILL = "#e8e8ff"
BOX_STROKE = "#4040c0"
BOX_FONT = "#202060"

POWER_FILL = "#fff0e0"
POWER_STROKE = "#c06000"
POWER_FONT = "#804000"

BUS_FILL = "#e0ffe0"
BUS_STROKE = "#008040"
BUS_FONT = "#004020"

IO_FILL = "#ffe0e0"
IO_STROKE = "#c04040"
IO_FONT = "#802020"

ARROW_COLOR = "#606060"
LABEL_FONT = "#404040"
BG_COLOR = "#ffffff"

BOX_CORNER_RADIUS = 2.0
BOX_PADDING = 3.0
FONT_SIZE = 2.5
SMALL_FONT = 1.8
ARROW_HEAD_SIZE = 1.5


# ======================================================================
# Drawing primitives
# ======================================================================

def _draw_box(svg: SvgBuilder, x: float, y: float, w: float, h: float,
              label: str, sublabel: str = "",
              fill: str = BOX_FILL, stroke: str = BOX_STROKE,
              font_color: str = BOX_FONT) -> None:
    """Draw a labeled rounded-rectangle box."""
    svg.rect(x, y, w, h, stroke=stroke, fill=fill,
             stroke_width=0.3, rx=BOX_CORNER_RADIUS)
    svg.text(x + w / 2, y + h / 2 - (1.5 if sublabel else 0),
             label, font_size=FONT_SIZE, fill=font_color,
             anchor='middle', dominant_baseline='central', bold=True)
    if sublabel:
        svg.text(x + w / 2, y + h / 2 + 2.0,
                 sublabel, font_size=SMALL_FONT, fill=font_color,
                 anchor='middle', dominant_baseline='central')


def _draw_arrow(svg: SvgBuilder, x1: float, y1: float,
                x2: float, y2: float, label: str = "",
                color: str = ARROW_COLOR, stroke_width: float = 0.3) -> None:
    """Draw an arrow from (x1,y1) to (x2,y2) with optional label."""
    svg.line(x1, y1, x2, y2, stroke=color, stroke_width=stroke_width)
    # Arrowhead
    dx, dy = x2 - x1, y2 - y1
    dist = math.hypot(dx, dy)
    if dist < 0.1:
        return
    nx, ny = dx / dist, dy / dist
    px, py = -ny, nx  # perpendicular
    s = ARROW_HEAD_SIZE
    svg.polyline([
        (x2 - nx * s + px * s * 0.4, y2 - ny * s + py * s * 0.4),
        (x2, y2),
        (x2 - nx * s - px * s * 0.4, y2 - ny * s - py * s * 0.4),
    ], stroke=color, fill=color, stroke_width=stroke_width, closed=True)
    # Label at midpoint
    if label:
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        svg.text(mx, my - 1.5, label, font_size=SMALL_FONT,
                 fill=LABEL_FONT, anchor='middle')


def _draw_bus_line(svg: SvgBuilder, x1: float, y1: float,
                   x2: float, y2: float,
                   color: str = BUS_STROKE, width: float = 0.6) -> None:
    """Draw a thick bus line."""
    svg.line(x1, y1, x2, y2, stroke=color, stroke_width=width)


# ======================================================================
# Power Tree Diagram
# ======================================================================

def _format_cap_summary(caps: list[dict]) -> str:
    """Format a capacitor list into a compact summary string."""
    if not caps:
        return ''
    # Group by value
    by_value: dict[str, list[str]] = {}
    for c in caps:
        val = c.get('value', '?')
        ref = c.get('ref', '')
        # Skip compensation/feedforward caps (tiny values)
        farads = c.get('farads', 0)
        if farads and farads < 1e-9:
            continue
        by_value.setdefault(val, []).append(ref)
    parts = []
    for val, refs in sorted(by_value.items(), key=lambda x: -len(x[1])):
        if len(refs) == 1:
            parts.append(f"{refs[0]}={val}")
        else:
            parts.append(f"{len(refs)}×{val}")
    return ', '.join(parts)


def generate_power_tree(analysis: dict, output_path: str) -> str | None:
    """Generate a power tree SVG from schematic analysis JSON.

    Shows regulators with topology, Vout, inductor value, and output
    capacitor summary.  Rail boxes show the rail name and voltage.
    """
    regulators = analysis.get('signal_analysis', {}).get('power_regulators', [])
    if not regulators:
        return None

    # Build graph
    rails = set()
    edges = []
    for reg in regulators:
        in_rail = reg.get('input_rail', '?')
        out_rail = reg.get('output_rail', '?')
        rails.add(in_rail)
        rails.add(out_rail)
        edges.append((in_rail, out_rail, reg))

    # Topological sort
    output_rails = {e[1] for e in edges}
    input_rails = {e[0] for e in edges}
    root_rails = input_rails - output_rails
    if not root_rails:
        root_rails = input_rails

    depth = {}
    queue = list(root_rails)
    for r in queue:
        depth[r] = 0
    visited = set(queue)
    while queue:
        rail = queue.pop(0)
        for in_r, out_r, _ in edges:
            if in_r == rail and out_r not in visited:
                depth[out_r] = depth[rail] + 1
                visited.add(out_r)
                queue.append(out_r)
    for r in rails:
        if r not in depth:
            depth[r] = 0

    max_depth = max(depth.values()) if depth else 0
    ranks: dict[int, list[str]] = {}
    for rail, d in depth.items():
        ranks.setdefault(d, []).append(rail)

    # Layout — wider spacing for richer regulator boxes
    rail_w = 30.0
    rail_h = 12.0
    reg_w = 42.0
    reg_h = 20.0
    h_spacing = 60.0
    v_spacing = 35.0
    margin = 12.0
    cap_font = 1.4
    detail_font = 1.5

    # Rail positions
    rail_pos: dict[str, tuple[float, float]] = {}
    for d in range(max_depth + 1):
        rank_rails = sorted(ranks.get(d, []))
        for i, rail in enumerate(rank_rails):
            x = margin + d * h_spacing
            y = margin + 8 + i * v_spacing
            rail_pos[rail] = (x, y)

    max_x = max((p[0] for p in rail_pos.values()), default=0) + rail_w + margin
    max_y = max((p[1] for p in rail_pos.values()), default=0) + rail_h + margin
    total_w = max(max_x + h_spacing, 180)
    total_h = max(max_y + 20, 70)

    svg = SvgBuilder(total_w, total_h)
    svg.rect(0, 0, total_w, total_h, fill=BG_COLOR, stroke='none')
    svg.text(total_w / 2, 5, "Power Tree",
             font_size=3.5, fill='#202020', anchor='middle', bold=True)

    # Draw rail boxes
    for rail, (x, y) in rail_pos.items():
        svg.rect(x, y, rail_w, rail_h, stroke=POWER_STROKE, fill=POWER_FILL,
                 stroke_width=0.3, rx=BOX_CORNER_RADIUS)
        svg.text(x + rail_w / 2, y + rail_h / 2, rail,
                 font_size=FONT_SIZE, fill=POWER_FONT,
                 anchor='middle', dominant_baseline='central', bold=True)

    # Draw regulator boxes with detail annotations
    for in_rail, out_rail, reg in edges:
        if in_rail not in rail_pos or out_rail not in rail_pos:
            continue
        in_x, in_y = rail_pos[in_rail]
        out_x, out_y = rail_pos[out_rail]

        # Regulator box centered between rails
        reg_x = (in_x + rail_w + out_x) / 2 - reg_w / 2
        reg_y = (in_y + out_y) / 2 + (rail_h - reg_h) / 2

        ref = reg.get('ref', '?')
        value = reg.get('value', '')
        topology = reg.get('topology', '')
        vout = reg.get('estimated_vout')
        inductor = reg.get('inductor', '')

        # Regulator box
        svg.rect(reg_x, reg_y, reg_w, reg_h, stroke=BOX_STROKE, fill=BOX_FILL,
                 stroke_width=0.3, rx=BOX_CORNER_RADIUS)

        # Line 1: ref + value
        svg.text(reg_x + reg_w / 2, reg_y + 4, f"{ref}: {value}",
                 font_size=detail_font, fill=BOX_FONT,
                 anchor='middle', dominant_baseline='central', bold=True)

        # Line 2: topology + Vout
        line2 = topology.upper()
        if vout:
            line2 += f"  →  {vout:.2f}V"
        svg.text(reg_x + reg_w / 2, reg_y + 8.5,
                 line2, font_size=detail_font, fill=BOX_FONT,
                 anchor='middle', dominant_baseline='central')

        # Line 3: inductor
        if inductor:
            # Find inductor value from components
            ind_value = ''
            for comp in analysis.get('components', []):
                if comp.get('reference') == inductor:
                    ind_value = comp.get('value', '')
                    break
            ind_text = f"L: {inductor}" + (f" ({ind_value})" if ind_value else "")
            svg.text(reg_x + reg_w / 2, reg_y + 12.5,
                     ind_text, font_size=cap_font, fill='#606060',
                     anchor='middle', dominant_baseline='central')

        # Line 4: output caps summary
        out_caps = reg.get('output_capacitors', [])
        cap_summary = _format_cap_summary(out_caps)
        if cap_summary:
            svg.text(reg_x + reg_w / 2, reg_y + 16,
                     f"Cout: {cap_summary}", font_size=cap_font, fill='#606060',
                     anchor='middle', dominant_baseline='central')

        # Arrows
        _draw_arrow(svg, in_x + rail_w, in_y + rail_h / 2,
                    reg_x, reg_y + reg_h / 2)
        _draw_arrow(svg, reg_x + reg_w, reg_y + reg_h / 2,
                    out_x, out_y + rail_h / 2)

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    svg.write(output_path)
    return output_path


# ======================================================================
# Bus Topology Diagram
# ======================================================================

def generate_bus_topology(analysis: dict, output_path: str) -> str | None:
    """Generate a bus topology SVG showing I2C, SPI, UART, CAN buses."""
    bus_analysis = analysis.get('design_analysis', {}).get('bus_analysis', {})

    # Collect non-empty buses
    buses = []
    for bus_type in ('i2c', 'spi', 'uart', 'can'):
        bus_list = bus_analysis.get(bus_type, [])
        if isinstance(bus_list, list):
            for bus in bus_list:
                if isinstance(bus, dict):
                    buses.append((bus_type.upper(), bus))

    if not buses:
        return None

    # Layout: stack buses vertically
    bus_h = 30.0
    bus_spacing = 10.0
    margin = 15.0
    device_w = 25.0
    device_h = 10.0
    device_spacing = 8.0

    total_h = margin * 2 + len(buses) * (bus_h + bus_spacing)
    total_w = 250.0

    svg = SvgBuilder(total_w, total_h)
    svg.rect(0, 0, total_w, total_h, fill=BG_COLOR, stroke='none')
    svg.text(total_w / 2, 6, "Bus Topology",
             font_size=4, fill='#202020', anchor='middle', bold=True)

    y_offset = margin + 5

    for bus_type, bus_data in buses:
        # Bus type label
        svg.text(margin, y_offset + 4, bus_type,
                 font_size=FONT_SIZE, fill=BUS_FONT, bold=True)

        # Central bus line
        bus_line_y = y_offset + bus_h / 2
        bus_line_x1 = margin + 20
        bus_line_x2 = total_w - margin
        _draw_bus_line(svg, bus_line_x1, bus_line_y, bus_line_x2, bus_line_y,
                       color=BUS_STROKE, width=0.8)

        # Extract devices on this bus
        devices = []
        # Different bus data formats — handle flexibly
        for key in ('devices', 'peripherals', 'members', 'endpoints'):
            devs = bus_data.get(key, [])
            if isinstance(devs, list):
                devices.extend(devs)
        # Also check for master/slave structure
        master = bus_data.get('master') or bus_data.get('controller')
        if master:
            if isinstance(master, str):
                devices.insert(0, {'ref': master, 'role': 'master'})
            elif isinstance(master, dict):
                master['role'] = 'master'
                devices.insert(0, master)

        # Deduplicate by ref
        seen = set()
        unique_devices = []
        for d in devices:
            ref = d.get('ref', '') if isinstance(d, dict) else str(d)
            if ref and ref not in seen:
                seen.add(ref)
                unique_devices.append(d)

        # Draw device boxes
        n_devices = len(unique_devices)
        if n_devices == 0:
            # Show bus signals instead
            signals = bus_data.get('signals', bus_data.get('nets', []))
            if isinstance(signals, list):
                for i, sig in enumerate(signals[:8]):
                    sig_name = sig.get('name', str(sig)) if isinstance(sig, dict) else str(sig)
                    x = bus_line_x1 + 10 + i * (device_w + 3)
                    svg.text(x, bus_line_y - 3, sig_name,
                             font_size=SMALL_FONT, fill=LABEL_FONT)
        else:
            spacing = min(device_spacing + device_w,
                          (bus_line_x2 - bus_line_x1 - 10) / max(n_devices, 1))
            for i, dev in enumerate(unique_devices):
                if isinstance(dev, dict):
                    ref = dev.get('ref', '?')
                    role = dev.get('role', '')
                    value = dev.get('value', '')
                else:
                    ref = str(dev)
                    role = ''
                    value = ''

                dx = bus_line_x1 + 10 + i * spacing
                dy = bus_line_y - device_h - 3
                if i % 2 == 1:
                    dy = bus_line_y + 3  # alternate above/below

                fill = POWER_FILL if role == 'master' else BOX_FILL
                _draw_box(svg, dx, dy, device_w, device_h, ref,
                          sublabel=value[:15] if value else role,
                          fill=fill, stroke=BOX_STROKE)

                # Drop line to bus
                conn_y = dy + device_h if dy < bus_line_y else dy
                svg.line(dx + device_w / 2, conn_y,
                         dx + device_w / 2, bus_line_y,
                         stroke=ARROW_COLOR, stroke_width=0.3)

        y_offset += bus_h + bus_spacing

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    svg.write(output_path)
    return output_path


# ======================================================================
# Architecture Block Diagram
# ======================================================================

def _classify_component(comp: dict, regulator_refs: set,
                        protection_refs: set) -> str | None:
    """Classify a component into an architecture cluster.

    Uses multiple signals in priority order:
    1. Known regulator refs from signal_analysis.power_regulators
    2. Component type field (most reliable)
    3. Library ID keywords (fallback)

    Returns cluster name or None to skip.
    """
    ref = comp.get('reference', '')
    ctype = comp.get('type', '')
    lib_id = comp.get('lib_id', '')
    value = comp.get('value', '')

    # Skip passives and non-functional components
    if ctype in ('resistor', 'capacitor', 'inductor', 'ferrite_bead',
                  'diode', 'test_point', 'mounting_hole', 'fiducial',
                  'power_symbol', 'graphic'):
        return None
    if ref.startswith('#'):
        return None

    # 1. Explicit regulator match from signal analysis (highest confidence)
    if ref in regulator_refs:
        return 'Power'

    # 2. Protection devices (ESD, TVS) — separate from Power
    lib_lower = lib_id.lower()
    if ref in protection_refs or 'protection' in lib_lower or 'tvs' in lib_lower:
        return 'Protection'

    # 3. Type-based classification (reliable — set by kicad_utils.classify_component)
    if ctype == 'connector':
        return 'Connectors'
    if ctype == 'battery':
        return 'Power'
    if ctype == 'fuse':
        return 'Protection'
    if ctype == 'led':
        return 'LEDs / Display'
    if ctype == 'buzzer':
        return 'Audio / Output'
    if ctype in ('crystal', 'oscillator'):
        return None  # Skip — these are support components, not functional blocks

    # 4. MCU / processor detection (lib_id keywords)
    mcu_keywords = ('mcu', 'stm32', 'esp32', 'rp2040', 'atmega', 'pic16',
                    'pic32', 'nrf', 'samd', 'microcontroller', 'processor',
                    'esp32-s', 'esp32-c', 'wroom', 'wrover')
    if any(k in lib_lower for k in mcu_keywords):
        return 'MCU / CPU'

    # 5. Memory
    if any(k in lib_lower for k in ('memory', 'flash', 'eeprom',
                                      'sram', 'sdram', 'w25q', 'at24')):
        return 'Memory'

    # 6. Communication / RF
    if any(k in lib_lower for k in ('uart', 'ethernet', 'can_',
                                      'wifi', 'bluetooth', 'rf_',
                                      'transceiver', 'phy', 'lora')):
        return 'Communication'

    # 7. Sensors
    if any(k in lib_lower for k in ('sensor', 'accel', 'gyro', 'temp',
                                      'humidity', 'pressure', 'bme', 'bmp',
                                      'mpu', 'lsm', 'lis')):
        return 'Sensors'

    # 8. Regulator keywords (catches regulators not in signal_analysis)
    if any(k in lib_lower for k in ('regulator', 'ldo', 'buck', 'boost',
                                      'charge', 'pmic', 'dcdc')):
        return 'Power'

    # 9. Remaining ICs and active components
    if ctype in ('ic', 'transistor', 'mosfet', 'relay', 'switch'):
        return 'Other ICs'

    return None


def generate_architecture(analysis: dict, output_path: str) -> str | None:
    """Generate a system architecture block diagram.

    Clusters ICs by function using signal analysis data, component types,
    and library ID keywords.  Dynamically sizes boxes to fit labels.
    """
    components = analysis.get('components', [])
    signal_analysis = analysis.get('signal_analysis', {})

    # Build lookup sets from signal analysis (high-confidence classification)
    regulator_refs = {r['ref'] for r in signal_analysis.get('power_regulators', [])
                      if isinstance(r, dict) and r.get('ref')}
    protection_refs = set()
    for e in signal_analysis.get('esd_coverage_audit', []):
        if isinstance(e, dict):
            for dev in e.get('esd_devices', []):
                if isinstance(dev, dict) and dev.get('ref'):
                    protection_refs.add(dev['ref'])

    # Classify all components
    clusters: dict[str, list[dict]] = {}
    for comp in components:
        cluster = _classify_component(comp, regulator_refs, protection_refs)
        if cluster:
            clusters.setdefault(cluster, []).append(comp)

    # Remove empty clusters
    clusters = {k: v for k, v in clusters.items() if v}
    if not clusters:
        return None

    # Build labels for each component (no truncation)
    def _comp_label(comp: dict) -> str:
        ref = comp.get('reference', '?')
        value = comp.get('value', '')
        if value:
            return f"{ref}: {value}"
        return ref

    # Calculate dynamic box widths based on longest label
    cluster_spacing_x = 12.0
    cluster_spacing_y = 10.0
    margin = 12.0
    char_width = SMALL_FONT * 0.55  # approximate character width

    cluster_widths: dict[str, float] = {}
    for name, comps in clusters.items():
        labels = [_comp_label(c) for c in comps[:8]]
        max_label_len = max((len(l) for l in labels), default=0)
        title_len = len(name)
        max_chars = max(max_label_len, title_len)
        cluster_widths[name] = max(30, max_chars * char_width + 8)

    # Layout order (logical grouping)
    layout_order = ['Power', 'MCU / CPU', 'Communication', 'Memory',
                    'Sensors', 'Connectors', 'Protection',
                    'LEDs / Display', 'Audio / Output', 'Other ICs']
    ordered = [(k, clusters[k]) for k in layout_order if k in clusters]

    # 3-column layout: left=power/protection, center=MCU, right=peripherals
    left_clusters = ['Power', 'Protection', 'Other ICs']
    center_clusters = ['MCU / CPU']
    right_clusters = ['Communication', 'Memory', 'Sensors', 'Connectors',
                      'LEDs / Display', 'Audio / Output']

    # Position columns
    positions: dict[str, tuple[float, float, float, float]] = {}

    def _layout_column(cluster_names: list[str], col_x: float, start_y: float):
        y = start_y
        for name in cluster_names:
            if name not in clusters:
                continue
            comps = clusters[name]
            w = cluster_widths.get(name, 50)
            n_items = min(len(comps), 8)
            h = max(14, 8 + n_items * 3.5)
            positions[name] = (col_x, y, w, h)
            y += h + cluster_spacing_y

    # Compute column widths for positioning
    left_w = max((cluster_widths.get(n, 30) for n in left_clusters if n in clusters), default=40)
    center_w = max((cluster_widths.get(n, 40) for n in center_clusters if n in clusters), default=50)

    col1_x = margin
    col2_x = col1_x + left_w + cluster_spacing_x
    col3_x = col2_x + center_w + cluster_spacing_x

    start_y = margin + 8
    _layout_column(left_clusters, col1_x, start_y)
    _layout_column(center_clusters, col2_x, start_y)
    _layout_column(right_clusters, col3_x, start_y)

    # Compute SVG size
    if not positions:
        return None
    max_x = max(p[0] + p[2] for p in positions.values()) + margin
    max_y = max(p[1] + p[3] for p in positions.values()) + margin
    total_w = max(max_x, 150)
    total_h = max(max_y, 80)

    svg = SvgBuilder(total_w, total_h)
    svg.rect(0, 0, total_w, total_h, fill=BG_COLOR, stroke='none')
    svg.text(total_w / 2, 5, "System Architecture",
             font_size=3.5, fill='#202020', anchor='middle', bold=True)

    # Color mapping
    color_map = {
        'MCU / CPU': (BOX_FILL, BOX_STROKE, BOX_FONT),
        'Power': (POWER_FILL, POWER_STROKE, POWER_FONT),
        'Communication': (BUS_FILL, BUS_STROKE, BUS_FONT),
        'Memory': ('#e8f0ff', '#4060c0', '#203060'),
        'Sensors': ('#f0ffe0', '#40a040', '#204020'),
        'Connectors': (IO_FILL, IO_STROKE, IO_FONT),
        'Protection': ('#fff0f0', '#c06060', '#804040'),
        'LEDs / Display': ('#fff8e0', '#c0a000', '#806000'),
        'Audio / Output': ('#f0f0ff', '#6060c0', '#404080'),
        'Other ICs': ('#f0f0f0', '#808080', '#404040'),
    }

    for name, comps in ordered:
        if name not in positions:
            continue
        x, y, w, h = positions[name]
        fill, stroke, font_color = color_map.get(name, (BOX_FILL, BOX_STROKE, BOX_FONT))

        svg.rect(x, y, w, h, stroke=stroke, fill=fill,
                 stroke_width=0.3, rx=BOX_CORNER_RADIUS)
        svg.text(x + w / 2, y + 3.5, name,
                 font_size=FONT_SIZE, fill=font_color,
                 anchor='middle', dominant_baseline='central', bold=True)

        # List components — no truncation
        for i, comp in enumerate(comps[:8]):
            label = _comp_label(comp)
            svg.text(x + 2.5, y + 8 + i * 3.2, label,
                     font_size=SMALL_FONT, fill=font_color)
        if len(comps) > 8:
            svg.text(x + 2.5, y + 8 + 8 * 3.2,
                     f"+{len(comps) - 8} more",
                     font_size=SMALL_FONT, fill=font_color, italic=True)

    # Draw connection arrows between MCU and other clusters
    mcu_pos = positions.get('MCU / CPU')
    if mcu_pos:
        for name in positions:
            if name == 'MCU / CPU':
                continue
            other = positions[name]
            # Connect from nearest edges
            mcu_cx = mcu_pos[0] + mcu_pos[2] / 2
            other_cx = other[0] + other[2] / 2
            if other_cx < mcu_cx:
                ax1 = mcu_pos[0]
                ax2 = other[0] + other[2]
            else:
                ax1 = mcu_pos[0] + mcu_pos[2]
                ax2 = other[0]
            ay1 = mcu_pos[1] + mcu_pos[3] / 2
            ay2 = other[1] + other[3] / 2
            _draw_arrow(svg, ax1, ay1, ax2, ay2, color='#b0b0d0',
                        stroke_width=0.25)

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    svg.write(output_path)
    return output_path


# ======================================================================
# Main
# ======================================================================

def generate_all(analysis: dict, output_dir: str) -> list[str]:
    """Generate all applicable diagrams. Returns list of output paths."""
    os.makedirs(output_dir, exist_ok=True)
    outputs = []

    path = generate_power_tree(analysis, os.path.join(output_dir, 'power_tree.svg'))
    if path:
        outputs.append(path)

    path = generate_bus_topology(analysis, os.path.join(output_dir, 'bus_topology.svg'))
    if path:
        outputs.append(path)

    path = generate_architecture(analysis, os.path.join(output_dir, 'architecture.svg'))
    if path:
        outputs.append(path)

    return outputs


def main():
    parser = argparse.ArgumentParser(description='Generate block diagrams from analysis JSON')
    parser.add_argument('--analysis', '-a', required=True,
                        help='Path to schematic analysis JSON')
    parser.add_argument('--output', '-o', required=True,
                        help='Output directory for SVGs')
    parser.add_argument('--power-tree', action='store_true',
                        help='Generate power tree diagram')
    parser.add_argument('--bus-topology', action='store_true',
                        help='Generate bus topology diagram')
    parser.add_argument('--architecture', action='store_true',
                        help='Generate architecture block diagram')
    parser.add_argument('--all', action='store_true',
                        help='Generate all applicable diagrams')
    args = parser.parse_args()

    with open(args.analysis) as f:
        analysis = json.load(f)

    os.makedirs(args.output, exist_ok=True)
    generated = []

    if args.all or args.power_tree:
        path = generate_power_tree(analysis, os.path.join(args.output, 'power_tree.svg'))
        if path:
            generated.append(path)

    if args.all or args.bus_topology:
        path = generate_bus_topology(analysis, os.path.join(args.output, 'bus_topology.svg'))
        if path:
            generated.append(path)

    if args.all or args.architecture:
        path = generate_architecture(analysis, os.path.join(args.output, 'architecture.svg'))
        if path:
            generated.append(path)

    if not generated:
        print("No diagrams generated (no applicable data found)", file=sys.stderr)
    else:
        for p in generated:
            print(p, file=sys.stderr)


if __name__ == '__main__':
    main()
