# Consumer API Reference

How to consume structured datasheet extractions in analyzer code. Covers the `datasheet_features.py` helper (Task 1.2), the raw cache access functions from `datasheet_extract_cache.py`, and the skip-with-INFO pattern for detectors that require extraction data.

---

## datasheet_features.py (Task 1.2)

This module provides typed accessors that abstract cache lookup, null safety, and field path traversal. The functions are forward-referenced here; they are implemented in Task 1.2.

### `get_regulator_features(mpn, extract_dir=None) -> dict | None`

Returns a dict of regulator-relevant fields from the extraction, or `None` if no extraction is available.

```python
from datasheet_features import get_regulator_features

feat = get_regulator_features('TPS61023DRLR', extract_dir)
# Returns:
# {
#     'topology': 'boost',
#     'has_pg': False,           # None if unknown
#     'vref_v': 0.595,
#     'en_v_ih_max': 0.96,       # None if not in extraction
#     'en_v_il_min': 0.4,        # None if not in extraction
#     'iss_time_us': None,
#     'switching_frequency_khz': 1200,
# }
# or None if no extraction exists for this MPN
```

Fields returned: `topology` (from `application_circuit.topology`), `has_pg` (derived from pin names or features), `vref_v` (from `electrical_characteristics.vref_v`), `en_v_ih_max` / `en_v_il_min` (from EN pin `threshold_high_v` / `threshold_low_v`), `iss_time_us` (soft-start time, if present), `switching_frequency_khz`.

### `get_mcu_features(mpn, extract_dir=None) -> dict | None`

Returns MCU-relevant fields, or `None` if no extraction is available.

```python
from datasheet_features import get_mcu_features

mcu = get_mcu_features('ESP32-S3', extract_dir)
# Returns:
# {
#     'usb_speed': 'FS',          # 'FS' | 'HS' | None
#     'has_native_usb_phy': True, # None if unknown
#     'usb_series_r_required': True,
# }
# or None if no extraction exists
```

Fields returned: `usb_speed` (from `peripherals.usb.speed`), `has_native_usb_phy` (from `peripherals.usb.native_phy`), `usb_series_r_required` (from `peripherals.usb.series_r_required`).

### `get_pin_function(mpn, pin_name, extract_dir=None) -> str | None`

Returns the `type` field from the pin entry matching `pin_name`, or `None` if not found.

```python
from datasheet_features import get_pin_function

fn = get_pin_function('TPS61023DRLR', 'EN', extract_dir)
# Returns: 'digital' (the pin's type field)
# or None if extraction missing or pin not found
```

---

## None Means "Unknown — Don't Fire"

The contract for all helper functions: `None` means the extraction data is unavailable, not that the feature is absent. Detectors must treat `None` as "skip" rather than "feature not present".

| Return value | Meaning | Detector action |
|-------------|---------|----------------|
| `None` | No extraction, or field not in extraction | Skip the check; emit INFO |
| `False` | Extraction present; field explicitly false | Fire the relevant check |
| `0` / `0.0` | Extraction present; field is zero | Treat as numeric zero |

This distinction matters for boolean fields like `has_pg` and `has_native_usb_phy`. A `None` return means you cannot determine whether the feature exists. A `False` return means the extraction was found and says the feature is absent.

---

## Skip Pattern for Detectors

When a detector needs extraction data and none is available, emit an INFO-level finding and return — do not fire the rule.

```python
feat = get_regulator_features(mpn, extract_dir)
if feat is None:
    findings.append({
        "severity": "INFO",
        "rule_id": "SS-004",
        "summary": (
            f"Check SS-004 skipped for {ref}: no datasheet extraction for {mpn}. "
            f"Run sync_datasheets to download and extract."
        ),
        "detector": "audit_soft_start",
    })
    return findings
```

Format for the skip message:
```
Check <rule_id> skipped for <ref>: no datasheet extraction for <mpn>. Run sync_datasheets to download and extract.
```

Do not emit a warning or error for a missing extraction — INFO is the correct severity. The extraction is optional; its absence means the check cannot run, not that there is a design problem.

---

