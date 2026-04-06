"""Architecture block diagram generator.

Clusters ICs by function using signal analysis data, component types,
and library ID keywords.  Renders a 3-column layout with connection
arrows from the central MCU/CPU cluster to peripheral clusters.
"""

from __future__ import annotations

import os

from .styles import (
    SvgBuilder,
    BOX_FILL, BOX_STROKE, BOX_FONT,
    POWER_FILL, POWER_STROKE, POWER_FONT,
    BUS_FILL, BUS_STROKE, BUS_FONT,
    IO_FILL, IO_STROKE, IO_FONT,
    BG_COLOR,
    BOX_CORNER_RADIUS,
    FONT_SIZE, SMALL_FONT,
    _draw_arrow,
)


# ======================================================================
# Component classification
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

    # 2. Protection devices (ESD, TVS) -- separate from Power
    lib_lower = lib_id.lower()
    if ref in protection_refs or 'protection' in lib_lower or 'tvs' in lib_lower:
        return 'Protection'

    # 3. Type-based classification (reliable -- set by kicad_utils.classify_component)
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
        return None  # Skip -- these are support components, not functional blocks

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


# ======================================================================
# Generator
# ======================================================================

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

    # Skip if no MCU/CPU cluster -- diagram isn't useful for simple
    # power-supply or passive designs with no central IC
    has_mcu = 'MCU / CPU' in clusters

    if not has_mcu:
        return None  # no central IC to anchor the diagram

    # Also skip if fewer than 3 non-empty clusters -- not enough to make
    # a meaningful architecture diagram
    if len(clusters) < 3:
        return None

    # Build labels for each component (no truncation, prefer description for connectors)
    def _comp_label(comp: dict) -> str:
        ref = comp.get('reference', '?')
        value = comp.get('value', '')
        desc = comp.get('description', '')
        # For connectors, use description if more readable than raw value
        if comp.get('type') == 'connector' and desc and len(desc) < 30:
            return f"{ref}: {desc}"
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

        # List components -- no truncation
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
