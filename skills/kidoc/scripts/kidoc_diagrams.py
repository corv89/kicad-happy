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

def generate_power_tree(analysis: dict, output_path: str) -> str | None:
    """Generate a power tree SVG from schematic analysis JSON.

    Reads signal_analysis.power_regulators and design_analysis.power_domains
    to build a DAG of power rails and regulators.
    """
    regulators = analysis.get('signal_analysis', {}).get('power_regulators', [])
    if not regulators:
        return None

    # Build a graph: input_rail -> regulator -> output_rail
    # Nodes are rails (strings), edges are regulators
    rails = set()
    edges = []  # (input_rail, output_rail, regulator_info)
    for reg in regulators:
        in_rail = reg.get('input_rail', '?')
        out_rail = reg.get('output_rail', '?')
        rails.add(in_rail)
        rails.add(out_rail)
        edges.append((in_rail, out_rail, reg))

    # Topological sort for left-to-right layout
    # Find root rails (no regulator outputs to them, or they're input-only)
    output_rails = {e[1] for e in edges}
    input_rails = {e[0] for e in edges}
    root_rails = input_rails - output_rails
    if not root_rails:
        root_rails = input_rails  # fallback: all inputs are roots

    # BFS to assign depth (rank) to each rail
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

    # Assign any unvisited rails
    for r in rails:
        if r not in depth:
            depth[r] = 0

    # Group rails by depth
    max_depth = max(depth.values()) if depth else 0
    ranks: dict[int, list[str]] = {}
    for rail, d in depth.items():
        ranks.setdefault(d, []).append(rail)

    # Layout
    box_w = 35.0
    box_h = 14.0
    reg_w = 30.0
    reg_h = 10.0
    h_spacing = 55.0
    v_spacing = 25.0
    margin = 15.0

    # Compute positions for rail boxes
    rail_pos: dict[str, tuple[float, float]] = {}
    for d in range(max_depth + 1):
        rank_rails = sorted(ranks.get(d, []))
        for i, rail in enumerate(rank_rails):
            x = margin + d * h_spacing
            y = margin + i * v_spacing
            rail_pos[rail] = (x, y)

    # Compute SVG size
    max_x = max((p[0] for p in rail_pos.values()), default=0) + box_w + margin
    max_y = max((p[1] for p in rail_pos.values()), default=0) + box_h + margin
    # Add space for regulators between rails
    total_w = max(max_x + margin, 200)
    total_h = max(max_y + margin, 80)

    svg = SvgBuilder(total_w, total_h)
    svg.rect(0, 0, total_w, total_h, fill=BG_COLOR, stroke='none')

    # Title
    svg.text(total_w / 2, 6, "Power Tree",
             font_size=4, fill='#202020', anchor='middle', bold=True)

    # Draw rail boxes
    for rail, (x, y) in rail_pos.items():
        _draw_box(svg, x, y, box_w, box_h, rail, fill=POWER_FILL,
                  stroke=POWER_STROKE, font_color=POWER_FONT)

    # Draw regulator boxes and arrows
    for in_rail, out_rail, reg in edges:
        if in_rail not in rail_pos or out_rail not in rail_pos:
            continue
        in_x, in_y = rail_pos[in_rail]
        out_x, out_y = rail_pos[out_rail]

        # Regulator box positioned between the two rails
        reg_x = (in_x + box_w + out_x) / 2 - reg_w / 2
        reg_y = (in_y + out_y) / 2 + (box_h - reg_h) / 2

        ref = reg.get('ref', '?')
        value = reg.get('value', '')
        topology = reg.get('topology', '')
        vout = reg.get('estimated_vout')
        sublabel = topology
        if vout:
            sublabel = f"{topology} → {vout:.2f}V"

        _draw_box(svg, reg_x, reg_y, reg_w, reg_h, f"{ref}",
                  sublabel=sublabel, fill=BOX_FILL, stroke=BOX_STROKE)

        # Arrow from input rail to regulator
        _draw_arrow(svg, in_x + box_w, in_y + box_h / 2,
                    reg_x, reg_y + reg_h / 2)
        # Arrow from regulator to output rail
        _draw_arrow(svg, reg_x + reg_w, reg_y + reg_h / 2,
                    out_x, out_y + box_h / 2)

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

