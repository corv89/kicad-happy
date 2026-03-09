---
name: bom
description: BOM (Bill of Materials) management for electronics projects — create, update, and maintain BOMs with part numbers, costs, quantities, descriptions stored as KiCad symbol properties. Integrates with KiCad, Interactive BOM (ibom), DigiKey, Mouser, LCSC, JLCPCB, PCBWay. Order parts, compare pricing, track sourcing across prototype and production phases. Use whenever the user wants to manage, create, export, compare, or price a bill of materials, even if they don't say "BOM" explicitly — phrases like "what parts do I need", "order components", "how much will this cost", "export for JLCPCB", "find parts for this board", or "cost estimate" should trigger this skill.
---

# BOM Management

You help users create, maintain, and manage Bills of Materials for electronics projects. BOM data lives in **KiCad schematic symbol properties** as the single source of truth. This skill covers the full lifecycle from prototype through production.

## Related Skills

| Skill | Purpose |
|-------|---------|
| `kicad` | Read/analyze KiCad project files (schematics, PCB, symbols, footprints) |
| `digikey` | Search DigiKey (prototype sourcing, primary) |
| `mouser` | Search Mouser (prototype sourcing, secondary) |
| `lcsc` | Search LCSC (production sourcing, JLCPCB parts) |
| `jlcpcb` | PCB fabrication & assembly ordering |
| `pcbway` | Alternative PCB fabrication & assembly |

## Package/Footprint Cross-Reference

Common imperial-to-metric and KiCad footprint mapping:

| Imperial | Metric | KiCad Footprint (typical) |
|----------|--------|--------------------------|
| 0201 | 0603 | `Resistor_SMD:R_0201_0603Metric` |
| 0402 | 1005 | `Resistor_SMD:R_0402_1005Metric` |
| 0603 | 1608 | `Resistor_SMD:R_0603_1608Metric` |
| 0805 | 2012 | `Resistor_SMD:R_0805_2012Metric` |
| 1206 | 3216 | `Resistor_SMD:R_1206_3216Metric` |

Replace `Resistor_SMD:R_` with `Capacitor_SMD:C_` or `Inductor_SMD:L_` as appropriate.

## Design-to-Production Workflow

### Phase 1: Prototype Design (Rev 1)

1. **Design schematic and layout** in KiCad
2. **Populate BOM fields** in symbol properties — at minimum MPN and DigiKey part numbers
3. **Order bare PCB + framed stencil** from JLCPCB (cheapest/fastest for prototype boards)
4. **Order components** from DigiKey (primary) or Mouser (secondary/backup)
   - Generate cart-upload CSV from BOM
   - Typical lead time: ~1 week for everything to arrive
5. **Hand-assemble prototype** in lab — apply solder paste with stencil, place components using ibom, reflow
6. **Test and document issues** — note design changes needed

### Phase 2: Iterate (Rev 2, 3, ...)

1. **Update schematic** with fixes and improvements
2. **Diff BOM** against previous revision — identify new/changed/removed parts
3. **Source new parts** — search DigiKey/Mouser, update symbol properties
4. **Re-order** bare PCB + framed stencil from JLCPCB + components from DigiKey/Mouser
5. **Assemble, test, repeat** until design is solid

### Phase 3: Production

1. **Finalize BOM** — ensure all symbol properties are complete (MPN, LCSC, DigiKey, Mouser)
2. **Find LCSC equivalents** for all parts (search by MPN on LCSC/jlcsearch)
3. **Identify basic vs extended parts** for JLCPCB assembly cost estimation
4. **Export production BOM** in JLCPCB or PCBWay format
5. **Order assembled boards** (100s qty) from JLCPCB or PCBWay using LCSC parts
6. **Keep DigiKey/Mouser PNs** in the schematic for future prototype runs or field repairs

## Source of Truth: KiCad Symbol Properties

All BOM data is stored as custom properties (fields) on schematic symbols in the `.kicad_sch` file. This keeps everything version-controlled with the project.

### Standard KiCad Fields

