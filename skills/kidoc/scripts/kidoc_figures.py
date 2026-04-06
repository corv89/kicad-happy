#!/usr/bin/env python3
"""Matplotlib-based figure generators for kidoc engineering reports.

Generates analysis charts from SPICE, thermal, and EMC data.
Outputs SVG files suitable for embedding in PDF reports.

Usage:
    python3 kidoc_figures.py --analysis-dir reports/cache/analysis/ --output reports/figures/plots/

Requires matplotlib (installed in reports/.venv/).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Force non-interactive backend before any other matplotlib import
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as ticker  # noqa: E402

# ======================================================================
# Engineering palette
# ======================================================================

_NAVY = '#1a1a2e'
_PRIMARY = '#1565c0'
_POSITIVE = '#43a047'
_CRITICAL = '#c62828'
_HIGH = '#ef6c00'
_MEDIUM = '#f9a825'
_LOW = '#1565c0'
_INFO = '#78909c'
_GRID_COLOR = '#d0d0d0'
_BG_COLOR = '#ffffff'

_SEVERITY_COLORS = {
    'CRITICAL': _CRITICAL,
    'HIGH': _HIGH,
    'MEDIUM': _MEDIUM,
    'LOW': _LOW,
    'INFO': _INFO,
}

_SUBCIRCUIT_COLORS = {
    'rc_filter': _PRIMARY,
    'rl_filter': _PRIMARY,
    'lc_filter': _PRIMARY,
    'voltage_divider': _POSITIVE,
    'opamp': '#7b1fa2',
}

# ======================================================================
# Shared styling
# ======================================================================

_FIGSIZE = (8, 5)


def _apply_style(ax: plt.Axes, title: str) -> None:
    """Apply consistent styling to an axes object."""
    ax.set_title(title, fontsize=14, fontweight='bold', color=_NAVY, pad=12)
    ax.set_facecolor(_BG_COLOR)
    ax.figure.set_facecolor(_BG_COLOR)
    ax.grid(True, linestyle='--', linewidth=0.5, color=_GRID_COLOR, alpha=0.7)
    ax.tick_params(labelsize=10)
    for spine in ax.spines.values():
        spine.set_color('#cccccc')


def _save(fig: plt.Figure, path: str) -> str:
    """Save figure as SVG and close. Returns the output path."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, format='svg', bbox_inches='tight')
    plt.close(fig)
    return path


# ======================================================================
# 1. Thermal margin chart
# ======================================================================

def generate_thermal_margin(thermal_data: dict, output_path: str) -> Optional[str]:
    """Bar chart of junction temperature margin per component.

    Shows Tj estimated (blue) stacked with margin to Tj_max.
    Margin color: green (>20 C), yellow (10-20 C), red (<10 C).
    Only includes components with Tj > 40 C.

    Returns output path on success, None if data is missing/empty.
    """
    assessments = thermal_data.get('thermal_assessments', [])
    if not assessments:
        return None

    # Filter to components above 40 C
    hot = [a for a in assessments if a.get('tj_estimated_c', 0) > 40]
    if not hot:
        return None

    # Sort by estimated Tj descending
    hot.sort(key=lambda a: a.get('tj_estimated_c', 0), reverse=True)

    refs = [a.get('ref', '?') for a in hot]
    tj_est = [a.get('tj_estimated_c', 0) for a in hot]
    margins = [a.get('margin_c', 0) for a in hot]
    tj_max_vals = [a.get('tj_max_c', 125) for a in hot]

    # Margin bar colors
    margin_colors = []
    for m in margins:
        if m < 10:
            margin_colors.append(_CRITICAL)
        elif m < 20:
            margin_colors.append(_MEDIUM)
        else:
            margin_colors.append(_POSITIVE)

    fig, ax = plt.subplots(figsize=_FIGSIZE)

    x = range(len(refs))
    bars_tj = ax.bar(x, tj_est, label='Tj estimated', color=_PRIMARY, alpha=0.85)
    bars_margin = ax.bar(x, margins, bottom=tj_est, label='Margin to Tj_max',
                         color=margin_colors, alpha=0.7)

    # Horizontal line at the most common Tj_max
    from collections import Counter
    tj_max_common = Counter(tj_max_vals).most_common(1)[0][0]
    ax.axhline(y=tj_max_common, color=_CRITICAL, linestyle='--', linewidth=1.2,
               label=f'Tj_max ({tj_max_common}\u00b0C)')

    # Add value labels on Tj bars
    for bar, tj in zip(bars_tj, tj_est):
        ax.text(bar.get_x() + bar.get_width() / 2, tj / 2,
                f'{tj:.0f}\u00b0C', ha='center', va='center',
                fontsize=8, color='white', fontweight='bold')

    # Add margin labels
    for bar, m, base in zip(bars_margin, margins, tj_est):
        if m > 5:  # Only label if there's room
            ax.text(bar.get_x() + bar.get_width() / 2, base + m / 2,
                    f'+{m:.0f}\u00b0C', ha='center', va='center',
                    fontsize=8, color=_NAVY)

    ax.set_xticks(x)
    ax.set_xticklabels(refs, fontsize=10)
    ax.set_ylabel('Temperature (\u00b0C)', fontsize=11)
    ax.set_xlabel('Component', fontsize=11)
    ax.legend(fontsize=9, loc='upper right')

    _apply_style(ax, 'Thermal Margin Analysis')
    ax.set_axisbelow(True)

    return _save(fig, output_path)