def generate_architecture(analysis: dict, output_path: str) -> str | None:
    """Generate a system architecture block diagram.

    Clusters ICs by function and shows connections between them.
    """
    components = analysis.get('components', [])
    signal_analysis = analysis.get('signal_analysis', {})
    design_analysis = analysis.get('design_analysis', {})

    # Classify components into functional clusters
    clusters: dict[str, list[dict]] = {
        'MCU / CPU': [],
        'Power': [],
        'Communication': [],
        'Memory': [],
        'Sensors': [],
        'Connectors': [],
        'Other ICs': [],
    }

    for comp in components:
        ctype = comp.get('type', '')
        ref = comp.get('reference', '')
        value = comp.get('value', '')
        lib_id = comp.get('lib_id', '')

        # Skip passives
        if ctype in ('resistor', 'capacitor', 'inductor', 'ferrite_bead',
                      'diode', 'test_point', 'mounting_hole', 'fiducial',
                      'power_symbol', 'graphic'):
            continue
        if ref.startswith('#'):
            continue

        # Classify
        lib_lower = lib_id.lower()
        val_lower = value.lower()

        if any(k in lib_lower for k in ('mcu', 'stm32', 'esp32', 'rp2040',
                                          'atmega', 'pic', 'nrf', 'samd',
                                          'microcontroller', 'processor')):
            clusters['MCU / CPU'].append(comp)
        elif any(k in lib_lower for k in ('regulator', 'power', 'ldo',
                                            'buck', 'boost', 'charge')):
            clusters['Power'].append(comp)
        elif any(k in lib_lower for k in ('uart', 'usb', 'ethernet', 'can',
                                            'wifi', 'bluetooth', 'rf',
                                            'transceiver', 'phy')):
            clusters['Communication'].append(comp)
        elif any(k in lib_lower for k in ('memory', 'flash', 'eeprom',
                                            'sram', 'sdram', 'w25q')):
            clusters['Memory'].append(comp)
        elif any(k in lib_lower for k in ('sensor', 'accel', 'gyro', 'temp',
                                            'humidity', 'pressure', 'adc')):
            clusters['Sensors'].append(comp)
        elif any(k in lib_lower for k in ('connector', 'conn_', 'usb_',
                                            'barrel', 'header', 'jack')):
            clusters['Connectors'].append(comp)
        elif ctype in ('ic', 'transistor', 'mosfet', 'led', 'crystal',
                        'oscillator', 'relay', 'switch'):
            clusters['Other ICs'].append(comp)

    # Remove empty clusters
    clusters = {k: v for k, v in clusters.items() if v}
    if not clusters:
        return None

    # Layout: grid of cluster boxes
    cluster_w = 50.0
    cluster_h_min = 18.0
    cluster_spacing_x = 15.0
    cluster_spacing_y = 12.0
    margin = 15.0

    # Position clusters in a logical arrangement
    layout_order = ['Power', 'MCU / CPU', 'Communication',
                    'Memory', 'Sensors', 'Connectors', 'Other ICs']
    ordered = [(k, clusters[k]) for k in layout_order if k in clusters]

    # 2-column layout: left column for power/sensors, right for comms/memory
    # MCU in the center
    positions: dict[str, tuple[float, float, float, float]] = {}
    col1_x = margin
    col2_x = margin + cluster_w + cluster_spacing_x
    col3_x = margin + 2 * (cluster_w + cluster_spacing_x)

    y = margin + 10
    for name, comps in ordered:
        n_items = len(comps)
        h = max(cluster_h_min, 8 + n_items * 4)

        if name == 'MCU / CPU':
            x = col2_x
        elif name in ('Power', 'Sensors', 'Other ICs'):
            x = col1_x
        else:
            x = col3_x

        positions[name] = (x, y, cluster_w, h)
        y += h + cluster_spacing_y

    # Compute SVG size
    max_x = max(p[0] + p[2] for p in positions.values()) + margin
    max_y = max(p[1] + p[3] for p in positions.values()) + margin
    total_w = max(max_x, 200)
    total_h = max(max_y, 100)

    svg = SvgBuilder(total_w, total_h)
    svg.rect(0, 0, total_w, total_h, fill=BG_COLOR, stroke='none')
    svg.text(total_w / 2, 6, "System Architecture",
             font_size=4, fill='#202020', anchor='middle', bold=True)

    # Color mapping
    color_map = {
        'MCU / CPU': (BOX_FILL, BOX_STROKE, BOX_FONT),
        'Power': (POWER_FILL, POWER_STROKE, POWER_FONT),
        'Communication': (BUS_FILL, BUS_STROKE, BUS_FONT),
        'Memory': ('#e8f0ff', '#4060c0', '#203060'),
        'Sensors': ('#f0ffe0', '#40a040', '#204020'),
        'Connectors': (IO_FILL, IO_STROKE, IO_FONT),
        'Other ICs': ('#f0f0f0', '#808080', '#404040'),
    }

    for name, comps in ordered:
        if name not in positions:
            continue
        x, y, w, h = positions[name]
        fill, stroke, font_color = color_map.get(name, (BOX_FILL, BOX_STROKE, BOX_FONT))

        svg.rect(x, y, w, h, stroke=stroke, fill=fill,
                 stroke_width=0.3, rx=BOX_CORNER_RADIUS)
        svg.text(x + w / 2, y + 4, name,
                 font_size=FONT_SIZE, fill=font_color,
                 anchor='middle', dominant_baseline='central', bold=True)

        # List components inside the cluster
        for i, comp in enumerate(comps[:6]):
            ref = comp.get('reference', '?')
            value = comp.get('value', '')
            label = f"{ref}: {value[:12]}" if value else ref
            svg.text(x + 3, y + 9 + i * 3.5, label,
                     font_size=SMALL_FONT, fill=font_color)
        if len(comps) > 6:
            svg.text(x + 3, y + 9 + 6 * 3.5,
                     f"... +{len(comps) - 6} more",
                     font_size=SMALL_FONT, fill=font_color, italic=True)

    # Draw connection arrows between clusters that share buses/signals
    mcu_pos = positions.get('MCU / CPU')
    if mcu_pos:
        for name in ('Memory', 'Communication', 'Sensors', 'Connectors', 'Power'):
            if name in positions:
                other = positions[name]
                # Arrow from MCU to other cluster
                mx = mcu_pos[0] + mcu_pos[2] / 2
                my = mcu_pos[1] + mcu_pos[3] / 2
                ox = other[0] + other[2] / 2
                oy = other[1] + other[3] / 2
                # Connect from nearest edges
                if ox < mx:
                    ax1, ax2 = mcu_pos[0], other[0] + other[2]
                else:
                    ax1, ax2 = mcu_pos[0] + mcu_pos[2], other[0]
                ay1 = mcu_pos[1] + mcu_pos[3] / 2
                ay2 = other[1] + other[3] / 2
                _draw_arrow(svg, ax1, ay1, ax2, ay2, color='#a0a0c0')

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