| Field | Description | Example |
|-------|-------------|---------|
| `Reference` | Designator (auto-assigned) | `C1`, `U3`, `R5` |
| `Value` | Component value | `100nF`, `ESP32-S3-WROOM-1` |
| `Footprint` | Library:footprint | `Capacitor_SMD:C_0402_1005Metric` |
| `Datasheet` | URL to datasheet | `https://...` |
| `Description` | Part description | `100nF 16V X7R 0402 MLCC` |

### Custom BOM Fields

Add these as custom symbol properties:

| Field | Purpose | When Needed | Example |
|-------|---------|-------------|---------|
| `MPN` | Manufacturer Part Number — universal cross-reference key | Always | `GRM155R71C104KA88D` |
| `Manufacturer` | Part manufacturer | Always | `Murata` |
| `DigiKey` | DigiKey part number — primary prototype source | Prototype (Phase 1-2) | `490-10698-1-ND` |
| `Mouser` | Mouser part number — secondary prototype source | Prototype (Phase 1-2) | `81-GRM155R71C104KA8D` |
| `LCSC` | LCSC part number — production assembly source | Production (Phase 3) | `C14663` |

Optional fields:

| Field | Purpose | Example |
|-------|---------|---------|
| `AltMPN` | Alternate/second-source MPN | `CL05B104KO5NNNC` |
| `Notes` | Sourcing or assembly notes | `Basic JLCPCB part` |

### Adding Properties in KiCad

**Single symbol**: Double-click or press E > click "+" to add a field > enter name and value.

**Bulk editing**: Tools > Edit Symbol Fields opens a spreadsheet view. Add columns, edit values, export/import CSV.

**Field Name Templates** (KiCad 9+): Schematic Setup > Field Name Templates. Pre-define MPN, Manufacturer, LCSC, DigiKey, Mouser so they appear on every new symbol automatically.

### Symbol Properties in `.kicad_sch` Format

Custom fields are stored as `(property ...)` entries, typically hidden:

```
(property "MPN" "GRM155R71C104KA88D"
    (at 0 0 0)
    (effects (font (size 1.27 1.27)) (hide yes))
)
```

## Extracting and Presenting the BOM

Read the `.kicad_sch` file and extract all component symbols with their properties. Group identical components (same Value + Footprint + MPN) and sum quantities.

```
| Ref | Qty | Value | Footprint | MPN | Manufacturer | DigiKey | Mouser | LCSC |
|-----|-----|-------|-----------|-----|--------------|---------|--------|------|
| C1,C2,C5 | 3 | 100nF | 0402 | GRM155R71C104KA88D | Murata | 490-10698-1-ND | 81-GRM... | C14663 |
```

## Enriching with Distributor Data

For parts missing distributor info, search to fill gaps:

**By MPN** (most reliable — cross-references across all distributors):
- DigiKey: keyword search with MPN (see `digikey` skill)
- Mouser: part number search with MPN (see `mouser` skill)
- LCSC: jlcsearch with MPN (see `lcsc` skill)

**By value + footprint** (when MPN unknown):
- Search distributors with descriptive keywords (e.g., "100nF 0402 X7R 16V")
- Select best match, record MPN

**Priority during prototyping**: focus on DigiKey first (primary), Mouser second (backup). LCSC numbers can wait until production phase.

**Priority for production**: ensure every part has an LCSC number. Search by MPN on jlcsearch or LCSC.

**Datasheets**: When you need to fetch a datasheet for validation or analysis, use the DigiKey API first (see the `digikey` skill) — it returns direct PDF URLs via the `DatasheetUrl` field, which is faster and more reliable than web searching. Fall back to WebSearch only if the part isn't on DigiKey.

After finding parts, **write the data back** into the schematic symbol properties.

## Ordering Parts

### Prototype Orders: DigiKey (Primary)

**Bulk Add (preferred)** — paste directly into DigiKey's "Bulk Add to Cart" box. One product per line, comma-delimited: `quantity, DigiKey part number, customer reference`:
```
3, 490-10698-1-ND, C1/C2/C5
1, ESP32-S3-WROOM-1-N16R8-ND, U1
5, 311-10.0KCRCT-ND, R1/R2/R3/R4/R5
```

When generating a BOM for DigiKey ordering, output this bulk-add text so the user can copy-paste it directly.

**CSV upload** — alternative for larger orders: My DigiKey > BOM Manager > Upload BOM.
```csv
Quantity,Part Number,Customer Reference
3,490-10698-1-ND,C1/C2/C5
```

