"""Pinout diagram generator.

Renders publication-quality SVG pinout diagrams for connectors.
Shows physical pin arrangement with signal names, direction arrows,
color coding, and ESD protection indicators.
"""

from __future__ import annotations

import os
import re

from .styles import SvgBuilder


# ======================================================================
# Colors and constants
# ======================================================================

PIN_COLORS = {
    'power':  ('#ffebee', '#c62828', '#b71c1c'),   # light red fill, dark red stroke, text
    'ground': ('#eceff1', '#37474f', '#263238'),     # light gray fill, dark stroke, text
    'signal': ('#e3f2fd', '#1565c0', '#0d47a1'),     # light blue fill, blue stroke, text
    'nc':     ('#f5f5f5', '#bdbdbd', '#9e9e9e'),     # very light gray, gray stroke, text
}

ESD_PROTECTED_COLOR = '#43a047'     # green dot
ESD_UNPROTECTED_COLOR = '#c62828'   # red dot

DIRECTION_ARROWS = {
    'input': '\u2192',         # into the connector
    'output': '\u2190',        # out of the connector
    'bidirectional': '\u2194',
    'passive': '',
    'power_in': '',
    'power_out': '',
    'no_connect': '',
    'unspecified': '',
    'tri_state': '\u2194',
    'open_collector': '\u2190',
    'open_emitter': '\u2190',
    'free': '',
}

# Dimensions (mm)
PIN_W = 8.0
PIN_H = 5.0
PIN_GAP = 1.5          # vertical gap between pins
PIN_RADIUS = 1.0
DUAL_COL_GAP = 1.5     # horizontal gap between dual-row columns
LABEL_FONT = 3.0
NET_FONT = 2.2
TITLE_FONT = 4.5
LEGEND_FONT = 2.5
PIN_NUM_FONT = 2.5
ESD_DOT_R = 0.75
MARGIN = 8.0
TITLE_H = 10.0
LEGEND_H = 12.0
LABEL_GAP = 2.5        # gap between pin rect edge and label text
BG_COLOR = '#ffffff'


# ======================================================================
# Classification helpers
# ======================================================================

