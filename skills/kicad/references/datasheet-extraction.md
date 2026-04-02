# Structured Datasheet Extraction

Pre-extract datasheet specifications into cached JSON for fast, consistent pin-by-pin verification during design reviews. The extraction captures pin tables, voltage ratings, operating conditions, and application circuit requirements — everything needed for a pin audit without re-reading the full PDF each time.

## When to Extract

Extract datasheets **before a design review** when:
- `datasheets/extracted/` is missing or empty
- Any IC in the design doesn't have a cached extraction
- An extraction is stale (cache manager reports `stale:<reason>`)

For small designs (< 8 ICs), extract all ICs. For large designs, prioritize ICs that appear in signal analysis detections (regulators, opamps, protection ICs, MCUs).

## Workflow

### Step 1: Identify pages to read

Use the page selector to find the relevant pages in each PDF:

```bash
python3 <skill-path>/scripts/datasheet_page_selector.py <pdf_path> --mpn <mpn> --category <category>
```

Or call from Python:
```python
from datasheet_page_selector import suggest_pages
selection = suggest_pages("datasheets/TPS61023DRLR.pdf", "TPS61023DRLR", "switching_regulator")
# selection.pages = [1, 2, 3, 5, 8, 15]
```

If the page selector is not available (no pdftotext), read pages 1-5 plus the application circuit section.

### Step 2: Read the selected PDF pages

Read the selected pages visually (the PDF file, not text extraction). Focus on:
1. **Pin description table** — every pin with its name, type, function, voltage/current limits
2. **Absolute maximum ratings table** — voltage, current, temperature limits
3. **Recommended operating conditions table** — normal operating ranges
4. **Electrical characteristics table** — key specs (category-dependent)
5. **Application circuit / typical application** — reference design, recommended components, formulas

### Step 3: Fill in the extraction JSON

Produce a JSON file following the schema below. Use `null` for any field not available in the datasheet — do not guess or infer values.

### Step 4: Score and cache

Run the scorer:
```python
from datasheet_score import score_extraction
result = score_extraction(extraction, expected_pin_count=6)
# result = {"total": 9.1, "sufficient": True, ...}
```

If `total >= 6.0`, cache the extraction:
```python
from datasheet_extract_cache import cache_extraction, resolve_extract_dir
extract_dir = resolve_extract_dir(project_dir="/path/to/project")
cache_extraction(extract_dir, "TPS61023DRLR", extraction, source_pdf="datasheets/TPS61023DRLR.pdf")
```

If `total < 6.0`, check the `issues` list and retry with more pages or focused attention on the gaps. Maximum 3 retries.

## Extraction JSON Schema

### Top-level fields

| Field | Type | Description |
|-------|------|-------------|
| `mpn` | string | Manufacturer part number (exact, including suffix) |
| `manufacturer` | string | Manufacturer name |
| `category` | string | Component category (see list below) |
| `package` | string | Package name and pin count, e.g. "TSSOP-14 (14-pin)" |
| `description` | string | One-line component description |
| `pins` | array | Per-pin specifications (see below) |
| `absolute_maximum_ratings` | object | Voltage/current/thermal absolute limits |
| `recommended_operating_conditions` | object | Normal operating ranges |
| `electrical_characteristics` | object | Key electrical specs (category-dependent) |
| `application_circuit` | object | Reference design info |
| `spice_specs` | object | SPICE model parameters (uses same keys as spice_part_library.py) |
| `extraction_metadata` | object | Source PDF, pages, score, version |

### Category values

Use these exact strings (matches `_classify_ic_function()` in analyze_schematic.py):

| Category | Examples |
|----------|----------|
| `microcontroller` | STM32, ESP32, ATmega, PIC, RP2040 |
| `operational_amplifier` | LM358, OPA340, AD8605 |
| `comparator` | LM393, TLV3501 |
| `linear_regulator` | AMS1117, LM1117, AP2112 |
| `switching_regulator` | TPS61023, LM2596, MP2307 |
| `voltage_reference` | REF3030, LM4040 |
| `esd_protection` | USBLC6-2SC6, PRTR5V0U2X |
| `adc` | ADS1115, MCP3008 |
| `dac` | MCP4725, DAC8552 |
| `interface` | MAX232, SN65HVD230, CP2102 |
| `memory` | AT24C256, W25Q128 |
| `sensor` | BME280, MPU6050 |
| `led_driver` | TLC5940, WS2812B |
| `motor_driver` | DRV8833, A4988 |
| `power_management` | BQ24074, TPS2113 |
| `fpga` | ICE40, XC7A |
| `rf` | CC1101, SX1276 |
| `audio` | MAX98357, PCM5102 |

### Pin entry schema

Each pin in the `pins` array:

```json
{
  "number": "1",
  "name": "SW",
  "type": "power",
  "direction": "bidirectional",
  "description": "Inductor switch node",
  "voltage_abs_max": 6.0,
  "voltage_operating_min": null,
  "voltage_operating_max": null,
  "current_max_ma": 3600,
  "internal_connection": "Power FET drain",
  "required_external": "Connect to inductor (0.47-2.2uH recommended)",
  "threshold_high_v": null,
  "threshold_low_v": null,
  "has_internal_pullup": null,
  "has_internal_pulldown": null
}
```

**Field details:**

| Field | Type | Description |
|-------|------|-------------|
| `number` | string | Pin number as shown on datasheet ("1", "A1", "EP") |
| `name` | string | Pin name from datasheet |
| `type` | string | One of: `power`, `ground`, `analog`, `digital`, `no_connect`, `bidirectional` |
| `direction` | string | One of: `input`, `output`, `bidirectional`, `passive` |
| `description` | string | Brief functional description from datasheet |
| `voltage_abs_max` | float\|null | Absolute maximum voltage on this pin (V) |
| `voltage_operating_min` | float\|null | Minimum operating voltage (V) |
| `voltage_operating_max` | float\|null | Maximum operating voltage (V) |
| `current_max_ma` | float\|null | Maximum current through this pin (mA) |
| `internal_connection` | string\|null | What this pin connects to internally |
| `required_external` | string\|null | What must be connected externally — the key field for pin audit |
| `threshold_high_v` | float\|null | Logic high threshold (digital inputs) |
| `threshold_low_v` | float\|null | Logic low threshold (digital inputs) |
| `has_internal_pullup` | bool\|null | True if pin has internal pull-up |
| `has_internal_pulldown` | bool\|null | True if pin has internal pull-down |

**The `required_external` field** is the most important for design review. It should describe what the datasheet requires/recommends be connected to this pin. Examples:
- `"Connect to ground plane, place input and output caps close"`
- `"10K pull-up to VCC required"`
- `"Resistor divider from VOUT, Vout = 0.595 * (1 + R1/R2)"`
- `"Do not connect (NC pin)"`
- `"Bypass cap 100nF to GND, place within 3mm"`
- `"Connect to VIN for always-on, or logic control. Do not float."`

### absolute_maximum_ratings

Use keys ending in `_max_v`, `_max_c`, `_max_ma`, `_v` as appropriate:

```json
{
  "vin_max_v": 6.0,
  "vout_max_v": 6.0,
  "io_voltage_max": 4.0,
  "junction_temp_max_c": 150,
  "storage_temp_min_c": -65,
  "storage_temp_max_c": 150,
  "esd_hbm_v": 2000,
  "esd_cdm_v": 500
}
```

### recommended_operating_conditions

```json
{
  "vin_min_v": 0.5,
  "vin_max_v": 5.5,
  "vout_min_v": 1.8,
  "vout_max_v": 5.5,
  "temp_min_c": -40,
  "temp_max_c": 85
}
```

### electrical_characteristics

Category-dependent. Include whatever the datasheet provides:

**Regulators:**
```json
{
  "vref_v": 0.595,
  "vref_accuracy_pct": 1.0,
  "quiescent_current_ua": 12,
  "shutdown_current_ua": 1,
  "switching_frequency_khz": 1200,
  "efficiency_pct": 96,
  "output_current_max_ma": 1000
}
```

**Opamps:**
```json
{
  "gbw_hz": 1000000,
  "slew_vus": 0.3,
  "vos_mv": 2.0,
  "aol_db": 100,
  "rin_ohms": 2000000,
  "cmrr_db": 85
}
```

**MCUs:** include io_voltage_max, quiescent/deep_sleep current, oscillator specs.

### application_circuit

```json
{
  "topology": "boost",
  "inductor_recommended": "1uH, Isat > 3.6A",
  "input_cap_recommended": "10uF ceramic, X5R or X7R",
  "output_cap_recommended": "22uF ceramic x2",
  "vout_formula": "Vout = 0.595 * (1 + R1/R2)",
  "notes": [
    "Place input and output caps close to IC pins",
    "Keep SW trace short and wide to minimize EMI",
    "Route FB sense trace directly to output cap positive terminal",
    "Add 100-220pF feedforward cap across top feedback resistor"
  ]
}
```

### spice_specs

Uses the **exact same key names** as `spice_part_library.py`. This ensures direct consumption by the SPICE model generator without field mapping.

```json
{
  "gbw_hz": null,
  "slew_vus": null,
  "vos_mv": null,
  "aol_db": null,
  "rin_ohms": null,
  "supply_min": 0.5,
  "supply_max": 5.5,
  "rro": false,
  "rri": false,
  "swing_v": null,
  "dropout_mv": null,
  "iq_ua": 12,
  "iout_max_ma": 1000,
  "vref": 0.595
}
```