# ======================================================================
# 2. EMC severity chart
# ======================================================================

def generate_emc_severity_chart(emc_data: dict, output_path: str) -> Optional[str]:
    """Horizontal stacked bar chart of EMC findings by category and severity.

    Returns output path on success, None if data is missing/empty.
    """
    findings = emc_data.get('findings', [])
    if not findings:
        return None

    # Count findings per category per severity
    categories: Dict[str, Dict[str, int]] = {}
    for f in findings:
        cat = f.get('category', 'other')
        sev = f.get('severity', 'INFO').upper()
        categories.setdefault(cat, {})
        categories[cat][sev] = categories[cat].get(sev, 0) + 1

    if not categories:
        return None

    # Sort categories by total count descending
    sorted_cats = sorted(categories.keys(),
                         key=lambda c: sum(categories[c].values()),
                         reverse=True)

    # Prettify category names: io_filtering -> IO Filtering
    pretty_names = []
    for cat in sorted_cats:
        pretty = cat.replace('_', ' ').title()
        pretty_names.append(pretty)

    severity_order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']

    fig, ax = plt.subplots(figsize=_FIGSIZE)

    y_pos = range(len(sorted_cats))
    left = [0] * len(sorted_cats)

    for sev in severity_order:
        counts = [categories[cat].get(sev, 0) for cat in sorted_cats]
        if sum(counts) == 0:
            continue
        color = _SEVERITY_COLORS.get(sev, _INFO)
        ax.barh(y_pos, counts, left=left, label=sev.capitalize(),
                color=color, alpha=0.85, height=0.6)
        # Update cumulative left
        left = [l + c for l, c in zip(left, counts)]

    ax.set_yticks(y_pos)
    ax.set_yticklabels(pretty_names, fontsize=10)
    ax.set_xlabel('Number of Findings', fontsize=11)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.legend(fontsize=9, loc='lower right')
    ax.invert_yaxis()  # Highest count on top

    _apply_style(ax, 'EMC Findings by Category')
    ax.set_axisbelow(True)

    return _save(fig, output_path)


# ======================================================================
# 3. SPICE validation scatter
# ======================================================================