def _detect_layout(lib_id: str, value: str, pin_count: int) -> tuple[str, int]:
    """Detect connector layout from library ID.

    Returns (layout_type, pins_per_side):
    - ('single', N) -- single row of N pins
    - ('dual', N) -- two rows of N pins each
    - ('usb_c', 12) -- USB-C simplified functional groups
    - ('barrel', 3) -- barrel jack (tip, ring, sleeve)
    """
    lib_lower = (lib_id + ' ' + value).lower()

    # Dual row pin headers: PinHeader_2xN, Conn_02xN
    m = re.search(r'(?:pinheader|conn)[_ ]?0?2x(\d+)', lib_lower)
    if m:
        return ('dual', int(m.group(1)))

    # Single row: PinHeader_1xN, Conn_01xN, Conn_1xN
    m = re.search(r'(?:pinheader|conn)[_ ]?0?1x(\d+)', lib_lower)
    if m:
        return ('single', int(m.group(1)))

    # USB-C
    if 'usb_c' in lib_lower or 'usb-c' in lib_lower:
        return ('usb_c', 12)

    # Barrel jack
    if 'barrel' in lib_lower:
        return ('barrel', pin_count)

    # RJ45 / Ethernet
    if 'rj45' in lib_lower or '8p8c' in lib_lower:
        return ('single', 8)

    # RJ11 / Phone
    if 'rj11' in lib_lower or '6p6c' in lib_lower:
        return ('single', 6)

    # Screw terminal: Screw_Terminal_01x03 -> 3 pins
    m = re.search(r'screw_terminal[_ ]?0?1x(\d+)', lib_lower)
    if m:
        return ('single', int(m.group(1)))
    # Bare screw terminal count: Screw_Terminal_3 -> 3 pins
    m = re.search(r'screw_terminal[_ ](\d+)', lib_lower)
    if m:
        return ('single', int(m.group(1)))

    # Fallback: single row if <=6, dual row otherwise
    if pin_count <= 6:
        return ('single', pin_count)
    else:
        return ('dual', (pin_count + 1) // 2)


def _classify_pin(pin_name: str, net_name: str, pin_type: str) -> str:
    """Classify pin for color coding.

    Returns: 'power', 'ground', 'signal', 'nc'
    """
    name_lower = (pin_name + ' ' + net_name).lower()

    if pin_type == 'no_connect' or 'nc' in name_lower or net_name.startswith('__unnamed'):
        return 'nc'
    if any(g in name_lower for g in ('gnd', 'vss', 'ground', 'pgnd', 'agnd', 'dgnd')):
        return 'ground'
    if any(p in name_lower for p in ('vcc', 'vdd', 'vin', 'vbus', '5v', '3v3', '3.3v',
                                      '12v', 'vbat', 'vsys', 'vout', '+5v', '+3v3', '+12v')):
        return 'power'
    return 'signal'


def _sort_key(pin_number: str) -> tuple[int, str]:
    """Sort key that orders numeric pins numerically, alpha pins alphabetically."""
    m = re.match(r'^(\d+)', pin_number)
    if m:
        return (int(m.group(1)), pin_number)
    return (999999, pin_number)


def _estimate_text_width(text: str, font_size: float) -> float:
    """Rough text width estimate (mm) based on average character width."""
    return len(text) * font_size * 0.55


# ======================================================================
# Drawing helpers
# ======================================================================

def _draw_pin_rect(svg: SvgBuilder, x: float, y: float,
                   pin_number: str, classification: str) -> None:
    """Draw a single pin rectangle with its number centered."""
    fill, stroke, _text = PIN_COLORS[classification]
    svg.rect(x, y, PIN_W, PIN_H, stroke=stroke, fill=fill,
             stroke_width=0.3, rx=PIN_RADIUS)
    svg.text(x + PIN_W / 2, y + PIN_H / 2, pin_number,
             font_size=PIN_NUM_FONT, fill=stroke,
             anchor='middle', dominant_baseline='central', bold=True)


def _draw_esd_dot(svg: SvgBuilder, cx: float, cy: float,
                  protected: bool) -> None:
    """Draw a small ESD status dot."""
    color = ESD_PROTECTED_COLOR if protected else ESD_UNPROTECTED_COLOR
    svg.circle(cx, cy, ESD_DOT_R, fill=color, stroke='none')


def _draw_label(svg: SvgBuilder, x: float, y: float,
                pin_name: str, net_name: str, pin_type: str,
                classification: str, anchor: str = 'start') -> float:
    """Draw signal name, direction arrow, and net name.

    Returns the total width consumed by the label.
    """
    _fill, _stroke, text_color = PIN_COLORS[classification]
    arrow = DIRECTION_ARROWS.get(pin_type, '')

    # Build display label: signal name + arrow
    if pin_name and pin_name != '~':
        display = pin_name
    elif net_name and not net_name.startswith('__unnamed'):
        display = net_name
    else:
        display = 'NC'

    if arrow:
        if anchor == 'end':
            display = arrow + ' ' + display
        else:
            display = display + ' ' + arrow

    svg.text(x, y, display,
             font_size=LABEL_FONT, fill=text_color,
             anchor=anchor, dominant_baseline='central')

    width = _estimate_text_width(display, LABEL_FONT)

    # Net name subtitle (if different from signal name and informative)
    show_net = (net_name and pin_name and pin_name != '~'
                and net_name != pin_name
                and not net_name.startswith('__unnamed'))
    if show_net:
        net_x = x
        svg.text(net_x, y + LABEL_FONT + 0.5, net_name,
                 font_size=NET_FONT, fill='#888888',
                 anchor=anchor, dominant_baseline='central',
                 italic=True)
        net_w = _estimate_text_width(net_name, NET_FONT)
        width = max(width, net_w)

    return width


# ======================================================================
# Layout renderers
# ======================================================================

def _render_single(svg: SvgBuilder, pins: list[dict],
                   esd_map: dict[str, bool],
                   start_x: float, start_y: float,
                   has_esd: bool) -> tuple[float, float]:
    """Render a single-column connector. Returns (total_width, total_height)."""
    pins_sorted = sorted(pins, key=lambda p: _sort_key(p['pin_number']))

    # First pass: measure label widths
    max_label_w = 0.0
    for pin in pins_sorted:
        cls = _classify_pin(pin['pin_name'], pin['net_name'], pin['pin_type'])
        arrow = DIRECTION_ARROWS.get(pin['pin_type'], '')
        name = pin['pin_name'] if pin['pin_name'] != '~' else pin['net_name']
        if not name or name.startswith('__unnamed'):
            name = 'NC'
        display = name + (' ' + arrow if arrow else '')
        w = _estimate_text_width(display, LABEL_FONT)
        # Check for net subtitle
        if (pin['net_name'] and pin['pin_name'] != '~'
                and pin['net_name'] != pin['pin_name']
                and not pin['net_name'].startswith('__unnamed')):
            net_w = _estimate_text_width(pin['net_name'], NET_FONT)
            w = max(w, net_w)
        max_label_w = max(max_label_w, w)

    esd_col_w = (ESD_DOT_R * 2 + 3.0) if has_esd else 0.0
    total_w = PIN_W + LABEL_GAP + max_label_w + LABEL_GAP + esd_col_w
    total_h = len(pins_sorted) * (PIN_H + PIN_GAP) - PIN_GAP

    # Draw pins
    for i, pin in enumerate(pins_sorted):
        cls = _classify_pin(pin['pin_name'], pin['net_name'], pin['pin_type'])
        py = start_y + i * (PIN_H + PIN_GAP)
        px = start_x

        _draw_pin_rect(svg, px, py, pin['pin_number'], cls)

        # Label to the right
        label_x = px + PIN_W + LABEL_GAP
        label_cy = py + PIN_H / 2
        _draw_label(svg, label_x, label_cy,
                    pin['pin_name'], pin['net_name'],
                    pin['pin_type'], cls, anchor='start')

        # ESD dot
        if has_esd:
            net = pin['net_name']
            protected = esd_map.get(net, None)
            if protected is not None:
                dot_x = start_x + total_w - ESD_DOT_R - 1.0
                _draw_esd_dot(svg, dot_x, label_cy, protected)

    return (total_w, total_h)


def _render_dual(svg: SvgBuilder, pins: list[dict],
                 esd_map: dict[str, bool],
                 start_x: float, start_y: float,
                 has_esd: bool) -> tuple[float, float]:
    """Render a dual-column connector. Returns (total_width, total_height)."""
    pins_sorted = sorted(pins, key=lambda p: _sort_key(p['pin_number']))

    # Split into left (odd index: 1,3,5,...) and right (even index: 2,4,6,...)
    left_pins = [p for p in pins_sorted if _sort_key(p['pin_number'])[0] % 2 == 1]
    right_pins = [p for p in pins_sorted if _sort_key(p['pin_number'])[0] % 2 == 0]

    # If the split doesn't work well (e.g., non-numeric pins), just alternate
    if not left_pins and not right_pins:
        left_pins = pins_sorted[::2]
        right_pins = pins_sorted[1::2]
    elif not left_pins:
        left_pins = pins_sorted[:len(pins_sorted) // 2]
        right_pins = pins_sorted[len(pins_sorted) // 2:]
    elif not right_pins:
        left_pins = pins_sorted[:len(pins_sorted) // 2]
        right_pins = pins_sorted[len(pins_sorted) // 2:]

    n_rows = max(len(left_pins), len(right_pins))

    # Measure label widths for each side
    def _measure_side(side_pins: list[dict]) -> float:
        max_w = 0.0
        for pin in side_pins:
            arrow = DIRECTION_ARROWS.get(pin['pin_type'], '')
            name = pin['pin_name'] if pin['pin_name'] != '~' else pin['net_name']
            if not name or name.startswith('__unnamed'):
                name = 'NC'
            display = (arrow + ' ' + name) if arrow else name
            w = _estimate_text_width(display, LABEL_FONT)
            if (pin['net_name'] and pin['pin_name'] != '~'
                    and pin['net_name'] != pin['pin_name']
                    and not pin['net_name'].startswith('__unnamed')):
                net_w = _estimate_text_width(pin['net_name'], NET_FONT)
                w = max(w, net_w)
            max_w = max(max_w, w)
        return max_w

    left_label_w = _measure_side(left_pins)
    right_label_w = _measure_side(right_pins)

    esd_col_w = (ESD_DOT_R * 2 + 3.0) if has_esd else 0.0

    # Layout: [esd] [left_labels] [left_pins] [gap] [right_pins] [right_labels] [esd]
    left_esd_x = start_x
    left_label_start = start_x + esd_col_w
    left_pin_x = left_label_start + left_label_w + LABEL_GAP
    right_pin_x = left_pin_x + PIN_W + DUAL_COL_GAP
    right_label_x = right_pin_x + PIN_W + LABEL_GAP
    right_esd_x = right_label_x + right_label_w + LABEL_GAP

    total_w = right_esd_x + esd_col_w - start_x
    total_h = n_rows * (PIN_H + PIN_GAP) - PIN_GAP

    for row in range(n_rows):
        py = start_y + row * (PIN_H + PIN_GAP)

        # Left pin
        if row < len(left_pins):
            pin = left_pins[row]
            cls = _classify_pin(pin['pin_name'], pin['net_name'], pin['pin_type'])
            _draw_pin_rect(svg, left_pin_x, py, pin['pin_number'], cls)

            # Label to the left of the left pin
            label_x = left_pin_x - LABEL_GAP
            label_cy = py + PIN_H / 2
            _draw_label(svg, label_x, label_cy,
                        pin['pin_name'], pin['net_name'],
                        pin['pin_type'], cls, anchor='end')

            # ESD dot on far left
            if has_esd:
                net = pin['net_name']
                protected = esd_map.get(net, None)
                if protected is not None:
                    _draw_esd_dot(svg, left_esd_x + ESD_DOT_R + 0.5,
                                  label_cy, protected)

        # Right pin
        if row < len(right_pins):
            pin = right_pins[row]
            cls = _classify_pin(pin['pin_name'], pin['net_name'], pin['pin_type'])
            _draw_pin_rect(svg, right_pin_x, py, pin['pin_number'], cls)

            # Label to the right of the right pin
            label_x = right_pin_x + PIN_W + LABEL_GAP
            label_cy = py + PIN_H / 2
            _draw_label(svg, label_x, label_cy,
                        pin['pin_name'], pin['net_name'],
                        pin['pin_type'], cls, anchor='start')

            # ESD dot on far right
            if has_esd:
                net = pin['net_name']
                protected = esd_map.get(net, None)
                if protected is not None:
                    _draw_esd_dot(svg, right_esd_x - ESD_DOT_R - 0.5,
                                  label_cy, protected)

    return (total_w, total_h)


def _render_barrel(svg: SvgBuilder, pins: list[dict],
                   esd_map: dict[str, bool],
                   start_x: float, start_y: float,
                   has_esd: bool) -> tuple[float, float]:
    """Render a barrel jack connector. Uses single layout."""
    return _render_single(svg, pins, esd_map, start_x, start_y, has_esd)


def _render_usb_c(svg: SvgBuilder, pins: list[dict],
                  esd_map: dict[str, bool],
                  start_x: float, start_y: float,
                  has_esd: bool) -> tuple[float, float]:
    """Render USB-C connector. Uses dual layout since USB-C has two rows."""
    return _render_dual(svg, pins, esd_map, start_x, start_y, has_esd)


# ======================================================================
# Legend
# ======================================================================

def _draw_legend(svg: SvgBuilder, x: float, y: float,
                 width: float, has_esd: bool) -> None:
    """Draw the color/ESD legend bar."""
    # Separator line
    svg.line(x, y - 2, x + width, y - 2,
             stroke='#e0e0e0', stroke_width=0.3)

    cx = x
    swatch_w = 6.0
    swatch_h = 4.0
    gap = 3.0

    items = [
        ('Power', 'power'),
        ('Ground', 'ground'),
        ('Signal', 'signal'),
        ('NC', 'nc'),
    ]

    for label, cls in items:
        fill, stroke, _text = PIN_COLORS[cls]
        svg.rect(cx, y, swatch_w, swatch_h,
                 fill=fill, stroke=stroke,
                 stroke_width=0.3, rx=PIN_RADIUS)
        svg.text(cx + swatch_w + 1.5, y + swatch_h / 2, label,
                 font_size=LEGEND_FONT, fill='#555555',
                 anchor='start', dominant_baseline='central')
        cx += swatch_w + 1.5 + _estimate_text_width(label, LEGEND_FONT) + gap

    if has_esd:
        # Protected dot
        svg.circle(cx + ESD_DOT_R, y + swatch_h / 2, ESD_DOT_R,
                   fill=ESD_PROTECTED_COLOR, stroke='none')
        svg.text(cx + ESD_DOT_R * 2 + 1.5, y + swatch_h / 2, 'Protected',
                 font_size=LEGEND_FONT, fill='#555555',
                 anchor='start', dominant_baseline='central')
        cx += ESD_DOT_R * 2 + 1.5 + _estimate_text_width('Protected', LEGEND_FONT) + gap

        # Unprotected dot
        svg.circle(cx + ESD_DOT_R, y + swatch_h / 2, ESD_DOT_R,
                   fill=ESD_UNPROTECTED_COLOR, stroke='none')
        svg.text(cx + ESD_DOT_R * 2 + 1.5, y + swatch_h / 2, 'Unprotected',
                 font_size=LEGEND_FONT, fill='#555555',
                 anchor='start', dominant_baseline='central')


# ======================================================================
# Public API
# ======================================================================

def generate_pinout(connector: dict, pin_data: list[dict],
                    esd_data: dict | None = None,
                    output_dir: str = '.') -> str | None:
    """Generate a pinout SVG for a single connector.

    Args:
        connector: dict with 'reference', 'value', 'lib_id', 'description'
        pin_data: list of dicts, each with:
            'pin_number': str
            'pin_name': str
            'net_name': str
            'pin_type': str (input/output/bidirectional/passive/power_in/no_connect)
        esd_data: dict from ESD coverage audit (optional, for protection status)
        output_dir: directory to write SVG

    Returns: path to generated SVG, or None if connector has no pins.
    """
    if not pin_data:
        return None

    ref = connector.get('reference', '?')
    value = connector.get('value', '')
    lib_id = connector.get('lib_id', '')
    description = connector.get('description', '')

    # Build ESD map: net_name -> protected (bool)
    esd_map: dict[str, bool] = {}
    has_esd = False
    if esd_data:
        has_esd = True
        for net in esd_data.get('protected_nets', []):
            esd_map[net] = True
        for net in esd_data.get('unprotected_nets', []):
            esd_map[net] = False

    # Detect layout
    layout_type, _pins_per_side = _detect_layout(lib_id, value, len(pin_data))

    # Title text
    title_text = f"{ref} \u2014 {value}" if value else ref
    if description and description != value:
        # Truncate long descriptions
        desc = description
        if len(desc) > 50:
            desc = desc[:48] + '\u2026'
        title_text += f"  ({desc})"

    # First pass: estimate body size for SVG dimensions
    # We need a rough size to create the SVG, then render into it.
    n_pins = len(pin_data)
    if layout_type == 'dual' or layout_type == 'usb_c':
        n_rows = (n_pins + 1) // 2
        est_body_w = 140.0
    else:
        n_rows = n_pins
        est_body_w = 100.0

    est_body_h = n_rows * (PIN_H + PIN_GAP) - PIN_GAP

    # Create SVG with estimated size (we'll adjust after rendering)
    svg_w = MARGIN * 2 + est_body_w
    svg_h = MARGIN * 2 + TITLE_H + est_body_h + LEGEND_H

    svg = SvgBuilder(svg_w, svg_h)

    # Background
    svg.rect(0, 0, svg_w, svg_h, fill=BG_COLOR, stroke='none')

    # Title
    svg.text(svg_w / 2, MARGIN + TITLE_FONT * 0.6, title_text,
             font_size=TITLE_FONT, fill='#1a1a1a',
             anchor='middle', dominant_baseline='auto', bold=True)

    # Body start
    body_y = MARGIN + TITLE_H

    # Choose renderer
    renderers = {
        'single': _render_single,
        'dual': _render_dual,
        'barrel': _render_barrel,
        'usb_c': _render_usb_c,
    }
    renderer = renderers.get(layout_type, _render_single)

    # Center the pin body horizontally
    body_x = MARGIN

    actual_w, actual_h = renderer(svg, pin_data, esd_map,
                                  body_x, body_y, has_esd)

    # Recalculate SVG size based on actual content
    final_w = max(MARGIN * 2 + actual_w,
                  MARGIN * 2 + _estimate_text_width(title_text, TITLE_FONT))
    final_h = MARGIN + TITLE_H + actual_h + LEGEND_H + MARGIN

    # Rebuild SVG with correct dimensions
    svg = SvgBuilder(final_w, final_h)
    svg.rect(0, 0, final_w, final_h, fill=BG_COLOR, stroke='none')

    # Title (centered in final width)
    svg.text(final_w / 2, MARGIN + TITLE_FONT * 0.6, title_text,
             font_size=TITLE_FONT, fill='#1a1a1a',
             anchor='middle', dominant_baseline='auto', bold=True)

    # Center body
    body_x = (final_w - actual_w) / 2
    body_y = MARGIN + TITLE_H

    renderer(svg, pin_data, esd_map, body_x, body_y, has_esd)

    # Legend
    legend_y = MARGIN + TITLE_H + actual_h + 5.0
    _draw_legend(svg, MARGIN, legend_y, final_w - MARGIN * 2, has_esd)

    # Write output
    os.makedirs(output_dir, exist_ok=True)
    filename = f"pinout_{ref}.svg"
    output_path = os.path.join(output_dir, filename)
    svg.write(output_path)
    return output_path


def generate_pinouts(analysis: dict, output_dir: str,
                     connector_refs: list[str] | None = None) -> list[str]:
    """Generate pinout SVGs for all connectors (or specified ones).

    Extracts connector data from schematic analysis JSON.
    Returns list of generated SVG file paths.
    """
    components = analysis.get('components', [])
    nets = analysis.get('nets', {})
    esd_audit = analysis.get('signal_analysis', {}).get('esd_coverage_audit', [])

    # Build ESD lookup by connector ref
    esd_by_ref: dict[str, dict] = {}
    for entry in esd_audit:
        ref = entry.get('connector_ref', '')
        if ref:
            esd_by_ref[ref] = entry

    # Build reverse mapping: (component_ref, pin_number) -> (net_name, pin_name, pin_type)
    pin_net_map: dict[tuple[str, str], tuple[str, str, str]] = {}
    for net_name, net_info in nets.items():
        if isinstance(net_info, dict):
            for pin_entry in net_info.get('pins', []):
                comp = pin_entry.get('component', '')
                pin_num = pin_entry.get('pin_number', '')
                pin_name = pin_entry.get('pin_name', '~')
                pin_type = pin_entry.get('pin_type', 'passive')
                if comp and pin_num:
                    pin_net_map[(comp, pin_num)] = (net_name, pin_name, pin_type)

    # Find all connectors
    connectors = [c for c in components if c.get('type') == 'connector']

    if connector_refs:
        ref_set = set(connector_refs)
        connectors = [c for c in connectors if c.get('reference') in ref_set]

    outputs: list[str] = []

    for connector in connectors:
        ref = connector.get('reference', '')
        if not ref:
            continue

        # Build pin_data from nets
        pin_data: list[dict] = []
        pin_uuids = connector.get('pin_uuids', {})

        # Collect all pins for this connector from the net map
        connector_pins: dict[str, tuple[str, str, str]] = {}
        for (comp, pin_num), (net_name, pin_name, pin_type) in pin_net_map.items():
            if comp == ref:
                connector_pins[pin_num] = (net_name, pin_name, pin_type)

        # If we have pin_uuids, use those as the canonical pin list
        if pin_uuids:
            for pin_num in pin_uuids:
                if pin_num in connector_pins:
                    net_name, pin_name, pin_type = connector_pins[pin_num]
                else:
                    net_name = ''
                    pin_name = '~'
                    pin_type = 'no_connect'
                pin_data.append({
                    'pin_number': str(pin_num),
                    'pin_name': pin_name,
                    'net_name': net_name,
                    'pin_type': pin_type,
                })
        else:
            # Fall back to whatever we found in nets
            for pin_num, (net_name, pin_name, pin_type) in sorted(
                    connector_pins.items(), key=lambda x: _sort_key(x[0])):
                pin_data.append({
                    'pin_number': str(pin_num),
                    'pin_name': pin_name,
                    'net_name': net_name,
                    'pin_type': pin_type,
                })

        if not pin_data:
            continue

        esd_data = esd_by_ref.get(ref)
        path = generate_pinout(connector, pin_data, esd_data=esd_data,
                               output_dir=output_dir)
        if path:
            outputs.append(path)

    return outputs
