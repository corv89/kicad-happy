# kicad-happy

AI-powered electronics design review skills for KiCad 5-10. This repo provides structured Python analysis scripts and skill definitions for AI coding agents.

## Skills

Each skill is defined in `skills/<name>/SKILL.md` with usage instructions and triggers. Read the relevant SKILL.md before using a skill.

| Skill | Purpose |
|-------|---------|
| kicad | Core analysis — schematic parsing, PCB layout review, Gerber verification, thermal, diff, what-if |
| emc | EMC pre-compliance — 42 rules across 17 categories |
| spice | SPICE simulation — auto-generated testbenches, Monte Carlo tolerance |
| kidoc | Engineering documentation — PDF/HTML reports, schematic SVG rendering |
| bom | BOM management — multi-supplier pricing, order file export |
| digikey | DigiKey API — component search, datasheet download |
| mouser | Mouser API — component search |
| lcsc | LCSC/JLCPCB parts — community API, no auth required |
| element14 | element14/Newark/Farnell API |
| jlcpcb | JLCPCB fab rules, BOM/CPL format |
| pcbway | PCBWay fab rules, turnkey assembly |

## Running analysis

```bash
# Schematic analysis
python3 skills/kicad/scripts/analyze_schematic.py <file>.kicad_sch
python3 skills/kicad/scripts/analyze_schematic.py <file>.kicad_sch --output analysis.json

# PCB layout analysis
python3 skills/kicad/scripts/analyze_pcb.py <file>.kicad_pcb
python3 skills/kicad/scripts/analyze_pcb.py <file>.kicad_pcb --full --output pcb.json

# EMC pre-compliance (requires schematic + PCB JSON)
python3 skills/emc/scripts/analyze_emc.py --schematic sch.json --pcb pcb.json

# Gerber verification
python3 skills/kicad/scripts/analyze_gerbers.py <gerber_dir>/
```

All scripts are zero-dependency (Python 3.8+ stdlib only). No pip install needed.

## Code structure

- `skills/kicad/scripts/analyze_schematic.py` — Schematic parser and analysis orchestrator (~7,200 LOC)
- `skills/kicad/scripts/signal_detectors.py` — Core signal path detectors (~3,000 LOC)
- `skills/kicad/scripts/domain_detectors.py` — Domain-specific detectors (~4,500 LOC)
- `skills/kicad/scripts/analyze_pcb.py` — PCB layout analyzer (~4,300 LOC)
- `skills/emc/scripts/emc_rules.py` — 42 EMC rule implementations (~4,100 LOC)
- `skills/kicad/scripts/kicad_types.py` — `AnalysisContext` dataclass shared by all detectors
- `skills/kicad/references/` — 14 deep methodology guides

## Key docs

- `CONTRIBUTING.md` — How detectors work, how to add skills, test harness usage
- `VALIDATION.md` — Test methodology and corpus statistics
- `CHANGELOG.md` — Release history
