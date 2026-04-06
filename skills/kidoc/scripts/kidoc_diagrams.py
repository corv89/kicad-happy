#!/usr/bin/env python3
"""Block diagram generator for engineering documentation.

Generates power tree, bus topology, and architecture block diagrams
from schematic analysis JSON.  Output is SVG via svg_builder.

Usage:
    python3 kidoc_diagrams.py --analysis schematic.json --all --output reports/figures/diagrams/
    python3 kidoc_diagrams.py --analysis schematic.json --power-tree --output diagrams/
    python3 kidoc_diagrams.py --analysis schematic.json --bus-topology --output diagrams/
    python3 kidoc_diagrams.py --analysis schematic.json --architecture --output diagrams/

Zero external dependencies — Python 3.8+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure this script's directory is on sys.path so figures/ is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from figures import (  # noqa: E402
    generate_power_tree,
    generate_bus_topology,
    generate_architecture,
    generate_all,
)


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
