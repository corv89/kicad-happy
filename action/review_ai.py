#!/usr/bin/env python3
"""kicad-happy AI Design Review.

Runs deterministic analysis followed by focused AI review tasks.
Works both locally and in CI (GitHub Actions).

Usage:
    python3 action/review_ai.py <schematic.kicad_sch> [pcb.kicad_pcb]
    python3 action/review_ai.py design.kicad_sch --task 3
    python3 action/review_ai.py --skip-analysis --analysis-dir ./review
    python3 action/review_ai.py --ci design.kicad_sch design.kicad_pcb
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "kicad" / "scripts"
EMC_SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "emc" / "scripts"
SPICE_SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "spice" / "scripts"

API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-5.1"
DEFAULT_MAX_TOKENS = 4096
API_TIMEOUT = 180


def run_cmd(cmd, check=True):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        print(f"  STDERR: {result.stderr[:500]}", file=sys.stderr)
        raise RuntimeError(f"Command failed (exit {result.returncode}): {cmd[0]}")
    return result


def run_analysis(schematic, pcb, outdir):
    """Run kicad-happy deterministic analysis. Returns dict of output paths."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results = {}

    sch_json = outdir / "schematic.json"
    pcb_json = outdir / "pcb.json"
    emc_json = outdir / "emc.json"
    thermal_json = outdir / "thermal.json"
    spice_json = outdir / "spice.json"

    print("Phase 1: Deterministic Analysis")
    print("=" * 50)

    if schematic and Path(schematic).exists():
        print(f"\n[1/5] Schematic analysis: {schematic}")
        try:
            run_cmd(
                [sys.executable, str(SCRIPTS_DIR / "analyze_schematic.py"),
                 str(schematic), "--output", str(sch_json)]
            )
            results["schematic"] = str(sch_json)
        except RuntimeError as e:
            print(f"  FAILED: {e}", file=sys.stderr)

    if pcb and Path(pcb).exists():
        print(f"\n[2/5] PCB analysis: {pcb}")
        try:
            run_cmd(
                [sys.executable, str(SCRIPTS_DIR / "analyze_pcb.py"),
                 str(pcb), "--full", "--output", str(pcb_json)]
            )
            results["pcb"] = str(pcb_json)
        except RuntimeError as e:
            print(f"  FAILED: {e}", file=sys.stderr)

    if "schematic" in results or "pcb" in results:
        emc_args = [sys.executable, str(EMC_SCRIPTS / "analyze_emc.py"), "--output", str(emc_json)]
        if "schematic" in results:
            emc_args.extend(["--schematic", results["schematic"]])
        if "pcb" in results:
            emc_args.extend(["--pcb", results["pcb"]])
        print(f"\n[3/5] EMC analysis")
        try:
            run_cmd(emc_args)
            results["emc"] = str(emc_json)
        except RuntimeError:
            pass

    if "schematic" in results and "pcb" in results:
        print(f"\n[4/5] Thermal analysis")
        try:
            run_cmd(
                [sys.executable, str(SCRIPTS_DIR / "analyze_thermal.py"),
                 "--schematic", results["schematic"],
                 "--pcb", results["pcb"],
                 "--output", str(thermal_json)]
            )
            results["thermal"] = str(thermal_json)
        except RuntimeError:
            pass

    if "schematic" in results:
        print(f"\n[5/5] SPICE simulation (best-effort)")
        try:
            run_cmd(
                [sys.executable, str(SPICE_SCRIPTS / "simulate_subcircuits.py"),
                 results["schematic"], "--compact", "--output", str(spice_json)]
            )
            results["spice"] = str(spice_json)
        except RuntimeError:
            pass

    print(f"\nAnalysis outputs: {list(results.keys())}")
    return results


def load_json(path):
    if not path or not Path(path).exists():
        return {}
    with open(path) as f:
        return json.load(f)


def load_readme():
    for name in ("README.md", "readme.md", "Readme.md"):
        p = Path(name)
        if p.exists():
            return p.read_text()
    return ""


