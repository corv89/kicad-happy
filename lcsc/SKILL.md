---
name: lcsc
description: Search LCSC Electronics for electronic components — find parts by LCSC number or keyword, check stock/pricing, buy components, datasheets, specifications. Sister company to JLCPCB, same parts library. Use with KiCad. Use this skill when the user mentions LCSC, JLCPCB parts library, JLCPCB assembly parts, production sourcing, Cxxxxx part numbers, needs to find LCSC equivalents for parts, or is preparing a BOM for JLCPCB assembly. For package cross-reference tables and BOM workflow, see the `bom` skill.
---

# LCSC Electronics — Component Search & Ordering

LCSC Electronics is a major electronic components distributor based in Shenzhen, China. It is a sister company to JLCPCB (common ownership) — they share the same parts library. LCSC handles component distribution; JLCPCB handles PCB fabrication and assembly.

LCSC is used for **production component sourcing** — when ordering 100s of assembled boards from JLCPCB or PCBWay. DigiKey (primary) and Mouser (secondary) are used for prototyping. For overall BOM management and export workflows, see the `bom` skill.

## Related Skills

| Skill | Purpose |
|-------|---------|
| `kicad` | Read/analyze KiCad project files (schematics, PCB, symbols, footprints) |
| `bom` | BOM management, ordering workflow, export formats |
| `digikey` | Search DigiKey (prototype sourcing, primary — also preferred for datasheet downloads via API) |
| `mouser` | Search Mouser (prototype sourcing, secondary) |
| `jlcpcb` | PCB fabrication & assembly ordering (uses LCSC parts) |
| `pcbway` | Alternative PCB fabrication & assembly |

## Key Differences from DigiKey/Mouser

- **Lower prices** — especially for passives and Chinese-manufactured ICs
- **JLCPCB integration** — same LCSC part numbers used in JLCPCB assembly BOMs
- **Low MOQ** — many parts available in quantities as low as 1
- **Warehouses** — Shenzhen (JS), Zhuhai (ZH), Hong Kong (HK)
- **Website**: `https://www.lcsc.com`

## LCSC Part Numbers

Format: `Cxxxxx` (e.g., `C14663`). This is the universal identifier across both LCSC and JLCPCB. Use it for:
- Direct ordering on LCSC
- BOM matching in JLCPCB assembly (see `jlcpcb` skill)
- Cross-referencing between platforms

## Searching for Parts

### Option 1: jlcsearch Community API (Free, No Auth)

The easiest way to search LCSC parts programmatically. No authentication required.

**Base URL:** `https://jlcsearch.tscircuit.com`

#### General Search
```
GET /api/search?q=100nF+0402&limit=20
```
Parameters:
- `q` — search query (matches description, MPN, or LCSC code)
- `package` — optional footprint filter
- `limit` — max results (default 100)
- `full` — boolean, include all database fields

#### Category-Specific Search
```
GET /resistors/list.json?search=10k+0402
GET /capacitors/list.json?search=100nF+0402
GET /microcontrollers/list.json?search=STM32
GET /voltage_regulators/list.json?search=3.3V
```

#### Response Format

Results are returned as a JSON array. No pagination — use `limit` to control result count.

```json
[
  {
    "lcsc": "C14663",
    "mfr": "Murata",
    "package": "0402",
    "description": "100nF 16V X7R 0402",
    "stock": 248000,
    "price": 0.0015
  }
]
```
Add `full=true` for all database fields (datasheet URL, detailed specs, etc.).

### Option 2: LCSC Official API (Requires Approval)

**Base URL:** `https://ips.lcsc.com`. Requires API key + signature authentication (key, nonce, signature, timestamp as query params). Contact `support@lcsc.com` for access. Rarely needed — jlcsearch covers most use cases.

### Option 3: LCSC Website (Manual)

Search at: `https://www.lcsc.com/search?q=<query>`

## Cross-Referencing with Other Distributors

LCSC part numbers are specific to the LCSC/JLCPCB ecosystem. To cross-reference:

1. Get the **MPN** (Manufacturer Part Number) from the LCSC listing
2. Search the same MPN on DigiKey or Mouser to compare specs/pricing
3. Verify the footprint matches your KiCad library footprint

Typical workflow: use DigiKey/Mouser for prototyping (fast domestic shipping), then find LCSC equivalents for JLCPCB assembly production runs (lower cost at volume).

## Handling Missing LCSC Equivalents

When an MPN has no exact match on LCSC:
1. Search by key parameters instead (e.g., "100nF 0402 X7R 16V")
2. Look for pin-compatible alternatives from Chinese manufacturers (often much cheaper)
3. Verify the alternative meets the same electrical specs (voltage, current, tolerance)
4. Check if the footprint is identical — even within the same package size, pad dimensions can vary
5. As a last resort, mark the part as "consigned" in the BOM and source it separately

## Tips

- **LCSC part number is the key identifier** — use it for ordering and JLCPCB BOM matching
- **Check stock per warehouse** — availability varies by location (JS/ZH/HK)
- **LCSC for production, DigiKey/Mouser for prototyping** — LCSC is cheaper at volume but ships from China
- **MOQ and multiples** — some parts have minimum order quantities or must be ordered in multiples
- **Datasheet quality varies** — Chinese manufacturers sometimes have sparse datasheets; cross-reference MPN on other distributor sites for better docs
- The jlcsearch API is community-maintained and free — best for quick lookups without API keys
- For JLCPCB assembly-specific info (BOM format, CPL, basic/extended parts), see the `jlcpcb` skill
