"""Power tree diagram generator.

Renders a publication-quality power distribution tree showing input rails,
protection devices, regulators, and output rails with enable chains.
"""

from __future__ import annotations

import os
import re

from .styles import (
    SvgBuilder,
    _format_cap_summary,
)

# ======================================================================
# Publication-quality power tree colors
# ======================================================================

PT_INPUT_FILL = "#1a3c5c"
PT_INPUT_TEXT = "#ffffff"
PT_INPUT_SUBTEXT = "#8ab4e0"
PT_REG_FILL = "#f0f4f8"
PT_REG_STROKE = "#4a6fa5"
PT_REG_TEXT = "#1a3c5c"
PT_REG_SUBTEXT = "#4a6fa5"
PT_REG_DETAIL = "#888888"
PT_OUTPUT_COLORS = [
    ("#e8f5e9", "#43a047", "#2e7d32"),  # Green
    ("#fff3e0", "#ef6c00", "#e65100"),  # Orange
    ("#e3f2fd", "#1565c0", "#0d47a1"),  # Blue
    ("#f3e5f5", "#7b1fa2", "#4a148c"),  # Purple
    ("#fff8e1", "#f9a825", "#f57f17"),  # Amber
    ("#fce4ec", "#c62828", "#b71c1c"),  # Red
]
PT_PROT_FILL = "#eceff1"
PT_PROT_STROKE = "#78909c"
PT_PROT_TEXT = "#37474f"
PT_ARROW_COLOR = "#4a6fa5"
PT_ENABLE_COLOR = "#888888"
PT_BG = "#ffffff"

PT_FONT_TITLE = 5.0
PT_FONT_RAIL = 4.5
PT_FONT_RAIL_SUB = 3.0
PT_FONT_REG = 3.5
PT_FONT_REG_SUB = 3.0
PT_FONT_REG_DETAIL = 2.5
PT_FONT_LEGEND = 2.8
PT_BOX_RADIUS = 3.0
PT_BOX_PADDING = 5.0
PT_ARROW_WIDTH = 0.8
PT_ARROW_HEAD = 2.5


# ======================================================================
# Helpers
# ======================================================================

def _pt_draw_arrow(svg: SvgBuilder, x1: float, y1: float,
                   x2: float, y2: float,
                   color: str = PT_ARROW_COLOR,
                   width: float = PT_ARROW_WIDTH,
                   head: float = PT_ARROW_HEAD,
                   dash: str | None = None,
                   label: str = "") -> None:
    """Draw a solid arrow with triangular head for power tree diagrams."""
    import math
    svg.line(x1, y1, x2, y2, stroke=color, stroke_width=width, dash=dash)
    # Arrowhead
    dx, dy = x2 - x1, y2 - y1
    dist = math.hypot(dx, dy)
    if dist < 0.5:
        return
    nx, ny = dx / dist, dy / dist
    px, py = -ny, nx
    svg.polyline([
        (x2 - nx * head + px * head * 0.4, y2 - ny * head + py * head * 0.4),
        (x2, y2),
        (x2 - nx * head - px * head * 0.4, y2 - ny * head - py * head * 0.4),
    ], stroke=color, fill=color, stroke_width=width * 0.5, closed=True)
    if label:
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        svg.text(mx, my - 2.0, label, font_size=PT_FONT_REG_DETAIL,
                 fill=color, anchor='middle')


def _pt_infer_voltage_from_name(rail_name: str) -> str:
    """Try to extract a voltage hint from a rail name like '12v', '+3V3', 'VBUS'."""
    name = rail_name.strip().upper()
    # Match patterns like '12V', '3.3V', '5V', '+3V3', etc.
    m = re.match(r'[+]?(\d+)[Vv](\d+)', name)
    if m:
        return f"{m.group(1)}.{m.group(2)}V"
    m = re.match(r'[+]?(\d+\.?\d*)\s*[Vv]', name)
    if m:
        return f"{m.group(1)}V"
    # Common names
    known = {'VBUS': '5.0V (USB)', 'VUSB': '5.0V', 'VIN': 'Input'}
    return known.get(name, '')