# ---------------------------------------------------------------------------
# Context extraction — one per task
# ---------------------------------------------------------------------------

def extract_findings_context(analysis):
    """Task 1: Findings arrays for triage."""
    sections = []
    for domain, data in analysis.items():
        findings = data.get("findings", [])
        if not findings:
            continue
        entries = []
        for f in findings:
            entry = {
                "severity": f.get("severity"),
                "rule_id": f.get("rule_id"),
                "summary": f.get("summary"),
                "confidence": f.get("confidence"),
                "components": f.get("components", []),
                "nets": f.get("nets", []),
                "recommendation": f.get("recommendation", ""),
            }
            entries.append(entry)
        sections.append(f"## {domain} ({len(findings)} findings)\n{json.dumps(entries, indent=2)}")
    return "\n\n".join(sections) if sections else "No findings found."


def extract_cross_domain_context(analysis):
    """Task 2: Findings grouped by component/net for cross-domain correlation."""
    by_component = {}
    by_net = {}

    for domain, data in analysis.items():
        for f in data.get("findings", []):
            severity = f.get("severity", "info")
            summary = f.get("summary", "")
            rule_id = f.get("rule_id", "")
            for comp in f.get("components", []):
                by_component.setdefault(comp, []).append(
                    f"[{domain}] {severity} {rule_id}: {summary}"
                )
            for net in f.get("nets", []):
                by_net.setdefault(net, []).append(
                    f"[{domain}] {severity} {rule_id}: {summary}"
                )

    lines = ["## Findings by Component"]
    for comp, items in sorted(by_component.items()):
        if len(items) >= 1:
            lines.append(f"### {comp}")
            for item in items:
                lines.append(f"  - {item}")

    lines.append("\n## Findings by Net")
    for net, items in sorted(by_net.items()):
        if len(items) >= 1:
            lines.append(f"### {net}")
            for item in items:
                lines.append(f"  - {item}")

    return "\n".join(lines) if len(lines) > 2 else "No cross-domain correlations found."


def extract_missed_issues_context(analysis):
    """Task 3: Component inventory and topology for gap analysis."""
    sch = analysis.get("schematic", {})
    pcb = analysis.get("pcb", {})

    lines = ["## Component Inventory"]
    comps = sch.get("components", [])
    for c in comps:
        ctype = c.get("type", "unknown")
        ref = c.get("reference", "?")
        value = c.get("value", "")
        lib = c.get("lib_id", "")
        dnp = c.get("dnp", False)
        lines.append(f"  {ref}: {value} ({ctype}, {lib}){' [DNP]' if dnp else ''}")

    lines.append("\n## Detected Subcircuits")
    for s in sch.get("subcircuits", []):
        center = s.get("center_ic", "?")
        desc = s.get("description", "")
        neighbors = ", ".join(
            n.get("ref", "?") for n in s.get("neighbor_components", [])
        )
        lines.append(f"  {center} ({desc}) — neighbors: {neighbors}")

    lines.append("\n## Power Rails")
    for rail in sch.get("statistics", {}).get("power_rails", []):
        if isinstance(rail, dict):
            lines.append(f"  {rail.get('name', '?')} ({rail.get('voltage', '?')}V)")
        else:
            lines.append(f"  {rail}")

    pcb_stats = pcb.get("statistics", {})
    if pcb_stats:
        lines.append("\n## PCB Layout Summary")
        lines.append(f"  Board: {pcb_stats.get('board_width_mm', '?')}x{pcb_stats.get('board_height_mm', '?')}mm")
        lines.append(f"  Layers: {pcb_stats.get('copper_layers_used', '?')}")
        lines.append(f"  Footprints: {pcb_stats.get('footprint_count', '?')}")
        lines.append(f"  Tracks: {pcb_stats.get('track_segments', '?')}")
        lines.append(f"  Vias: {pcb_stats.get('via_count', '?')}")
        lines.append(f"  Zones: {pcb_stats.get('zone_count', '?')}")
        lines.append(f"  Routing complete: {pcb_stats.get('routing_complete', '?')}")

    return "\n".join(lines)