def generate_spice_validation(spice_data: dict, output_path: str) -> Optional[str]:
    """Scatter plot of expected vs simulated values from SPICE results.

    Points colored by subcircuit type, with +-10% tolerance band
    and perfect-match diagonal.

    Returns output path on success, None if data is missing/empty.
    """
    results = spice_data.get('simulation_results', [])
    if not results:
        return None

    # Collect plottable points: need both expected and simulated fc_hz
    points: List[Dict[str, Any]] = []
    for r in results:
        expected = r.get('expected', {})
        simulated = r.get('simulated', {})
        stype = r.get('subcircuit_type', 'unknown')

        # Try fc_hz first (filters), then ratio (dividers), then gain
        for metric in ('fc_hz', 'ratio', 'gain_db'):
            exp_val = expected.get(metric)
            sim_val = simulated.get(metric)
            if exp_val is not None and sim_val is not None:
                label = ', '.join(r.get('components', [])[:2])
                points.append({
                    'expected': exp_val,
                    'simulated': sim_val,
                    'type': stype,
                    'label': label,
                    'metric': metric,
                })
                break

    if not points:
        return None

    fig, ax = plt.subplots(figsize=_FIGSIZE)

    # Plot diagonal and tolerance band
    all_vals = [p['expected'] for p in points] + [p['simulated'] for p in points]
    vmin = min(all_vals) * 0.8
    vmax = max(all_vals) * 1.2
    if vmin <= 0:
        vmin = min(v for v in all_vals if v > 0) * 0.5 if any(v > 0 for v in all_vals) else 0.1

    diag = [vmin, vmax]
    ax.plot(diag, diag, '--', color='#888888', linewidth=1, label='Perfect match')
    ax.fill_between(diag, [d * 0.9 for d in diag], [d * 1.1 for d in diag],
                    alpha=0.1, color=_PRIMARY, label='\u00b110% tolerance')

    # Group by subcircuit type
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for p in points:
        by_type.setdefault(p['type'], []).append(p)

    for stype, pts in by_type.items():
        color = _SUBCIRCUIT_COLORS.get(stype, '#546e7a')
        xs = [p['expected'] for p in pts]
        ys = [p['simulated'] for p in pts]
        ax.scatter(xs, ys, c=color, s=60, alpha=0.85, edgecolors='white',
                   linewidths=0.5, label=stype.replace('_', ' ').title(), zorder=5)
        # Labels
        for p in pts:
            if p['label']:
                ax.annotate(p['label'],
                            (p['expected'], p['simulated']),
                            textcoords='offset points', xytext=(6, 6),
                            fontsize=7, color=_NAVY, alpha=0.8)

    # Log scale if range spans more than 2 decades
    if vmax / max(vmin, 1e-12) > 100:
        ax.set_xscale('log')
        ax.set_yscale('log')

    ax.set_xlabel('Expected Value', fontsize=11)
    ax.set_ylabel('Simulated Value', fontsize=11)
    ax.legend(fontsize=9, loc='upper left')

    _apply_style(ax, 'SPICE Simulation Validation')
    ax.set_axisbelow(True)

    return _save(fig, output_path)


# ======================================================================
# 4. Monte Carlo histogram
# ======================================================================

