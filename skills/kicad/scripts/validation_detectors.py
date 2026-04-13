"""Validation detectors — correctness checks that emit rich findings.

Separated from domain_detectors.py (which discovers circuit topologies).
These detectors check for design errors: missing components, wrong values,
protocol violations, sequencing issues.

Each validator takes an AnalysisContext (and optional detector results) and
returns a list of rich finding dicts via finding_schema.make_finding().
"""

from __future__ import annotations

import re

from kicad_types import AnalysisContext
from kicad_utils import parse_value, parse_voltage_from_net_name
from detector_helpers import (
    get_components_by_type, get_unique_ics, index_two_pin_components,
    match_ic_keywords,
)
from signal_detectors import _get_net_components
from finding_schema import make_finding


# ---------------------------------------------------------------------------
# Shared pull-up/pull-down detection helpers
# ---------------------------------------------------------------------------

def _find_pullups_on_net(
    ctx: AnalysisContext,
    net_name: str,
    resistor_nets: dict[str, tuple[str, str]],
    net_to_resistors: dict[str, list[str]],
) -> list[dict]:
    """Find pull-up resistors on a net (resistor between net and power rail).

    Returns list of dicts: [{ref, ohms, rail}].
    """
    pullups = []
    for rref in net_to_resistors.get(net_name, []):
        n1, n2 = resistor_nets.get(rref, (None, None))
        if not n1 or not n2:
            continue
        other = n2 if n1 == net_name else n1
        if ctx.is_power_net(other) and not ctx.is_ground(other):
            ohms = ctx.parsed_values.get(rref)
            pullups.append({'ref': rref, 'ohms': ohms, 'rail': other})
    return pullups


def _find_pulldowns_on_net(
    ctx: AnalysisContext,
    net_name: str,
    resistor_nets: dict[str, tuple[str, str]],
    net_to_resistors: dict[str, list[str]],
) -> list[dict]:
    """Find pull-down resistors on a net (resistor between net and ground)."""
    pulldowns = []
    for rref in net_to_resistors.get(net_name, []):
        n1, n2 = resistor_nets.get(rref, (None, None))
        if not n1 or not n2:
            continue
        other = n2 if n1 == net_name else n1
        if ctx.is_ground(other):
            ohms = ctx.parsed_values.get(rref)
            pulldowns.append({'ref': rref, 'ohms': ohms, 'rail': other})
    return pulldowns


def _get_pin_net(ctx: AnalysisContext, ref: str, pin_names: tuple[str, ...]) -> str | None:
    """Find the net connected to a pin matching any of the given names."""
    pins = ctx.ref_pins.get(ref, {})
    for pnum, (net, _) in pins.items():
        comp = ctx.comp_lookup.get(ref)
        if not comp:
            continue
        for p in comp.get('pins', []):
            if p.get('number') == pnum and p.get('name', '').upper() in pin_names:
                return net
    # Fallback: check net pin_name via ctx.nets
    for pnum, (net, _) in pins.items():
        if not net or net not in ctx.nets:
            continue
        for np in ctx.nets[net]['pins']:
            if np['component'] == ref and np.get('pin_name', '').upper() in pin_names:
                return net
    return None


def _net_has_driver(ctx: AnalysisContext, net_name: str, exclude_ref: str) -> bool:
    """Check if a net has at least one push-pull driver (non-OD/OC IC output)."""
    if not net_name or net_name not in ctx.nets:
        return False
    for p in ctx.nets[net_name]['pins']:
        if p['component'] == exclude_ref:
            continue
        comp = ctx.comp_lookup.get(p['component'])
        if not comp:
            continue
        if comp['type'] == 'ic':
            return True  # Conservative: assume IC outputs can drive
    return False
