"""Shared colors, constants, and drawing helpers for figure generators.

All figure modules import their styling from here to keep the palette
consistent and avoid duplicating drawing primitives.
"""

from __future__ import annotations

import math
import os
import sys

# Allow importing svg_builder from the parent scripts/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from svg_builder import SvgBuilder  # noqa: E402

__all__ = [
    # Re-export SvgBuilder so figure modules can do:
    #   from .styles import SvgBuilder, BOX_FILL, _draw_box, ...
    'SvgBuilder',
    # Colors
    'BOX_FILL', 'BOX_STROKE', 'BOX_FONT',
    'POWER_FILL', 'POWER_STROKE', 'POWER_FONT',
    'BUS_FILL', 'BUS_STROKE', 'BUS_FONT',
    'IO_FILL', 'IO_STROKE', 'IO_FONT',
    'ARROW_COLOR', 'LABEL_FONT', 'BG_COLOR',
    # Constants
    'BOX_CORNER_RADIUS', 'BOX_PADDING', 'FONT_SIZE', 'SMALL_FONT',
    'ARROW_HEAD_SIZE',
    # Drawing helpers
    '_draw_box', '_draw_arrow', '_draw_bus_line', '_format_cap_summary',
]

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
            parts.append(f"{refs[0]}: {val}")
        else:
            parts.append(f"{len(refs)}\u00d7{val}")
    return ', '.join(parts)
