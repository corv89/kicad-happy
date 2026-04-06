"""Figure generators for engineering documentation.

Provides power tree, bus topology, and architecture block diagrams
from schematic analysis JSON.  Output is SVG via svg_builder.

Usage from other scripts::

    from figures import generate_power_tree, generate_bus_topology, generate_architecture
    from figures import generate_all
"""

from __future__ import annotations

import os

from .power_tree import generate_power_tree
from .bus_topology import generate_bus_topology
from .architecture import generate_architecture

__all__ = [
    'generate_power_tree',
    'generate_bus_topology',
    'generate_architecture',
    'generate_all',
]


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