### Prototype Orders: Mouser (Secondary)

Mouser cart upload CSV:
```csv
Mouser Part Number,Quantity,Customer Part Number
81-GRM155R71C104KA8D,3,C1/C2/C5
```
Upload at Mouser: Order > Upload a Spreadsheet.

### Production Orders: JLCPCB Assembly

See the `jlcpcb` skill for BOM/CPL format, basic vs extended parts, and ordering workflow.

### Production Orders: PCBWay Assembly

See the `pcbway` skill for BOM format (MPN-based), turnkey vs consigned options, and ordering workflow.

## Gerber & Stencil Export for PCB Fabrication

### Required Gerber Layers

Export from KiCad: Fabrication > Plot (format: Gerber, coordinate format: 4.6 mm).

| KiCad Layer | Description |
|-------------|-------------|
| F.Cu / B.Cu | Front/back copper |
| F.Paste / B.Paste | Solder paste (for stencil) |
| F.SilkS / B.SilkS | Silkscreen |
| F.Mask / B.Mask | Solder mask |
| Edge.Cuts | Board outline |

Also generate Excellon drill files (Fabrication > Generate Drill Files). Zip all gerber + drill files together for upload.

### CPL (Component Placement List)

Export from KiCad: Fabrication > Generate Placement Files (CSV format).

| Column | Description |
|--------|-------------|
| `Designator` | Reference designator |
| `Mid X` / `Mid Y` | Component center position (mm) |
| `Rotation` | Rotation angle (degrees) |
| `Layer` | `Top` or `Bottom` |

Both JLCPCB and PCBWay use the same CPL format.

### Stencil Ordering

When ordering bare prototype PCBs, also order a **framed stencil** for solder paste application:
- Framed stencil (~$7 from JLCPCB) — rigid frame for use with a stencil jig; recommended for lab hand assembly
- Stencil is generated from the F.Paste (and optionally B.Paste) gerber layers
- Order as a separate cart item alongside the PCB order

## Price Comparison

Compare across distributors at target quantity. Always query current pricing via the `digikey`, `mouser`, and `lcsc` skills — prices change frequently.

```
| Part | Qty | DigiKey | Mouser | LCSC |
|------|-----|---------|--------|------|
| 100nF 0402 (C1,C2,C5) | 3 | $0.010 | $0.009 | $0.002 |
| ESP32-S3-WROOM-1 (U1) | 1 | $3.20 | $3.45 | $2.80 |
```
*(Prices are illustrative — always query current pricing.)*

### Cost Summary

```
BOM Summary — Project: sacmap-rev1
===================================
Unique parts:     23
Total components:  87
DNP:               3

Prototype (1 board, DigiKey):
  Parts:       $45.23
  PCB (JLC):   ~$7 (5 boards min, 2-layer)
  Stencil:     ~$7 (framed, from JLC)
  Shipping:    ~$5-12
  Total:       ~$64-71

Production (100 boards, JLCPCB assembled):
  Parts/board:     $8.50
  PCB fab/board:    $0.80
  Assembly/board:   $2.50
  Extended fees:    $0.09/board (3 extended x $3 / 100)
  Per board:        ~$11.89
  Total (100):      ~$1,189
```

## BOM Diffing Between Revisions

When the schematic changes between revisions:

```
BOM Diff: Rev 1 -> Rev 2
=========================
Added:
  + U3 (ESP32-S3-WROOM-1) — needs MPN, DigiKey
  + C10,C11 (22uF 0805) — needs sourcing

Removed:
  - U2 (ESP-WROOM-02)
  - C8 (10uF 0603)

Changed:
  ~ R5: 10k -> 4.7k (value change — check if same MPN family)
  ~ C3: 0402 -> 0603 (footprint change — new part needed)

Unchanged: 18 unique parts (72 components)
New parts to source: 3
```

## Edit Symbol Fields — CSV Round-Trip

The most powerful way to bulk-edit BOM data:

1. **Export**: Tools > Edit Symbol Fields > Export to CSV
2. **Edit**: open CSV in a spreadsheet or script, add/update MPN, DigiKey, Mouser, LCSC columns
3. **Import**: Tools > Edit Symbol Fields > Import from CSV
   - Reference designators must match exactly — mismatched references cause import failures
   - New columns become new symbol properties
   - Existing values are overwritten
   - **Back up the `.kicad_sch` before importing** — the import overwrites in place with no undo