## Direct Cache Access

For cases where the helper functions do not cover the needed field, use the cache functions directly.

### `get_cached_extraction(extract_dir, mpn) -> dict | None`

Returns the full extraction dict, or `None` if not cached.

```python
from datasheet_extract_cache import get_cached_extraction, resolve_extract_dir

extract_dir = resolve_extract_dir(analysis_json=analysis)
extraction = get_cached_extraction(extract_dir, mpn)
if extraction is None:
    # no cached data — skip checks that need it
    pass
```

### `resolve_extract_dir(analysis_json=None, project_dir=None, override_dir=None) -> Path`

Resolves the `datasheets/extracted/` directory for a project:

1. If `override_dir` is set, use it directly.
2. If `project_dir` is set, use `<project_dir>/datasheets/extracted/`.
3. If `analysis_json` is provided, use the `"file"` field to find the project root.
4. Fallback: system temp directory.

```python
extract_dir = resolve_extract_dir(project_dir="/path/to/project")
```

### `get_extraction_for_review(mpn, extract_dir, datasheets_dir=None) -> (dict | None, str)`

High-level function that checks freshness. Returns `(extraction_dict, status)` where status is one of:
- `"cached"` — fresh, use it
- `"stale:<reason>"` — exists but stale; extraction is returned but may need refresh
- `"missing"` — no data for this MPN

```python
extraction, status = get_extraction_for_review(mpn, extract_dir, datasheets_dir)
if status == "missing":
    # skip
elif status.startswith("stale"):
    # use extraction but note it may be outdated
else:
    # "cached" — use normally
```

---

## Resolving extract_dir in Detectors

Detectors called from `analyze_schematic.py` receive an `AnalysisContext` object. The extraction directory is resolved once at the top of the analysis run and passed through context.

```python
# In analyze_schematic.py (caller side)
from datasheet_extract_cache import resolve_extract_dir
ctx.extract_dir = resolve_extract_dir(analysis_json=analysis_data)

# In a detector (receiver side)
extract_dir = ctx.extract_dir  # Path or None
if not extract_dir or not extract_dir.exists():
    return []  # skip silently — no extraction infrastructure set up
```

If `extract_dir` does not exist, skip silently (no INFO finding) — this is the normal state for projects that have not run the extraction pipeline.

---

## Schema Field Paths

Reference for navigating the extraction JSON to specific fields used by detectors:

| Detector intent | Extraction path |
|----------------|----------------|
| EN pin threshold high | `pins[name=="EN"].threshold_high_v` |
| EN pin threshold low | `pins[name=="EN"].threshold_low_v` |
| Soft-start time | `electrical_characteristics.iss_time_us` or similar |
| Topology | `application_circuit.topology` |
| Reference voltage | `electrical_characteristics.vref_v` |
| Switching frequency | `electrical_characteristics.switching_frequency_khz` |
| Input cap recommendation | `application_circuit.input_cap_recommended` |
| Output cap recommendation | `application_circuit.output_cap_recommended` |
| Decoupling recommendation | `application_circuit.decoupling_cap` |
| USB speed | `peripherals.usb.speed` |
| USB native PHY | `peripherals.usb.native_phy` |
| USB series R | `peripherals.usb.series_r_required` |
| Power-good pin present | Look for pin name matching "PG", "PGOOD", "POWER_GOOD" |

Note: `peripherals.usb.*` fields are schema v2 additions and will be null in extractions produced under `EXTRACTION_VERSION = 1`. When `EXTRACTION_VERSION` is bumped to 2, these fields trigger re-extraction.

---

## Trust Gate in datasheet_verify.py

The verification bridge (`datasheet_verify.py`) applies an extraction quality gate before using any extraction: if `extraction_metadata.extraction_score < 6.0`, the extraction is discarded and the component is skipped. This avoids false positives from low-quality extractions.

Detectors that use `get_regulator_features()` or `get_mcu_features()` should apply the same gate internally, or rely on the helper functions to apply it transparently. Do not use an extraction whose score is below `MIN_SCORE` (6.0) to drive HIGH or CRITICAL findings.
