---
name: element14
description: Search Newark, Farnell, and element14 for electronic components — find parts by MPN or distributor part number, check pricing/stock, download datasheets, analyze specifications. One unified API covers all three storefronts (Newark for US, Farnell for UK/EU, element14 for APAC). Free API key, simple query-parameter auth, no OAuth. Datasheets download directly from farnell.com CDN with no bot protection. Sync and maintain a local datasheets directory for a KiCad project. Use this skill when the user mentions Newark, Farnell, element14, needs parts from a non-US distributor, wants to compare pricing across regions, or needs datasheets from a source that doesn't require complex API auth. For package cross-reference tables and BOM workflow, see the `bom` skill.
---

# element14 / Newark / Farnell — Component Search, Datasheets & Ordering

One API covers three regional storefronts — same catalog, same datasheets, only pricing/stock vary by region:

| Storefront | Region | Store ID |
|------------|--------|----------|
| **Newark** | North America | `www.newark.com` |
| **Farnell** | UK / Europe | `uk.farnell.com` |
| **element14** | Asia-Pacific | `au.element14.com` |

For BOM management and export workflows, see `bom`.

## Key Differences from DigiKey/Mouser

- **Simple auth** — API key as a query parameter, no OAuth flow
- **Free API key** — register at partner.element14.com, courtesy usage allowance
- **Global coverage** — same API covers US (Newark), EU (Farnell), APAC (element14)
- **Unprotected PDFs** — datasheets hosted on farnell.com CDN, download freely with no bot protection
- **Datasheet URL in API response** — `responseGroup=medium` includes `datasheets[].url`

## API Credential Setup