def _pt_format_topology(topology: str) -> str:
    """Format topology string for display."""
    if not topology:
        return 'Regulator'
    t = topology.lower()
    mapping = {
        'ldo': 'LDO',
        'linear': 'Linear',
        'buck': 'Buck',
        'boost': 'Boost',
        'buck-boost': 'Buck-Boost',
        'switching': 'Switching',
        'charge_pump': 'Charge Pump',
    }
    return mapping.get(t, topology.capitalize())


# ======================================================================
# Generator
# ======================================================================

def generate_power_tree(analysis: dict, output_path: str) -> str | None:
    """Generate a publication-quality power tree SVG.

    Layout: Input rails (left) -> Protection (opt) -> Regulators (center) -> Output rails (right).
    Arrows show power flow.  Enable chains shown as dashed gray arrows.
    """
    regulators = analysis.get('signal_analysis', {}).get('power_regulators', [])
    if not regulators:
        return None

    # -- Collect data --
    protection_devices = analysis.get('signal_analysis', {}).get('protection_devices', [])
    enable_chains = (analysis.get('design_analysis', {})
                     .get('power_sequencing', {})
                     .get('enable_chains', []))

    # Component lookup for inductor values
    comp_lookup: dict[str, dict] = {}
    for comp in analysis.get('components', []):
        ref = comp.get('reference', '')
        if ref:
            comp_lookup[ref] = comp

    # -- Derive unique rails --
    input_rail_set: set[str] = set()
    output_rail_set: set[str] = set()
    for reg in regulators:
        input_rail_set.add(reg.get('input_rail', '?'))
        output_rail_set.add(reg.get('output_rail', '?'))
    # True input rails = only appear as input, not as another reg's output
    root_input_rails = sorted(input_rail_set - output_rail_set)
    if not root_input_rails:
        root_input_rails = sorted(input_rail_set)

    # Group regulators by input rail, sort by Vout ascending
    regs_by_input: dict[str, list[dict]] = {}
    for reg in regulators:
        in_rail = reg.get('input_rail', '?')
        regs_by_input.setdefault(in_rail, []).append(reg)
    for key in regs_by_input:
        regs_by_input[key].sort(key=lambda r: r.get('estimated_vout') or 0)

    # Assign color index to each unique output rail
    all_output_rails = sorted(output_rail_set)
    output_color_map: dict[str, tuple[str, str, str]] = {}
    for i, rail in enumerate(all_output_rails):
        output_color_map[rail] = PT_OUTPUT_COLORS[i % len(PT_OUTPUT_COLORS)]

    # Check for protection devices on input paths
    prot_by_rail: dict[str, list[dict]] = {}
    for pd in protection_devices:
        # Protection devices may have a 'rail' or 'net' field
        rail = pd.get('rail', pd.get('net', pd.get('input_rail', '')))
        if rail:
            prot_by_rail.setdefault(rail, []).append(pd)
    has_protection = bool(prot_by_rail)

    # -- Layout geometry --
    margin = 15.0
    title_h = 12.0
    legend_h = 15.0

    # Column widths
    input_col_w = 50.0
    prot_col_w = 50.0 if has_protection else 0.0
    reg_col_w = 65.0
    output_col_w = 45.0
    col_gap = 25.0

    # Column x positions (left edge of each column)
    input_col_x = margin
    prot_col_x = input_col_x + input_col_w + col_gap
    if not has_protection:
        prot_col_x = input_col_x + input_col_w  # no gap if no protection
    reg_col_x = prot_col_x + prot_col_w + (col_gap if has_protection else col_gap)
    output_col_x = reg_col_x + reg_col_w + col_gap

    # Row heights -- one row per regulator, grouped under input rails
    row_h = 32.0  # height per regulator row
    input_rail_gap = 8.0  # extra gap between input rail groups

    # Count total regulator rows
    total_reg_rows = 0
    input_rail_row_start: dict[str, int] = {}
    for in_rail in root_input_rails:
        input_rail_row_start[in_rail] = total_reg_rows
        n = len(regs_by_input.get(in_rail, []))
        total_reg_rows += max(n, 1)
    # Also handle cascade regulators (input rail is another reg's output)
    cascade_rails = sorted(input_rail_set - set(root_input_rails))
    for in_rail in cascade_rails:
        input_rail_row_start[in_rail] = total_reg_rows
        n = len(regs_by_input.get(in_rail, []))
        total_reg_rows += max(n, 1)

    body_h = total_reg_rows * row_h + (len(root_input_rails) + len(cascade_rails) - 1) * input_rail_gap
    total_w = output_col_x + output_col_w + margin
    total_h = margin + title_h + body_h + legend_h + margin

    # Ensure minimum dimensions
    total_w = max(total_w, 200.0)
    total_h = max(total_h, 100.0)

    svg = SvgBuilder(total_w, total_h)

    # -- Background --
    svg.rect(0, 0, total_w, total_h, fill=PT_BG, stroke='none')

    # -- Title --
    svg.text(total_w / 2, margin + PT_FONT_TITLE * 0.6, "Power Distribution Tree",
             font_size=PT_FONT_TITLE, fill='#1a1a1a', anchor='middle', bold=True)

    body_top = margin + title_h

    # -- Position tracking --
    # Track box positions for arrow drawing
    # input_box_pos[rail] = (cx_right_edge, cy)
    # reg_box_pos[reg_ref] = (x, y, w, h)
    # output_box_pos[rail] = (cx_left_edge, cy)
    input_box_pos: dict[str, tuple[float, float]] = {}
    reg_box_pos: dict[str, tuple[float, float, float, float]] = {}
    output_box_pos: dict[str, tuple[float, float]] = {}
    # Track regulator center-Y for enable chains
    reg_center_y: dict[str, float] = {}

    # Collect regulator positions per output rail for box centering
    _pending_output_regs: dict[str, list[tuple[float, float, float | None]]] = {}

    # -- Draw columns --
    all_input_rails = root_input_rails + cascade_rails
    current_y = body_top

    for rail_idx, in_rail in enumerate(all_input_rails):
        regs_for_rail = regs_by_input.get(in_rail, [])
        n_regs = max(len(regs_for_rail), 1)
        group_h = n_regs * row_h

        # -- Input rail box --
        input_box_h = min(group_h, 20.0)
        input_box_y = current_y + (group_h - input_box_h) / 2
        input_box_x = input_col_x

        svg.rect(input_box_x, input_box_y, input_col_w, input_box_h,
                 stroke='none', fill=PT_INPUT_FILL, rx=PT_BOX_RADIUS)
        svg.text(input_box_x + input_col_w / 2, input_box_y + input_box_h / 2 - 1.0,
                 in_rail, font_size=PT_FONT_RAIL, fill=PT_INPUT_TEXT,
                 anchor='middle', dominant_baseline='central', bold=True)
        # Voltage subtext if we can infer it from the rail name
        v_text = _pt_infer_voltage_from_name(in_rail)
        if v_text:
            svg.text(input_box_x + input_col_w / 2, input_box_y + input_box_h / 2 + 3.5,
                     v_text, font_size=PT_FONT_RAIL_SUB, fill=PT_INPUT_SUBTEXT,
                     anchor='middle', dominant_baseline='central')

        input_box_pos[in_rail] = (input_box_x + input_col_w, input_box_y + input_box_h / 2)

        # -- Protection devices (if any for this rail) --
        prot_devices = prot_by_rail.get(in_rail, [])
        prot_right_x = input_box_x + input_col_w  # default: no protection column
        if has_protection and prot_devices:
            prot_box_h = min(len(prot_devices) * 10.0 + 6.0, group_h)
            prot_box_y = current_y + (group_h - prot_box_h) / 2
            svg.rect(prot_col_x, prot_box_y, prot_col_w, prot_box_h,
                     stroke=PT_PROT_STROKE, fill=PT_PROT_FILL,
                     stroke_width=0.4, rx=PT_BOX_RADIUS)
            for pi, pd in enumerate(prot_devices[:3]):
                pd_ref = pd.get('ref', '?')
                pd_type = pd.get('type', '')
                pd_val = pd.get('value', '')
                pd_label = pd_ref
                if pd_type:
                    pd_label += f" \u2014 {pd_type}"
                if pd_val:
                    pd_label += f" ({pd_val})"
                svg.text(prot_col_x + prot_col_w / 2,
                         prot_box_y + 5.0 + pi * 8.0,
                         pd_label, font_size=PT_FONT_REG_DETAIL,
                         fill=PT_PROT_TEXT, anchor='middle',
                         dominant_baseline='central')
            prot_right_x = prot_col_x + prot_col_w

        # -- Regulator boxes --
        for reg_idx, reg in enumerate(regs_for_rail):
            reg_y = current_y + reg_idx * row_h
            reg_box_h = row_h - 4.0  # leave small gap between rows
            reg_box_x = reg_col_x
            reg_box_w = reg_col_w

            ref = reg.get('ref', '?')
            value = reg.get('value', '')
            topology = reg.get('topology', '')
            vout = reg.get('estimated_vout')
            inductor_ref = reg.get('inductor', '')
            out_rail = reg.get('output_rail', '?')

            # Draw regulator box
            svg.rect(reg_box_x, reg_y, reg_box_w, reg_box_h,
                     stroke=PT_REG_STROKE, fill=PT_REG_FILL,
                     stroke_width=0.5, rx=PT_BOX_RADIUS)

            # Line 1: ref + part (bold)
            line1 = f"{ref} \u2014 {value}" if value else ref
            # Truncate if too long
            if len(line1) > 28:
                line1 = line1[:26] + "\u2026"
            svg.text(reg_box_x + reg_box_w / 2, reg_y + 5.5, line1,
                     font_size=PT_FONT_REG, fill=PT_REG_TEXT,
                     anchor='middle', dominant_baseline='central', bold=True)

            # Line 2: topology + Vout
            topo_label = _pt_format_topology(topology)
            if vout:
                line2 = f"{topo_label} \u2022 {vout:.1f}V"
            else:
                line2 = topo_label
            svg.text(reg_box_x + reg_box_w / 2, reg_y + 10.5, line2,
                     font_size=PT_FONT_REG_SUB, fill=PT_REG_SUBTEXT,
                     anchor='middle', dominant_baseline='central')

            # Line 3: inductor (if buck/boost)
            detail_y = 15.5
            if inductor_ref:
                ind_value = ''
                if inductor_ref in comp_lookup:
                    ind_value = comp_lookup[inductor_ref].get('value', '')
                ind_text = f"{inductor_ref}: {ind_value}" if ind_value else inductor_ref
                svg.text(reg_box_x + reg_box_w / 2, reg_y + detail_y, ind_text,
                         font_size=PT_FONT_REG_DETAIL, fill=PT_REG_DETAIL,
                         anchor='middle', dominant_baseline='central')
                detail_y += 4.5

            # Line 4: output cap summary
            out_caps = reg.get('output_capacitors', [])
            cap_summary = _format_cap_summary(out_caps)
            if cap_summary:
                cap_text = f"Cout: {cap_summary}"
                # Truncate if needed
                if len(cap_text) > 35:
                    cap_text = cap_text[:33] + "\u2026"
                svg.text(reg_box_x + reg_box_w / 2, reg_y + detail_y, cap_text,
                         font_size=PT_FONT_REG_DETAIL, fill=PT_REG_DETAIL,
                         anchor='middle', dominant_baseline='central')

            reg_box_pos[ref] = (reg_box_x, reg_y, reg_box_w, reg_box_h)
            reg_center_y[ref] = reg_y + reg_box_h / 2

            # Record this regulator's Y for output box positioning
            _pending_output_regs.setdefault(out_rail, []).append(
                (reg_y, reg_box_h, vout))

        current_y += group_h + input_rail_gap

    # -- Draw output rail boxes (centered among their regulators) --
    for out_rail, entries in _pending_output_regs.items():
        out_fill, out_stroke, out_text_color = output_color_map.get(
            out_rail, PT_OUTPUT_COLORS[0])
        out_box_h = 18.0
        # Center the box vertically among all regulators that feed this rail
        if len(entries) == 1:
            reg_y, reg_box_h, vout = entries[0]
            out_box_y = reg_y + (reg_box_h - out_box_h) / 2
        else:
            ys = [ry + rh / 2 for ry, rh, _ in entries]
            center = (min(ys) + max(ys)) / 2
            out_box_y = center - out_box_h / 2
        vout = next((v for _, _, v in entries if v is not None), None)
        svg.rect(output_col_x, out_box_y, output_col_w, out_box_h,
                 stroke=out_stroke, fill=out_fill,
                 stroke_width=0.5, rx=PT_BOX_RADIUS)
        svg.text(output_col_x + output_col_w / 2,
                 out_box_y + out_box_h / 2 - 1.0,
                 out_rail, font_size=PT_FONT_RAIL, fill=out_text_color,
                 anchor='middle', dominant_baseline='central', bold=True)
        if vout:
            svg.text(output_col_x + output_col_w / 2,
                     out_box_y + out_box_h / 2 + 3.5,
                     f"{vout:.2f}V", font_size=PT_FONT_RAIL_SUB,
                     fill=out_stroke, anchor='middle',
                     dominant_baseline='central')
        output_box_pos[out_rail] = (output_col_x, out_box_y + out_box_h / 2)

    # -- Draw arrows --
    for in_rail in all_input_rails:
        regs_for_rail = regs_by_input.get(in_rail, [])
        if not regs_for_rail:
            continue

        in_right_x, in_cy = input_box_pos.get(in_rail, (0, 0))

        if len(regs_for_rail) == 1:
            # Single regulator: straight arrow
            reg = regs_for_rail[0]
            ref = reg.get('ref', '?')
            if ref in reg_box_pos:
                rx, ry, rw, rh = reg_box_pos[ref]
                _pt_draw_arrow(svg, in_right_x + 2, in_cy,
                               rx - 2, ry + rh / 2)
        else:
            # Multiple regulators: L-shaped trunk routing
            # Draw vertical trunk from input
            trunk_x = in_right_x + (reg_col_x - in_right_x) * 0.35
            # Find Y range of regulators
            reg_ys = []
            for reg in regs_for_rail:
                ref = reg.get('ref', '?')
                if ref in reg_box_pos:
                    rx, ry, rw, rh = reg_box_pos[ref]
                    reg_ys.append(ry + rh / 2)
            if reg_ys:
                # Horizontal line from input to trunk
                svg.line(in_right_x + 2, in_cy, trunk_x, in_cy,
                         stroke=PT_ARROW_COLOR, stroke_width=PT_ARROW_WIDTH)
                # Vertical trunk
                svg.line(trunk_x, min(min(reg_ys), in_cy),
                         trunk_x, max(max(reg_ys), in_cy),
                         stroke=PT_ARROW_COLOR, stroke_width=PT_ARROW_WIDTH)
                # Horizontal branches to each regulator
                for reg in regs_for_rail:
                    ref = reg.get('ref', '?')
                    if ref in reg_box_pos:
                        rx, ry, rw, rh = reg_box_pos[ref]
                        target_y = ry + rh / 2
                        _pt_draw_arrow(svg, trunk_x, target_y,
                                       rx - 2, target_y)

        # Output arrows from each regulator to output rail
        # Group regulators by output rail for trunk routing
        out_groups: dict[str, list[dict]] = {}
        for reg in regs_for_rail:
            out_rail = reg.get('output_rail', '?')
            out_groups.setdefault(out_rail, []).append(reg)

        for out_rail, group_regs in out_groups.items():
            if out_rail not in output_box_pos:
                continue
            out_lx, out_cy = output_box_pos[out_rail]
            if len(group_regs) == 1:
                ref = group_regs[0].get('ref', '?')
                if ref in reg_box_pos:
                    rx, ry, rw, rh = reg_box_pos[ref]
                    _pt_draw_arrow(svg, rx + rw + 2, ry + rh / 2,
                                   out_lx - 2, out_cy)
            else:
                # Multiple regs to same output: trunk routing
                trunk_x = reg_col_x + reg_col_w + (output_col_x - reg_col_x - reg_col_w) * 0.65
                reg_ys = []
                for reg in group_regs:
                    ref = reg.get('ref', '?')
                    if ref in reg_box_pos:
                        rx, ry, rw, rh = reg_box_pos[ref]
                        reg_ys.append(ry + rh / 2)
                if reg_ys:
                    # Horizontal branches from each regulator to trunk
                    for reg in group_regs:
                        ref = reg.get('ref', '?')
                        if ref in reg_box_pos:
                            rx, ry, rw, rh = reg_box_pos[ref]
                            src_y = ry + rh / 2
                            svg.line(rx + rw + 2, src_y, trunk_x, src_y,
                                     stroke=PT_ARROW_COLOR, stroke_width=PT_ARROW_WIDTH)
                    # Vertical trunk
                    svg.line(trunk_x, min(min(reg_ys), out_cy),
                             trunk_x, max(max(reg_ys), out_cy),
                             stroke=PT_ARROW_COLOR, stroke_width=PT_ARROW_WIDTH)
                    # Single arrow from trunk to output box
                    _pt_draw_arrow(svg, trunk_x, out_cy,
                                   out_lx - 2, out_cy)

    # -- Enable chains (dashed gray) --
    for chain in (enable_chains or []):
        src_ref = chain.get('source_ref', '')
        tgt_ref = chain.get('target_ref', '')
        if src_ref in reg_box_pos and tgt_ref in reg_box_pos:
            sx, sy, sw, sh = reg_box_pos[src_ref]
            tx, ty, tw, th = reg_box_pos[tgt_ref]
            # Draw curved dashed arrow from source output side to target
            src_out_x = sx + sw + 3
            src_out_y = sy + sh * 0.8
            tgt_in_x = tx - 3
            tgt_in_y = ty + th * 0.2
            _pt_draw_arrow(svg, src_out_x, src_out_y,
                           tgt_in_x, tgt_in_y,
                           color=PT_ENABLE_COLOR, width=0.5,
                           head=2.0, dash="2,1.5", label="PG\u2192EN")

    # -- Legend bar --
    legend_y = total_h - legend_h - margin / 2
    legend_x = margin
    swatch_w = 8.0
    swatch_h = 5.0
    legend_gap = 4.0

    legend_items = [
        ("Input Rail", PT_INPUT_FILL, 'none', PT_INPUT_TEXT),
        ("Regulator", PT_REG_FILL, PT_REG_STROKE, PT_REG_TEXT),
    ]
    if has_protection:
        legend_items.append(("Protection", PT_PROT_FILL, PT_PROT_STROKE, PT_PROT_TEXT))
    # Add first output color as representative
    if all_output_rails:
        ofill, ostroke, otext = output_color_map.get(all_output_rails[0], PT_OUTPUT_COLORS[0])
        legend_items.append(("Output Rail", ofill, ostroke, otext))

    # Light separator line above legend
    svg.line(margin, legend_y - 2, total_w - margin, legend_y - 2,
             stroke='#e0e0e0', stroke_width=0.3)

    cx = legend_x
    for label, fill, stroke_color, text_color in legend_items:
        svg.rect(cx, legend_y, swatch_w, swatch_h,
                 fill=fill, stroke=stroke_color if stroke_color != 'none' else fill,
                 stroke_width=0.3, rx=1.0)
        # If fill is very dark, draw inner text dot for visibility
        svg.text(cx + swatch_w + 2.0, legend_y + swatch_h / 2,
                 label, font_size=PT_FONT_LEGEND, fill='#555555',
                 anchor='start', dominant_baseline='central')
        cx += swatch_w + 2.0 + len(label) * PT_FONT_LEGEND * 0.55 + legend_gap

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    svg.write(output_path)
    return output_path