def extract_pinout_context(analysis):
    """Task 4: IC and power component pin-to-net mapping."""
    sch = analysis.get("schematic", {})
    lines = ["## IC and Power Component Pinouts"]

    comps = sch.get("components", [])
    targets = [c for c in comps if c.get("type") in ("ic", "transistor", "connector")]

    for c in targets:
        ref = c.get("reference", "?")
        value = c.get("value", "")
        lib = c.get("lib_id", "")
        pin_nets = c.get("pin_nets", {})
        if not pin_nets:
            continue
        lines.append(f"\n### {ref}: {value} ({lib})")
        for pin, net in sorted(pin_nets.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]):
            lines.append(f"  Pin {pin} → {net}")

    lines.append("\n## Power Rail Connections")
    nets = sch.get("nets", {})
    power_keywords = ("+5V", "+3V3", "GND", "VBUS", "VCC", "VDD", "VSS", "+3.3V", "+5")
    for name, net_data in sorted(nets.items()):
        if any(name.startswith(pk) or name == pk for pk in power_keywords):
            pins = net_data.get("pins", [])
            pin_list = ", ".join(
                f"{p.get('component', '?')}.{p.get('pin_number', '?')}" for p in pins
            )
            lines.append(f"  {name}: {pin_list}")

    return "\n".join(lines)


def extract_intent_context(analysis, readme_text):
    """Task 5: Design intent from README + topology summary."""
    lines = ["## README (Design Intent)"]
    lines.append(readme_text[:3000])

    sch = analysis.get("schematic", {})
    lines.append("\n## Schematic Topology")
    stats = sch.get("statistics", {})
    lines.append(f"  Components: {stats.get('total_components', '?')} "
                 f"({stats.get('unique_parts', '?')} unique)")
    lines.append(f"  Nets: {stats.get('total_nets', '?')}")
    lines.append(f"  No-connects: {stats.get('total_no_connects', '?')}")

    for s in sch.get("subcircuits", []):
        center = s.get("center_ic", "?")
        desc = s.get("description", "")
        lines.append(f"  Subcircuit: {center} — {desc}")

    usb = sch.get("usb_compliance", {})
    if usb:
        lines.append(f"\n## USB Compliance")
        lines.append(json.dumps(usb, indent=2)[:1000])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task prompts
# ---------------------------------------------------------------------------

TASK_PROMPTS = {
    1: (
        "You are an electronics design reviewer. Triage the following analysis "
        "findings from a KiCad design review tool.\n\n"
        "For each finding:\n"
        "- Assess if it is a REAL ISSUE or an EXPECTED DESIGN DECISION\n"
        "- Rate priority: CRITICAL (board won't work), HIGH (reliability risk), "
        "MEDIUM (should fix), LOW (nice to have)\n"
        "- Provide a one-line actionable recommendation\n\n"
        "Group by severity: critical first, then high, medium, low.\n"
        "Keep total output under 2000 characters.\n\n"
        "{context}"
    ),
    2: (
        "You are an electronics design reviewer. Look for correlations between "
        "findings from different analysis domains (schematic, PCB, EMC, thermal).\n\n"
        "For each component or net that appears in multiple domains:\n"
        "- Summarize the combined concern\n"
        "- Assess whether the cross-domain overlap makes the issue worse\n"
        "- Recommend a specific action\n\n"
        "If no significant correlations exist, say so briefly.\n"
        "Keep total output under 1500 characters.\n\n"
        "{context}"
    ),
    3: (
        "You are an electronics design reviewer. Given the following component "
        "inventory, subcircuit topology, and PCB layout summary, identify common "
        "design issues that the automated analysis may have MISSED.\n\n"
        "Consider:\n"
        "- Missing decoupling or bulk capacitance\n"
        "- Incorrect component values for the application\n"
        "- Missing protection (ESD, overvoltage, reverse polarity)\n"
        "- Signal integrity concerns (termination, impedance matching)\n"
        "- Power sequencing or ramp issues\n"
        "- Thermal management gaps\n"
        "- Manufacturing issues (tenths, courtyard, silkscreen)\n\n"
        "List up to 5 missed issues with brief justification.\n"
        "Keep total output under 1500 characters.\n\n"
        "{context}"
    ),
    4: (
        "You are an electronics design reviewer. Verify the following IC and "
        "connector pin-to-net connections.\n\n"
        "Check:\n"
        "- Are power pins connected to the correct voltage rails?\n"
        "- Are ground pins properly grounded?\n"
        "- Do signal pins connect to expected nets for the IC function?\n"
        "- Are any pins suspiciously floating or connected to wrong nets?\n\n"
        "List any pinout issues found, or confirm connections look correct.\n"
        "Keep total output under 1500 characters.\n\n"
        "{context}"
    ),
    5: (
        "You are an electronics design reviewer. Compare the design intent "
        "(from the README) with the actual implementation (from the schematic topology).\n\n"
        "Check:\n"
        "- Does the schematic implement what the README describes?\n"
        "- Are there components or connections that don't match the stated purpose?\n"
        "- Are any described features missing from the implementation?\n"
        "- Does the connector pinout match the documented I/O?\n\n"
        "List any discrepancies, or confirm the implementation matches intent.\n"
        "Keep total output under 1500 characters.\n\n"
        "{context}"
    ),
}