### extraction_metadata

Filled by the cache manager and scorer — the extractor only needs to provide `source_pdf` and `extracted_from_pages`:

```json
{
  "source_pdf": "TPS61023DRLR_Boost_Converter.pdf",
  "source_pdf_hash": "sha256:...",
  "extracted_from_pages": [1, 2, 3, 5, 8, 15],
  "total_pdf_pages": 24,
  "extraction_date": "2026-03-31T14:30:00Z",
  "extraction_score": 9.1,
  "score_breakdown": {
    "pin_coverage": 10.0,
    "voltage_ratings": 10.0,
    "operating_conditions": 9.0,
    "application_info": 8.0,
    "spice_specs": 8.5
  },
  "extraction_version": 1,
  "retry_count": 0
}
```

## Category-Specific Extraction Guidance

### Switching regulators (TPS61023, LM2596, etc.)
- **Critical:** VREF value and accuracy, feedback divider formula, inductor range, input/output cap requirements
- Pin table is usually small (6-8 pins) — aim for 100% pin coverage
- Application circuit section is the most valuable — extract recommended component values
- EN pin: extract threshold voltages and internal pull-up/down behavior

### Linear regulators (AMS1117, AP2112, etc.)
- **Critical:** Output voltage (fixed variants), dropout voltage, quiescent current, output current max
- Input/output cap requirements with ESR constraints
- Thermal shutdown temperature

### Opamps (LM358, OPA340, etc.)
- **Critical:** GBW, slew rate, input offset voltage, open-loop gain, supply range, rail-to-rail capability
- Output swing from rails (for non-RRO parts)
- Input impedance (FET vs BJT input)

### MCUs (STM32, ESP32, etc.)
- **Critical:** Power pin requirements (VDD/VSS count, per-pin decoupling), boot/strap pin configurations, reset circuit requirements
- Pin table will be large (48-144+ pins) — focus on power pins, boot pins, and commonly-used peripherals
- GPIO voltage levels and drive strength
- Crystal/oscillator requirements

### ESD protection (USBLC6-2SC6, etc.)
- **Critical:** Pin-to-pin mapping (which I/O protects which line), clamping voltage, parasitic capacitance
- Correct wiring is essential — swapped I/O pins provide no protection

### Comparators (LM393, TLV3201, etc.)
- **Critical:** Propagation delay (tPD), input offset voltage, supply range, output type (push-pull vs open-drain)
- For open-drain outputs: maximum sink current, required pull-up resistor value
- Input common-mode range — determines usable voltage range for comparison
- Hysteresis: internal (if any) and external resistor recommendations

### MOSFETs (BSS138, IRF540, etc.)
- **Critical:** VGS threshold range, RDS(on), maximum VDS, maximum ID, gate charge
- For level shifters: VGS(th) determines minimum logic level
- For power switches: RDS(on) and thermal limits

## Using Extractions During Design Review

When performing a design review with pre-extracted datasheet specs:

1. **Load extractions** for all ICs in the design from `datasheets/extracted/`
2. **Load ic_pin_analysis** from the schematic analyzer JSON
3. **Cross-reference** each IC:
   - Join on `pin_number` between analyzer's pin data and extraction's pin data
   - For each pin: compare what IS connected (analyzer) against what SHOULD be connected (extraction's `required_external`)
   - Check voltage compatibility: is the net voltage within the pin's operating range?
   - Check power pins: does every VDD pin have a decoupling cap?
   - Check digital inputs: are thresholds met? Are pull-ups/downs present where required?
4. **Report findings** with extraction data as evidence — cite specific datasheet values

The extraction's `required_external` field is the primary driver for "is this correct?" judgments. If it says "10K pull-up to VCC required" and the analyzer shows no resistor on that net, that's a finding.

## Scoring Rubric

The scorer (`datasheet_score.py`) evaluates five dimensions:

| Dimension | Weight | What 10.0 means |
|-----------|--------|------------------|
| Pin coverage | 35% | All pins present with name, type, and at least one electrical spec or description |
| Voltage ratings | 25% | Abs max has voltage limits + junction temp; operating has voltage + temp ranges |
| Application info | 20% | Has topology + 2+ component recommendations + formula or notes |
| Electrical characteristics | 10% | Category-dependent required specs present |
| SPICE specs | 10% | Enough fields for behavioral model generation |

**Thresholds:**
- `>= 8.0` — Excellent extraction, high confidence for pin audit
- `>= 6.0` — Sufficient for design review, may lack some optional specs
- `< 6.0` — Retry with more pages or focused attention on gaps (check `issues` list)

Maximum 3 retry attempts. Keep the highest-scoring extraction.
