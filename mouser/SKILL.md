---
name: mouser
description: Search Mouser Electronics for electronic components — secondary source for prototype orders. Find parts, check pricing/stock, download datasheets, analyze specifications. Use with KiCad for BOM creation and part selection. Use this skill when the user specifically mentions Mouser, when DigiKey is out of stock or has worse pricing, when comparing prices across distributors, or when searching for parts that DigiKey doesn't carry. For package cross-reference tables and BOM workflow, see the `bom` skill.
---

# Mouser Electronics Parts Search & Analysis

You help users search for electronic components on Mouser, analyze specifications, compare parts, and find datasheets. Mouser is the **secondary source for prototype orders** — use when DigiKey is out of stock or has worse pricing. For production orders, see the `lcsc` and `jlcpcb` skills. For overall BOM management, package cross-reference, and export workflows, see the `bom` skill.

## Related Skills

| Skill | Purpose |
|-------|---------|
| `kicad` | Read/analyze KiCad project files (schematics, PCB, symbols, footprints) |
| `bom` | BOM management, ordering workflow, export formats |
| `digikey` | Search DigiKey (prototype sourcing, primary — also preferred for datasheet downloads via API) |
| `lcsc` | Search LCSC (production sourcing, JLCPCB parts) |
| `jlcpcb` | PCB fabrication & assembly ordering |
| `pcbway` | Alternative PCB fabrication & assembly |

## Web Search (No API Key Required)

The most common way to search Mouser — no credentials needed:

- Search URL: `https://www.mouser.com/c/?q=<query>`
- Use WebFetch to retrieve search results
- Filter panel on left for category, manufacturer, parameters
- Product pages contain full specs, pricing tiers, stock, datasheets

When searching, always include key parameters in the query:
- **Passives**: value, package (0402/0603/0805), tolerance, voltage/power rating, dielectric (C0G/X7R)
- **ICs**: part number or function, package (QFN/SOIC/TSSOP), key specs (voltage, current, interface)
- **Connectors**: type (USB-C, JST-PH), pin count, pitch, mounting (SMD/THT), orientation

## Mouser API Overview

Mouser uses a **simple API key** authentication (no OAuth). Register at My Mouser account page > Personal Information > APIs > Manage.

### Authentication

All endpoints use `apiKey` as a **query parameter** (not a header):
```
POST https://api.mouser.com/api/v2/search/keywordandmanufacturer?apiKey=<YOUR_API_KEY>
```
- No OAuth, no tokens — just a UUID API key
- Two separate keys: one for Search, one for Cart/Order
- Content-Type: `application/json`

Check `~/.config/secrets.env` for `MOUSER_PART_API_KEY` and `MOUSER_ORDER_API_KEY`. If not present, fall back to WebFetch (see Web Search section above).

## Search API Endpoints

### Keyword Search (V1)
```
POST /api/v1/search/keyword?apiKey=<key>
```
```json
{
  "SearchByKeywordRequest": {
    "keyword": "100nF 0402 ceramic capacitor",
    "records": 50,
    "startingRecord": 0,
    "searchOptions": "InStock"
  }
}
```
- `searchOptions`: `None` | `Rohs` | `InStock` | `RohsAndInStock`

### Keyword + Manufacturer Search (V2)
```
POST /api/v2/search/keywordandmanufacturer?apiKey=<key>
```
Adds `manufacturerName` filter and `pageNumber` pagination.

### Part Number Search (V1)
```
POST /api/v1/search/partnumber?apiKey=<key>
```
```json
{
  "SearchByPartRequest": {
    "mouserPartNumber": "GRM155R71C104KA88D|RC0402FR-0710KL",
    "partSearchOptions": "Exact"
  }
}
```
Up to 10 part numbers, pipe-separated (`|`).

## Search Response Structure

```json
{
  "SearchResults": {
    "NumberOfResult": 142,
    "Parts": [
      {
        "MouserPartNumber": "81-GRM155R71C104KA8D",
        "ManufacturerPartNumber": "GRM155R71C104KA88D",
        "Manufacturer": "Murata",
        "Description": "Multilayer Ceramic Capacitors MLCC - SMD/SMT 100nF 16V X7R 0402",
        "DataSheetUrl": "https://www.mouser.com/datasheet/...",
        "Availability": "In Stock",
        "AvailabilityInStock": "24000",
        "PriceBreaks": [
          {"Quantity": 1, "Price": "$0.010", "Currency": "USD"},
          {"Quantity": 10, "Price": "$0.006", "Currency": "USD"}
        ],
        "ProductAttributes": [
          {"AttributeName": "Capacitance", "AttributeValue": "100nF"},
          {"AttributeName": "Package / Case", "AttributeValue": "0402 (1005 Metric)"}
        ],
        "ROHSStatus": "RoHS Compliant",
        "IsDiscontinued": "false",
        "Min": "1",
        "Mult": "1"
      }
    ]
  }
}
```

### Key Response Fields

| Field | Description |
|-------|-------------|
| `MouserPartNumber` | Mouser's internal part number |
| `ManufacturerPartNumber` | Manufacturer's part number (MPN) |
| `DataSheetUrl` | Link to PDF datasheet |
| `AvailabilityInStock` | Quantity in stock (string) |
| `Min` / `Mult` | Minimum order quantity / order multiple |
| `PriceBreaks[]` | Tiered pricing (Quantity, Price, Currency) |
| `ProductAttributes[]` | Parametric specs (name/value pairs) |
| `IsDiscontinued` | Discontinuation flag |
| `SuggestedReplacement` | Replacement part if discontinued |

## Cart API

Uses a separate API key. Cart endpoints use version `v1.0`.

```json
{
  "CartKey": "",
  "CartItems": [
    {"MouserPartNumber": "546-1590B", "Quantity": 1}
  ]
}
```
- Leave `CartKey` empty to create new cart (key returned in response)
- Max 100 items per request, 399 total cart items
- Only Mouser part numbers accepted (not MPNs)

## Tips

- Mouser part numbers are prefixed (e.g., `81-GRM155R71C104KA8D`) — the prefix is Mouser-specific
- Use `ManufacturerPartNumber` (MPN) for cross-referencing across distributors
- Check `IsDiscontinued` and `LifecycleStatus` — avoid obsolete parts for new designs
- `Min` and `Mult` fields matter: some parts have minimum order qty or must be ordered in multiples
- `Reeling: true` means tape-and-reel packaging is available
- Pipe-separate up to 10 part numbers in a single search for efficient batch lookups
- The `searchOptions: "InStock"` filter is highly recommended