TASK_NAMES = {
    1: "Triage Findings",
    2: "Cross-Domain Correlation",
    3: "Missed Issues",
    4: "Pinout Verification",
    5: "Design Intent Check",
}

EXTRACTORS = {
    1: extract_findings_context,
    2: extract_cross_domain_context,
    3: extract_missed_issues_context,
    4: extract_pinout_context,
    5: extract_intent_context,
}


# ---------------------------------------------------------------------------
# LLM API call
# ---------------------------------------------------------------------------

def call_llm(prompt, model=DEFAULT_MODEL, api_key=None, thinking=True, max_tokens=DEFAULT_MAX_TOKENS):
    if not api_key:
        api_key = os.environ.get("ZHIPU_API_KEY", "")
    if not api_key:
        raise RuntimeError("ZHIPU_API_KEY not set. Set env var or pass --api-key.")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 1.0,
    }
    if thinking:
        payload["thinking"] = {"type": "enabled"}

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            resp_data = json.loads(resp.read())
            content = resp_data["choices"][0]["message"]["content"]
            return content
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        raise RuntimeError(f"API error {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def combine_report(task_results, analysis_paths):
    lines = ["# AI Design Review\n"]

    det_summary = "Deterministic analysis completed for: " + ", ".join(
        k for k in analysis_paths
    )
    lines.append(det_summary)
    lines.append("")

    for task_num, result in sorted(task_results.items()):
        name = TASK_NAMES.get(task_num, f"Task {task_num}")
        status = result.get("status", "unknown")
        lines.append(f"## Task {task_num}: {name} ({status})")
        lines.append("")
        content = result.get("content", "")
        if content:
            lines.append(content)
        else:
            lines.append(result.get("error", "No output"))
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _get_issue_number():
    """Extract issue/PR number from GitHub Actions event JSON."""
    import re
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if event_path and Path(event_path).exists():
        with open(event_path) as f:
            event = json.load(f)
        if "issue" in event:
            return event["issue"].get("number")
        if "pull_request" in event:
            return event["pull_request"].get("number")
    ref = os.environ.get("GITHUB_REF", "")
    match = re.match(r"refs/(?:pull|issues)/(\d+)", ref)
    if match:
        return int(match.group(1))
    return None


def post_pr_comment(report, token=None):
    if not token:
        token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not all([token, repo]):
        print("Skipping comment: missing GITHUB_TOKEN or GITHUB_REPOSITORY")
        return

    number = _get_issue_number()
    if not number:
        print("Skipping comment: could not determine issue/PR number")
        return

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(report)
        f.flush()
        tmp_path = f.name

    try:
        subprocess.run(
            ["gh", "issue", "comment", str(number),
             "--body-file", tmp_path,
             "--edit-last"],
            check=True,
            env={**os.environ, "GH_TOKEN": token},
        )
        print(f"Posted comment on #{number}.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Failed to post comment: {e}", file=sys.stderr)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="kicad-happy AI Design Review")
    parser.add_argument("schematic", nargs="?", help="Path to .kicad_sch file")
    parser.add_argument("pcb", nargs="?", help="Path to .kicad_pcb file")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--api-key", help="Zhipu API key (or set ZHIPU_API_KEY env)")
    parser.add_argument("--task", type=int, choices=[1, 2, 3, 4, 5],
                        help="Run single task only (1-5)")
    parser.add_argument("--skip-analysis", action="store_true",
                        help="Skip Phase 1, reuse existing JSON from --analysis-dir")
    parser.add_argument("--analysis-dir", default="./kh-review",
                        help="Directory for analysis outputs (default: ./kh-review)")
    parser.add_argument("--ci", action="store_true",
                        help="CI mode: post result as PR comment")
    parser.add_argument("--no-thinking", action="store_true",
                        help="Disable extended thinking")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help=f"Max tokens per API call (default: {DEFAULT_MAX_TOKENS})")
    args = parser.parse_args()

    outdir = Path(args.analysis_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    analysis_paths = {}

    # Phase 1: Deterministic analysis
    if not args.skip_analysis:
        if not args.schematic:
            parser.error("schematic required when not using --skip-analysis")
        analysis_paths = run_analysis(args.schematic, args.pcb, outdir)
    else:
        print("Phase 1: Skipping analysis (--skip-analysis)")
        for name in ("schematic", "pcb", "emc", "thermal", "spice"):
            p = outdir / f"{name}.json"
            if p.exists():
                analysis_paths[name] = str(p)
        print(f"Loaded existing: {list(analysis_paths.keys())}")

    # Load analysis JSON
    analysis = {}
    for domain, path in analysis_paths.items():
        analysis[domain] = load_json(path)

    if not analysis:
        print("ERROR: No analysis data available.", file=sys.stderr)
        sys.exit(1)

    # Phase 2: AI review tasks
    print(f"\nPhase 2: AI Review (model={args.model}, thinking={'off' if args.no_thinking else 'on'})")
    print("=" * 50)

    readme_text = load_readme()
    tasks_to_run = [args.task] if args.task else [1, 2, 3, 4, 5]
    task_results = {}

    for task_num in tasks_to_run:
        name = TASK_NAMES.get(task_num, f"Task {task_num}")
        extractor = EXTRACTORS.get(task_num)
        prompt_template = TASK_PROMPTS.get(task_num)

        if not extractor or not prompt_template:
            print(f"\n[Task {task_num}] SKIPPED: unknown task")
            continue

        print(f"\n[Task {task_num}] {name}")

        if task_num == 5:
            context = extractor(analysis, readme_text)
        else:
            context = extractor(analysis)

        context_preview = context[:100].replace("\n", " ")
        print(f"  Context: {len(context)} chars — {context_preview}...")

        prompt = prompt_template.format(context=context)
        print(f"  Calling LLM...")

        try:
            response = call_llm(
                prompt,
                model=args.model,
                api_key=args.api_key,
                thinking=not args.no_thinking,
                max_tokens=args.max_tokens,
            )
            task_results[task_num] = {"status": "ok", "content": response}
            print(f"  Response: {len(response)} chars")
        except Exception as e:
            task_results[task_num] = {"status": "error", "error": str(e)}
            print(f"  FAILED: {e}", file=sys.stderr)

    # Phase 3: Combine report
    print(f"\nPhase 3: Report")
    print("=" * 50)

    report = combine_report(task_results, analysis_paths)

    report_path = outdir / "ai-review.md"
    report_path.write_text(report)
    print(f"Report written to: {report_path}")

    if args.ci:
        post_pr_comment(report)
    else:
        print(f"\n{'=' * 50}")
        print(report)


if __name__ == "__main__":
    main()
