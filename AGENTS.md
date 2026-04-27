# AGENTS.md — kicad-happy

AI-powered electronics design review suite for KiCad (v5–v10). 12 skills, ~30K LOC of pure Python analysis scripts. Zero external dependencies in core analysis.

## Project Structure

```
skills/
├── kicad/           # Core analysis (schematic, PCB, Gerber, thermal, cross-analysis)
│   ├── SKILL.md     # Skill definition, triggers, design review contract
│   ├── scripts/     # 31 Python scripts (stdlib only, Python 3.8+)
│   └── references/  # 15 deep methodology guides
├── emc/             # EMC pre-compliance (44 rules, 18 categories)
├── spice/           # SPICE simulation (ngspice/LTspice/Xyce)
├── datasheets/      # Per-MPN structured extraction cache
├── kidoc/           # Engineering documentation generation
├── bom/             # BOM management
├── digikey/         # DigiKey API integration
├── mouser/          # Mouser API integration
├── lcsc/            # LCSC/JLCPCB parts (no auth)
├── element14/       # element14/Newark/Farnell API
├── jlcpcb/          # JLCPCB fab rules and BOM/CPL format
└── pcbway/          # PCBWay fab rules
```

## Analysis Pipeline

Run analyzers in this order:

```bash
# 1. Schematic analysis (required first)
python3 skills/kicad/scripts/analyze_schematic.py design.kicad_sch -o schematic.json

# 2. PCB analysis (requires --full for EMC/thermal downstream)
python3 skills/kicad/scripts/analyze_pcb.py design.kicad_pcb --full -o pcb.json

# 3. Cross-analysis (schematic + PCB cross-domain checks)
python3 skills/kicad/scripts/cross_analysis.py --schematic schematic.json --pcb pcb.json -o cross.json

# 4. EMC pre-compliance (requires schematic JSON, PCB JSON recommended)
python3 skills/emc/scripts/analyze_emc.py --schematic schematic.json --pcb pcb.json -o emc.json

# 5. Thermal analysis (requires both schematic + PCB JSON)
python3 skills/kicad/scripts/analyze_thermal.py --schematic schematic.json --pcb pcb.json -o thermal.json

# 6. SPICE simulation (optional, requires ngspice/LTspice/Xyce)
python3 skills/spice/scripts/simulate_subcircuits.py schematic.json --compact -o spice.json

# 7. Gerber verification (when fabrication outputs exist)
python3 skills/kicad/scripts/analyze_gerbers.py gerber_dir/
```

All scripts support `--help` for full flag documentation.

## Finding Schema

Every analyzer produces findings via `make_finding()` (`skills/kicad/scripts/finding_schema.py`):

| Field | Values |
|-------|--------|
| `severity` | `error`, `warning`, `info` |
| `confidence` | `deterministic`, `heuristic`, `datasheet-backed` |
| `evidence_source` | `datasheet`, `topology`, `heuristic_rule`, `symbol_footprint`, `bom`, `geometry`, `api_lookup` |
| `category` | e.g., `signal_integrity`, `power_integrity`, `emc`, `thermal`, `dfm` |
| `detector` | Detector function name (see `Det` class in finding_schema.py) |
| `rule_id` | Unique rule identifier (e.g., `PU-001`, `DC-006`, `EMC-012`) |

### Trust Summary

Each analysis JSON includes a `trust_summary` block:
- `trust_level`: `high` (>80% deterministic), `mixed`, `low` (>50% heuristic)
- `by_confidence`: counts per confidence level
- `bom_coverage`: MPN and datasheet coverage percentages

### DS-001/DS-002/DS-003 Datasheet Findings

- `DS-001` (severity `high`): Datasheets not synced — all findings are consistency-only, NOT verified against manufacturer specs. Do not use "verified" or "per datasheet" language.
- `DS-002`: Datasheets missing but MPNs set — softer variant.
- `DS-003`: Partial MPN coverage — applies only to cited parts.

## Design Review Contract

When performing a full design review, the minimum checklist is:

1. `analyze_schematic.py` — run and review findings
2. `analyze_pcb.py --full` — run and review findings
3. `cross_analysis.py` — schematic-to-PCB cross checks
4. `analyze_emc.py` — EMC pre-compliance risk analysis
5. SPICE simulation — when any simulator is installed (check with `which ngspice ltspice xyce`)
6. `analyze_thermal.py` — when both schematic and PCB JSON exist
7. `analyze_gerbers.py` — when fabrication outputs exist
8. Datasheet coverage check — DS-001/002/003 findings
9. Raw schematic/PCB spot-verification for critical parts
10. Explicit sections for blockers, verification basis, false positives, and skipped analyses

If any step was skipped, state why and how it limits confidence.

## Code Conventions (for contributing to kicad-happy)

- **Python 3.8+ compatibility** — no walrus operators, no `match` statements
- **Zero external dependencies** in analysis scripts — stdlib only
- **Functions over classes** — detectors are plain functions
- **Inline equation tags**: `# EQ-NNN: formula_name (source)` with source citation
- Use `from __future__ import annotations` for modern type hints
- `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE_CASE` constants

## Testing

```bash
# Smoke test (verify all scripts load)
python3 skills/kicad/scripts/analyze_schematic.py --help
python3 skills/kicad/scripts/analyze_pcb.py --help

# Full test harness (separate repo)
git clone https://github.com/aklofas/kicad-happy-testharness.git
python3 run/run_schematic.py --jobs 16
python3 regression/run_checks.py --type schematic
```

## GitHub Action

kicad-happy is also a composite GitHub Action (`action.yml`). Hardware design repos use it as:

```yaml
- uses: aklofas/kicad-happy@v1
  id: analysis
  with:
    severity: all
    derating-profile: commercial
```

Outputs: `schematic-json`, `pcb-json`, `report-path`, `findings-count`, `has-critical`.

See `action/examples/` for complete workflow templates including opencode integration.
