#!/usr/bin/env python3
"""
SPICE simulation orchestrator for kicad-happy.

Reads analyzer JSON output (from analyze_schematic.py), identifies simulatable
subcircuits, generates ngspice testbenches, runs simulations, and produces a
structured report.

Usage:
    python3 simulate_subcircuits.py analysis.json
    python3 simulate_subcircuits.py analysis.json --output sim_report.json
    python3 simulate_subcircuits.py analysis.json --workdir /tmp/spice_runs
    python3 simulate_subcircuits.py analysis.json --timeout 10
    python3 simulate_subcircuits.py analysis.json --types rc_filters,voltage_dividers

Requires: ngspice (install: apt install ngspice / brew install ngspice)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# Allow imports from same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spice_templates import TEMPLATE_REGISTRY, TOPLEVEL_REGISTRY, list_supported_types, SpiceTestbench
from spice_results import (
    EVALUATOR_REGISTRY,
    build_report,
    parse_output_file,
)

# Map detector key → singular subcircuit_type for output
# Most are just rstrip("s"), but some need special handling
_SINGULAR = {
    "decoupling_analysis": "decoupling",
    "power_regulators": "regulator_feedback",
    "rf_matching": "rf_matching",
    "bridge_circuits": "bridge_circuit",
    "inrush_analysis": "inrush",
    "bms_systems": "bms_balance",
    "rf_chains": "rf_chain",
    "snubber_circuits": "snubber_circuit",
}


def _singular_type(det_type):
    """Convert a detector key to its singular subcircuit_type name."""
    if det_type in _SINGULAR:
        return _SINGULAR[det_type]
    return det_type.rstrip("s")


def find_ngspice():
    """Locate the ngspice binary. Returns path or None.

    Legacy wrapper — prefer detect_simulator() for multi-simulator support.
    """
    from spice_simulator import NgspiceBackend
    backend = NgspiceBackend()
    return backend.find()


def run_ngspice(cir_file, timeout=5):
    """Run ngspice in batch mode on a .cir file.

    Legacy wrapper — prefer simulator_backend.run() for multi-simulator.
    """
    from spice_simulator import NgspiceBackend
    backend = NgspiceBackend()
    return backend.run(cir_file, timeout=timeout)


def simulate_subcircuits(analysis_json, workdir=None, timeout=5, types=None,
                         parasitics=None, simulator_backend=None):
    """Run SPICE simulations for all simulatable subcircuits in the analysis.

    Args:
        analysis_json: Parsed JSON dict from analyze_schematic.py
        workdir: Directory for .cir and output files (default: temp dir)
        timeout: Seconds per simulation (default: 5)
        types: List of detector types to simulate, or None for all supported

    Returns:
        Report dict with simulation results
    """
    signal = analysis_json.get("signal_analysis", {})
    if not signal:
        return build_report([])

    # Synthesize snubber_circuits from transistor_circuits with snubber_data
    tc = signal.get("transistor_circuits", [])
    if isinstance(tc, list):
        snubber_circuits = [t for t in tc if isinstance(t, dict) and t.get("snubber_data")]
        if snubber_circuits:
            signal["snubber_circuits"] = snubber_circuits

    # Filter to requested types
    sim_types = types if types else list_supported_types()

    # Set up working directory
    cleanup_workdir = False
    if workdir is None:
        workdir = tempfile.mkdtemp(prefix="spice_sim_")
        cleanup_workdir = True
    else:
        os.makedirs(workdir, exist_ok=True)

    results = []
    total_time = 0

    def _run_detection_batch(det_type, detections, generator, evaluator):
        """Simulate a batch of detections — shared by signal_analysis and top-level loops."""
        nonlocal total_time
        for i, det in enumerate(detections):
            det["_context"] = analysis_json
            if parasitics:
                det["_parasitics"] = parasitics

            label = _make_label(det_type, det, i)
            cir_file = os.path.join(workdir, f"{label}.cir")
            out_file = os.path.join(workdir, f"{label}.out")
            log_file = os.path.join(workdir, f"{label}.log")
            out_file_spice = out_file.replace("\\", "/")

            try:
                cir_content = generator(det, out_file_spice)
            except (KeyError, TypeError, ValueError) as e:
                results.append({
                    "subcircuit_type": _singular_type(det_type),
                    "components": _get_components(det),
                    "status": "skip",
                    "note": f"Testbench generation failed: {e}",
                })
                continue

            if cir_content is None:
                continue

            if isinstance(cir_content, SpiceTestbench):
                cir_content = cir_content.render(simulator_backend, out_file_spice)

            with open(cir_file, "w") as f:
                f.write(cir_content)

            t0 = time.monotonic()
            if simulator_backend:
                success, stdout, stderr = simulator_backend.run(cir_file, timeout=timeout)
            else:
                success, stdout, stderr = run_ngspice(cir_file, timeout=timeout)
            elapsed = time.monotonic() - t0
            total_time += elapsed

            with open(log_file, "w") as f:
                f.write(f"=== stdout ===\n{stdout}\n=== stderr ===\n{stderr}\n")

            if not success:
                results.append({
                    "subcircuit_type": _singular_type(det_type),
                    "components": _get_components(det),
                    "status": "skip",
                    "note": f"Simulation failed: {stderr[:200]}",
                    "cir_file": cir_file,
                    "log_file": log_file,
                    "elapsed_s": round(elapsed, 3),
                })
                continue

            if simulator_backend and hasattr(simulator_backend, 'parse_results'):
                sim_data = simulator_backend.parse_results(out_file, stdout, stderr)
            else:
                sim_data = parse_output_file(out_file)

            if evaluator:
                result = evaluator(det, sim_data)
            else:
                result = {
                    "subcircuit_type": _singular_type(det_type),
                    "components": _get_components(det),
                    "status": "pass",
                    "simulated": sim_data,
                }

            result["cir_file"] = cir_file
            result["log_file"] = log_file
            result["elapsed_s"] = round(elapsed, 3)
            results.append(result)

    # Process signal_analysis detections
    for det_type in sim_types:
        if det_type not in signal or det_type not in TEMPLATE_REGISTRY:
            continue
        detections = signal[det_type]
        if not isinstance(detections, list):
            continue
        _run_detection_batch(det_type, detections,
                            TEMPLATE_REGISTRY[det_type],
                            EVALUATOR_REGISTRY.get(det_type))

    # Process top-level types (not under signal_analysis)
    for tl_type, (list_key, generator) in TOPLEVEL_REGISTRY.items():
        if types and tl_type not in types:
            continue
        tl_data = analysis_json.get(tl_type, {})
        if not tl_data:
            continue
        detections = tl_data.get(list_key, [])
        if not isinstance(detections, list):
            continue
        _run_detection_batch(tl_type, detections,
                            generator, EVALUATOR_REGISTRY.get(tl_type))

    report = build_report(results)
    report["workdir"] = workdir
    report["total_elapsed_s"] = round(total_time, 3)
    report["ngspice"] = find_ngspice()

    return report


def _make_label(det_type, det, index):
    """Generate a filesystem-safe label for a subcircuit simulation."""
    parts = []
    # Singular form of detector type
    parts.append(_singular_type(det_type).replace("_", "-"))
    # Component references
    comps = _get_components(det)
    if comps:
        parts.append("_".join(comps))
    else:
        parts.append(f"idx{index}")
    label = "_".join(parts)
    # Sanitize for filesystem
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in label)


def _get_components(det):
    """Extract component reference list from any detector dict."""
    refs = []
    # Try common field patterns from different detector types
    for key in ("resistor", "r_top", "inductor", "shunt"):
        if key in det and isinstance(det[key], dict) and "ref" in det[key]:
            refs.append(det[key]["ref"])
    for key in ("capacitor", "r_bottom"):
        if key in det and isinstance(det[key], dict) and "ref" in det[key]:
            refs.append(det[key]["ref"])
    if "reference" in det:
        refs.append(det["reference"])
    # Feedback/input resistors for opamps
    for key in ("feedback_resistor", "input_resistor"):
        if key in det and isinstance(det[key], dict) and "ref" in det[key]:
            refs.append(det[key]["ref"])
    # Decoupling capacitor list
    if "capacitors" in det and isinstance(det["capacitors"], list):
        for c in det["capacitors"][:5]:  # Limit to 5 for label length
            if isinstance(c, dict) and "ref" in c:
                refs.append(c["ref"])
    # BMS: IC ref + balance resistor refs
    if "bms_reference" in det:
        refs.append(det["bms_reference"])
        br_list = det.get("balance_resistors", [])
        if not isinstance(br_list, list):
            br_list = []  # Old format was int count, not list
        for br in br_list[:3]:
            if isinstance(br, dict) and "reference" in br:
                refs.append(br["reference"])
    # Snubber components
    sd = det.get("snubber_data")
    if isinstance(sd, dict):
        if sd.get("resistor_ref"):
            refs.append(sd["resistor_ref"])
        if sd.get("capacitor_ref"):
            refs.append(sd["capacitor_ref"])
    # RF chain components
    for cat in ("transceivers", "amplifiers", "filters", "switches", "mixers"):
        for c in det.get(cat, [])[:2]:
            if isinstance(c, dict) and "reference" in c:
                refs.append(c["reference"])
    # Power regulators: IC ref + feedback divider resistors
    if "ref" in det and "feedback_divider" in det:
        refs.append(det["ref"])
        fd = det["feedback_divider"]
        if isinstance(fd, dict):
            for k in ("r_top", "r_bottom"):
                v = fd.get(k)
                if isinstance(v, dict) and "ref" in v:
                    refs.append(v["ref"])
    # RF matching components
    if "antenna" in det and "components" in det:
        for c in det.get("components", [])[:5]:
            if isinstance(c, dict) and "ref" in c:
                refs.append(c["ref"])
    return refs


def main():
    parser = argparse.ArgumentParser(
        description="Run SPICE simulations for detected subcircuits"
    )
    parser.add_argument(
        "input",
        help="Path to analyzer JSON output (from analyze_schematic.py --output)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Path to write simulation report JSON (default: stdout)",
    )
    parser.add_argument(
        "--workdir", "-w",
        help="Directory for simulation files (default: temp dir)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=5,
        help="Timeout per simulation in seconds (default: 5)",
    )
    parser.add_argument(
        "--types",
        help="Comma-separated list of detector types to simulate "
             f"(default: all supported: {', '.join(list_supported_types())})",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Omit file paths from output (for clean reports)",
    )
    parser.add_argument(
        "--parasitics",
        help="Path to parasitics JSON (from extract_parasitics.py) for "
             "PCB-aware simulation. When provided, testbenches include trace "
             "resistance and via inductance.",
    )
    parser.add_argument(
        "--simulator",
        choices=["auto", "ngspice", "ltspice", "xyce"],
        default="auto",
        help="SPICE simulator to use (default: auto-detect)",
    )

    args = parser.parse_args()

    # Detect simulator
    from spice_simulator import detect_simulator
    simulator_backend = detect_simulator(args.simulator)
    if not simulator_backend:
        print("Error: no SPICE simulator found. Install one of:", file=sys.stderr)
        print("  ngspice: apt install ngspice / brew install ngspice", file=sys.stderr)
        print("  LTspice: download from analog.com/ltspice", file=sys.stderr)
        print("  Xyce:    download from xyce.sandia.gov", file=sys.stderr)
        print("  Or set NGSPICE_PATH env var to the ngspice binary path.", file=sys.stderr)
        sys.exit(1)

    # Read analysis JSON
    try:
        with open(args.input, "r") as f:
            analysis = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {args.input}: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse types filter
    types = None
    if args.types:
        types = [t.strip() for t in args.types.split(",")]
        supported = set(list_supported_types())
        unknown = set(types) - supported
        if unknown:
            print(f"Warning: unknown types ignored: {', '.join(unknown)}",
                  file=sys.stderr)
            types = [t for t in types if t in supported]

    # Load parasitics if provided
    parasitics_data = None
    if args.parasitics:
        try:
            with open(args.parasitics, "r") as f:
                parasitics_data = json.load(f)
            n_nets = len(parasitics_data.get("nets", {}))
            print(f"Loaded parasitics for {n_nets} nets from {args.parasitics}",
                  file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not load parasitics: {e}", file=sys.stderr)

    # Run simulations
    report = simulate_subcircuits(
        analysis,
        workdir=args.workdir,
        timeout=args.timeout,
        types=types,
        parasitics=parasitics_data,
        simulator_backend=simulator_backend,
    )

    # Clean up file paths if compact mode
    if args.compact:
        for r in report.get("simulation_results", []):
            r.pop("cir_file", None)
            r.pop("log_file", None)
        report.pop("workdir", None)

    # Output
    output_json = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        # Print summary to stderr
        s = report["summary"]
        print(
            f"Simulation complete: {s['total']} subcircuits — "
            f"{s['pass']} pass, {s['warn']} warn, {s['fail']} fail, "
            f"{s['skip']} skip ({report['total_elapsed_s']:.1f}s)",
            file=sys.stderr,
        )
    else:
        print(output_json)


if __name__ == "__main__":
    main()