1. **Register** at [partner.element14.com/member/register](https://partner.element14.com/member/register)
   - Free account — just username, email, password. No credit card needed.
   - Provides a "courtesy usage allowance" (2 calls/sec, 1,000 calls/day — sufficient for normal use)
2. **Register an application** — after logging in, go to [My API Keys](https://partner.element14.com/apps/mykeys) and click "Get API Keys"
   - App name: anything (e.g., "claude-code")
   - Type: "Desktop application"
   - Users: "1-10"
   - Commercial: No
   - Advertising: No
   - Check "Issue a new key for Product Search API" → select "Basic" tier
   - Agree to Terms of Service and click "Register Application"
3. **Copy your API key** — a 24-character alphanumeric string shown on the My API Keys page
4. **Set the environment variable** `ELEMENT14_API_KEY` before running the scripts:
   ```bash
   export ELEMENT14_API_KEY=your_api_key_here
   ```
   If credentials are stored in a central secrets file (e.g., `~/.config/secrets.env`), load them first:
   ```bash
   export $(grep -v '^#' ~/.config/secrets.env | grep -v '^$' | xargs)
   ```

## Product Search API

**Base URL:** `https://api.element14.com/catalog/products`

All requests use GET with query parameters. Authentication is via `callInfo.apiKey`.

### Search Modes

The `term` parameter supports three search types:

| Mode | Format | Example |
|------|--------|---------|
| **Keyword** | `any:<keywords>` | `term=any:100nF 0402 X7R` |
| **MPN** | `manuPartNum:<mpn>` | `term=manuPartNum:GRM155R71C104KA88D` |
| **Distributor PN** | `id:<sku>` | `term=id:94AK6874` |

### Full Example

```
GET https://api.element14.com/catalog/products
  ?term=manuPartNum:GRM155R71C104KA88D
  &storeInfo.id=www.newark.com
  &resultsSettings.offset=0
  &resultsSettings.numberOfResults=10
  &resultsSettings.responseGroup=medium
  &callInfo.responseDataFormat=JSON
  &callInfo.apiKey=YOUR_KEY
```

### Response Groups

| Group | Fields |
|-------|--------|
| `small` | SKU, displayName, brandName, MPN, attributes |
| `medium` | + datasheets[], prices[], stock |
| `large` | + images, related products, country of origin |
| `prices` | Tiered pricing only |
| `inventory` | Stock levels by warehouse/region |

### Response Format

With `responseGroup=medium`, the response looks like:

```json
{
  "manufacturerPartNumberSearchReturn": {
    "numberOfResults": 5,
    "products": [
      {
        "sku": "94AK6874",
        "displayName": "Murata GRM155R71C104KA88D",
        "translatedManufacturerPartNumber": "GRM155R71C104KA88D",
        "brandName": "Murata Electronics",
        "datasheets": [
          {
            "type": "TechnicalDataSheet",
            "description": "Datasheet",
            "url": "https://www.farnell.com/datasheets/74273.pdf"
          }
        ],
        "prices": [
          {
            "from": 1,
            "to": 9,
            "cost": 0.156
          }
        ],
        "stock": {
          "level": 45000,
          "leastLeadTime": 0,
          "status": 4,
          "statusMessage": "In Stock"
        },
        "attributes": [
          {"attributeLabel": "Capacitance", "attributeUnit": "", "attributeValue": "100nF"},
          {"attributeLabel": "Voltage Rating", "attributeUnit": "V", "attributeValue": "16"}
        ],
        "rohsStatusCode": "YES"
      }
    ]
  }
}
```

Key fields:
- `sku` — Newark/Farnell/element14 part number
- `translatedManufacturerPartNumber` — MPN
- `brandName` — manufacturer
- `datasheets[].url` — **direct PDF URL** (farnell.com CDN, no bot protection)
- `datasheets[].type` — usually `TechnicalDataSheet`
- `prices[]` — tiered pricing with `from`, `to`, `cost`
- `stock.level` — quantity in stock
- `stock.statusMessage` — human-readable availability
- `attributes[]` — parametric specs (label, unit, value)
- `rohsStatusCode` — RoHS compliance (`YES`/`NO`)

### Store IDs

Common store IDs for the `storeInfo.id` parameter:

| Store ID | Region |
|----------|--------|
| `www.newark.com` | US (default) |
| `uk.farnell.com` | UK |
| `www.farnell.com` | EU |
| `au.element14.com` | Australia |
| `sg.element14.com` | Singapore |
| `in.element14.com` | India |

### Rate Limits

No documented rate limits beyond the courtesy usage allowance. Be respectful — use 0.5s delays between calls.

### Filters

Add to query parameters:
- `resultsSettings.refinements.filter=rohsCompliant` — RoHS parts only
- `resultsSettings.refinements.filter=inStock` — in-stock only

### Pagination

- `resultsSettings.offset` — starting index (0-based)
- `resultsSettings.numberOfResults` — max 50 per page
- Only the first 100 results are reliably pageable

## Datasheet Download & Sync

element14's farnell.com CDN serves datasheet PDFs directly — no bot protection, no special headers needed. Datasheet URLs come from the API response (`datasheets[].url`).

### Datasheet Directory Sync

Use `sync_datasheets_element14.py` to maintain a `datasheets/` directory alongside a KiCad project. Same workflow and `index.json` format as the DigiKey, Mouser, and LCSC skills.

```bash
# Sync datasheets for a KiCad project
python3 <skill-path>/scripts/sync_datasheets_element14.py <file.kicad_sch>

# Preview what would be downloaded
python3 <skill-path>/scripts/sync_datasheets_element14.py <file.kicad_sch> --dry-run

# Retry previously failed downloads
python3 <skill-path>/scripts/sync_datasheets_element14.py <file.kicad_sch> --force

# Use a specific store (default: www.newark.com)
python3 <skill-path>/scripts/sync_datasheets_element14.py <file.kicad_sch> --store uk.farnell.com

# Custom output directory
python3 <skill-path>/scripts/sync_datasheets_element14.py <file.kicad_sch> -o ./my-datasheets
```

The script:
- **Runs the kicad schematic analyzer** to extract components, MPNs, and distributor PNs
- **Accepts any identifier** — MPN, Newark/Farnell PN, or other distributor PNs from KiCad symbol properties
- **Prefers MPN search** (`manuPartNum:`) for exact match — falls back to keyword search
- **Downloads from farnell.com CDN** — direct PDF URLs, no bot protection
- **Writes `index.json` manifest** — same format as DigiKey/Mouser/LCSC skills
- **Verifies PDF content** — checks MPN, manufacturer, and description keywords
- **Rate-limited** — 0.5s between API calls (configurable with `--delay`)
- **Saves progress incrementally** — safe to interrupt

### Single Datasheet Download

Use `fetch_datasheet_element14.py` for one-off downloads.

```bash
# Search by MPN
python3 <skill-path>/scripts/fetch_datasheet_element14.py --search "GRM155R71C104KA88D" -o datasheet.pdf

# Search by Newark/Farnell part number
python3 <skill-path>/scripts/fetch_datasheet_element14.py --search "94AK6874" -o datasheet.pdf

# Direct URL download
python3 <skill-path>/scripts/fetch_datasheet_element14.py "https://www.farnell.com/datasheets/74273.pdf" -o datasheet.pdf

# JSON output
python3 <skill-path>/scripts/fetch_datasheet_element14.py --search "GRM155R71C104KA88D" --json
```

The script:
- **OS-agnostic** — uses `requests` → `urllib` → `playwright` fallback chain
- **Validates PDF headers** — rejects HTML error pages
- **Falls back to alternative manufacturer sources** when element14 URL fails
- **Exit codes**: 0 = success, 1 = download failed, 2 = search/API error
- **Dependencies**:
  - `pip install requests` (recommended; urllib fallback works fine for element14)
  - `pip install playwright && playwright install chromium` (optional; rarely needed)

## Web Search Fallback

If the API is unavailable, search via WebFetch:

```
https://www.newark.com/search?st=<query>
https://uk.farnell.com/search?st=<query>
```

## Tips

- Use `responseGroup=medium` — includes datasheets and pricing without the overhead of `large`
- Use `manuPartNum:` prefix for exact MPN matches; `any:` for keyword search
- Cross-reference using `translatedManufacturerPartNumber` (MPN) across DigiKey/Mouser/LCSC
- Useful for international users where DigiKey/Mouser shipping is expensive
