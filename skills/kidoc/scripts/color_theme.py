"""KiCad default schematic color theme and rendering constants.

Hardcoded values matching KiCad's default color scheme.  These are used
by the SVG renderer to produce output that visually matches KiCad.

Zero external dependencies — constants only.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Schematic element colors (KiCad default theme, hex RRGGBB)
# ---------------------------------------------------------------------------

WIRE_COLOR = "#00841a"
BUS_COLOR = "#0000c8"
JUNCTION_COLOR = "#00841a"
NO_CONNECT_COLOR = "#00841a"

COMPONENT_OUTLINE_COLOR = "#840000"
COMPONENT_FILL_COLOR = "#ffffc8"      # background fill for symbol bodies
PIN_COLOR = "#840000"

LABEL_COLOR = "#00841a"               # local net labels
GLOBAL_LABEL_COLOR = "#840000"        # global labels
HIER_LABEL_COLOR = "#840064"          # hierarchical labels
POWER_SYMBOL_COLOR = "#840000"

REFERENCE_COLOR = "#00841a"
VALUE_COLOR = "#00841a"
TEXT_COLOR = "#000000"

SHEET_BORDER_COLOR = "#72009a"        # hierarchical sheet box border
SHEET_FILL_COLOR = "#ffffc8"          # hierarchical sheet box fill
SHEET_LABEL_COLOR = "#840064"

TITLE_BLOCK_COLOR = "#840000"
DRAWING_SHEET_COLOR = "#c0c0c0"       # sheet border and grid

BACKGROUND_COLOR = "#ffffff"

# ---------------------------------------------------------------------------
# Stroke defaults
# ---------------------------------------------------------------------------

DEFAULT_STROKE_WIDTH = 0.1524         # mm (6 mil) — KiCad default when width=0
WIRE_STROKE_WIDTH = 0.1524            # mm (6 mil) — match pin thickness
BUS_STROKE_WIDTH = 0.381              # mm (15 mil)
PIN_STROKE_WIDTH = 0.1524             # mm (6 mil)

# ---------------------------------------------------------------------------
# Element sizes
# ---------------------------------------------------------------------------

JUNCTION_RADIUS = 0.508               # mm (20 mil)
NO_CONNECT_SIZE = 0.508               # mm half-size of the X marker
PIN_INVERTED_RADIUS = 0.508           # mm — inverted pin bubble
PIN_NAME_OFFSET_DEFAULT = 0.508       # mm — default offset for pin names

# ---------------------------------------------------------------------------
# Text defaults
# ---------------------------------------------------------------------------

DEFAULT_FONT_SIZE = 1.27              # mm — KiCad default text height
DEFAULT_FONT_FAMILY = "sans-serif"

# ---------------------------------------------------------------------------
# Paper sizes (mm) — width x height in landscape orientation
# ---------------------------------------------------------------------------

PAPER_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
    "A":  (279.4, 215.9),   # US Letter
    "B":  (431.8, 279.4),   # US Tabloid / Ledger
    "C":  (558.8, 431.8),
    "D":  (863.6, 558.8),
    "E":  (1117.6, 863.6),
}

# ---------------------------------------------------------------------------
# SVG dash patterns (stroke-dasharray values for KiCad line types)
# ---------------------------------------------------------------------------

DASH_PATTERNS = {
    "solid": None,
    "dash": "2.0,1.0",
    "dot": "0.5,0.5",
    "dash_dot": "2.0,0.5,0.5,0.5",
    "dash_dot_dot": "2.0,0.5,0.5,0.5,0.5,0.5",
    "default": None,
}
