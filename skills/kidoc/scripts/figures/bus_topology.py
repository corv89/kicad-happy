"""Bus topology diagram generator.

Renders a bus topology SVG showing I2C, SPI, UART, and CAN buses
with their connected devices.
"""

from __future__ import annotations

import os

from .styles import (
    SvgBuilder,
    BOX_FILL, BOX_STROKE,
    POWER_FILL,
    BUS_STROKE, BUS_FONT,
    ARROW_COLOR, LABEL_FONT, BG_COLOR,
    FONT_SIZE, SMALL_FONT,
    _draw_box, _draw_bus_line,
)


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
        # Different bus data formats -- handle flexibly
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