def generate_monte_carlo(spice_data: dict, output_path: str) -> Optional[str]:
    """Histogram of Monte Carlo tolerance analysis results.

    Shows distribution with mean, +-1 sigma, and +-3 sigma markers.

    Returns output path on success, None if data is missing/empty.
    """
    results = spice_data.get('simulation_results', [])
    if not results:
        return None

    # Find the first result with tolerance_analysis.statistics
    tol_stats = None
    metric_name = None
    subcircuit_label = None
    for r in results:
        ta = r.get('tolerance_analysis', {})
        stats = ta.get('statistics', {})
        if stats:
            # Pick the first metric with full statistics
            for name, s in stats.items():
                if all(k in s for k in ('mean', 'std', 'min', 'max', 'nominal')):
                    tol_stats = s
                    metric_name = name
                    subcircuit_label = r.get('subcircuit_type', 'circuit')
                    break
        if tol_stats:
            break

    if not tol_stats:
        return None

    mean = tol_stats['mean']
    std = tol_stats['std']
    nominal = tol_stats['nominal']
    spread_pct = tol_stats.get('spread_pct', 0)
    p3lo = tol_stats.get('p3sigma_lo', mean - 3 * std)
    p3hi = tol_stats.get('p3sigma_hi', mean + 3 * std)

    # If we have individual MC samples in the data, use them; otherwise
    # synthesize a normal distribution for visualization
    mc_values = tol_stats.get('_values')  # not always present
    if not mc_values:
        # Generate synthetic samples from statistics
        import numpy as np
        rng = np.random.default_rng(42)
        mc_values = rng.normal(mean, std, 1000).tolist()

    fig, ax = plt.subplots(figsize=_FIGSIZE)

    # Histogram
    n_bins = min(40, max(15, len(mc_values) // 10))
    ax.hist(mc_values, bins=n_bins, color=_PRIMARY, alpha=0.6,
            edgecolor='white', linewidth=0.5, density=True)

    # Mean line
    ax.axvline(mean, color=_NAVY, linewidth=1.5, linestyle='-', label=f'Mean: {mean:.4g}')

    # Nominal line
    if abs(nominal - mean) / max(abs(nominal), 1e-12) > 0.001:
        ax.axvline(nominal, color=_POSITIVE, linewidth=1.2, linestyle='--',
                   label=f'Nominal: {nominal:.4g}')

    # +-1 sigma band
    ax.axvspan(mean - std, mean + std, alpha=0.12, color=_PRIMARY,
               label=f'\u00b11\u03c3: {std:.4g}')

    # +-3 sigma markers
    ax.axvline(p3lo, color=_CRITICAL, linewidth=1, linestyle=':',
               label=f'\u00b13\u03c3 bounds')
    ax.axvline(p3hi, color=_CRITICAL, linewidth=1, linestyle=':')

    # Format metric name for display
    pretty_metric = metric_name.replace('_', ' ').replace('hz', 'Hz').replace('db', 'dB')
    pretty_type = subcircuit_label.replace('_', ' ').title()

    ax.set_xlabel(pretty_metric.title(), fontsize=11)
    ax.set_ylabel('Density', fontsize=11)
    ax.legend(fontsize=9, loc='upper right')

    title = f'Monte Carlo: {pretty_type} \u2014 {pretty_metric.title()}'
    if spread_pct:
        title += f' (spread {spread_pct:.1f}%)'
    _apply_style(ax, title)
    ax.set_axisbelow(True)

    return _save(fig, output_path)


# ======================================================================
# 5. Convenience: generate all figures
# ======================================================================

def generate_all_figures(analysis_cache: dict, output_dir: str) -> List[str]:
    """Generate all applicable figures from available analysis data.

    Args:
        analysis_cache: Dict with optional keys 'thermal', 'emc', 'spice'.
            Each value is the parsed JSON from the corresponding analyzer.
        output_dir: Directory to write SVG files into.

    Returns:
        List of generated figure file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    generated: List[str] = []

    thermal = analysis_cache.get('thermal')
    if thermal:
        path = generate_thermal_margin(thermal, os.path.join(output_dir, 'thermal_margin.svg'))
        if path:
            generated.append(path)

    emc = analysis_cache.get('emc')
    if emc:
        path = generate_emc_severity_chart(emc, os.path.join(output_dir, 'emc_severity.svg'))
        if path:
            generated.append(path)

    spice = analysis_cache.get('spice')
    if spice:
        path = generate_spice_validation(spice, os.path.join(output_dir, 'spice_validation.svg'))
        if path:
            generated.append(path)

        path = generate_monte_carlo(spice, os.path.join(output_dir, 'monte_carlo.svg'))
        if path:
            generated.append(path)

    return generated


# ======================================================================
# CLI
# ======================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate analysis charts from SPICE, thermal, and EMC data.')
    parser.add_argument('--analysis-dir', required=True,
                        help='Directory containing analysis JSON files '
                             '(emc.json, thermal.json, spice.json)')
    parser.add_argument('--output', required=True,
                        help='Output directory for SVG figures')
    args = parser.parse_args()

    analysis_dir = Path(args.analysis_dir)
    cache: Dict[str, Any] = {}

    for name in ('thermal', 'emc', 'spice'):
        json_path = analysis_dir / f'{name}.json'
        if json_path.is_file():
            try:
                with open(json_path) as f:
                    cache[name] = json.load(f)
                print(f'  Loaded {name}.json')
            except (json.JSONDecodeError, OSError) as e:
                print(f'  Warning: could not load {json_path}: {e}', file=sys.stderr)

    if not cache:
        print('No analysis data found. Nothing to generate.')
        return

    generated = generate_all_figures(cache, args.output)

    if generated:
        print(f'\nGenerated {len(generated)} figure(s):')
        for p in generated:
            print(f'  {p}')
    else:
        print('\nNo figures generated (data may be below thresholds).')


if __name__ == '__main__':
    main()