Ideal for bulk-adding distributor part numbers found via API searches.

## Interactive HTML BOM (ibom)

Generates a visual HTML page showing component locations on the PCB — essential for hand-assembling prototypes in the lab.

### Installation

```bash
pip install InteractiveHtmlBom
```
Or: KiCad Plugin and Content Manager > search "Interactive HTML BOM"

### Recommended Command

```bash
generate_interactive_bom board.kicad_pcb \
  --dest-dir bom/ \
  --name-format "%f_ibom_%r" \
  --extra-fields "MPN,Manufacturer,DigiKey,Mouser,LCSC" \
  --group-fields "Value,Footprint,MPN" \
  --checkboxes "Sourced,Placed" \
  --dnp-field "DNP" \
  --sort-order "C,R,L,D,U,Y,X,F,SW,A,~,HS,CNN,J,P,NT,MH" \
  --no-browser
```

This produces an HTML file with:
- Visual PCB — click a BOM row to highlight components on the board
- BOM table showing MPN and all distributor part numbers
- Sourced/Placed checkboxes for tracking hand assembly progress
- DNP components excluded
- Grouped by Value + Footprint + MPN

### Prototype Assembly Workflow

1. Parts arrive from DigiKey/Mouser (~1 week after ordering)
2. Bare PCBs + framed stencil arrive from JLCPCB (~1 week)
3. Generate ibom with `--checkboxes "Sourced,Placed"`
4. Open HTML in browser at the workbench
5. Apply solder paste using the framed stencil
6. Work through BOM groups — click each row in ibom to highlight placement on PCB
7. Place components, check "Placed" as you go
8. Reflow solder (hot plate, oven, or hot air)
9. Hand-solder any through-hole components

## DNP Handling

Components marked Do Not Populate are handled consistently across the ecosystem:
- KiCad: set the `DNP` attribute on the symbol (or use a custom `DNP` field)
- ibom: use `--dnp-field "DNP"` to exclude from the visual BOM
- JLCPCB BOM export: omit DNP components from the CSV entirely
- PCBWay BOM export: omit DNP components or mark with a note

## Production Readiness Checklist

Before ordering production assembled boards:

- [ ] All parts have MPN populated
- [ ] All parts have LCSC numbers (for JLCPCB) or MPN (for PCBWay turnkey)
- [ ] No obsolete or end-of-life parts
- [ ] Stock verified on LCSC for all parts (for JLCPCB)
- [ ] Basic vs extended parts identified (JLCPCB cost impact)
- [ ] BOM exported in correct format (JLCPCB or PCBWay)
- [ ] CPL/centroid file exported and verified
- [ ] Gerbers exported and verified
- [ ] Design rules meet manufacturer minimums (see `jlcpcb` or `pcbway` skill)
- [ ] Prototype fully tested — no more design changes expected

## Tips

- **MPN is the universal key** — always populate it first; enables cross-referencing all distributors
- **Schematic is the source of truth** — all BOM data in symbol properties, exported as needed
- **DigiKey first, Mouser second** — for prototyping, DigiKey is primary source, Mouser is backup
- **LCSC numbers can wait** — don't need them until production phase; focus on DigiKey/Mouser for prototyping
- **CSV round-trip** — use Edit Symbol Fields export/import for bulk updates
- **Version in git** — the `.kicad_sch` file contains all BOM data; commit with meaningful messages per revision
- **ibom per revision** — regenerate when the board changes; keep in project for lab reference
- **Field Name Templates** — set up MPN, Manufacturer, LCSC, DigiKey, Mouser as templates in KiCad 9
- **Second source** — use `AltMPN` field for critical parts in case primary goes out of stock
- **Price at target qty** — unit prices vary dramatically; prototype qty pricing != production qty pricing
- **Alternate footprints** — if a part is only available in a different package (e.g., 0402 needed but only 0603 in stock on LCSC), update the footprint in the schematic, re-run DRC on the PCB, and update the BOM. Don't just swap the LCSC number without verifying the footprint matches.
