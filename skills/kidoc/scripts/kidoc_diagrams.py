#!/usr/bin/env python3
"""Figure generator CLI for engineering documentation.

Generates all registered figures (power tree, bus topology, architecture,
pinouts, and any matplotlib charts if available) from analysis JSON.

Usage:
    python3 kidoc_diagrams.py --analysis schematic.json --output reports/figures/
    python3 kidoc_diagrams.py --analysis schematic.json --output figures/ --config .kicad-happy.json
    python3 kidoc_diagrams.py --analysis schematic.json --output figures/ --force

Zero external dependencies — Python 3.8+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure this script's directory is on sys.path so figures/ is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from figures import run_all, FigureTheme  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description='Generate figures from analysis JSON')
    parser.add_argument('--analysis', '-a', required=True,
                        help='Path to schematic analysis JSON')
    parser.add_argument('--output', '-o', required=True,
                        help='Output directory for figures')
    parser.add_argument('--config', default=None,
                        help='Path to .kicad-happy.json config '
                             '(for branding/theme)')
    parser.add_argument('--force', action='store_true',
                        help='Force regeneration (ignore cache)')
    args = parser.parse_args()

    with open(args.analysis) as f:
        analysis = json.load(f)

    config = {}
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    generated = run_all(analysis, config, args.output, force=args.force)

    if not generated:
        print("No figures generated (no applicable data found)",
              file=sys.stderr)
    else:
        for p in generated:
            print(p, file=sys.stderr)


if __name__ == '__main__':
    main()
