"""Consumer helper API for datasheet extractions.

Thin wrapper over datasheet_extract_cache. Provides field-level accessors for
IC-aware detectors in kicad, emc, spice, and thermal skills.

Contract:
  - Returns a dict of feature fields on cache hit with sufficient score.
  - Returns None on cache miss, stale entry, low score, or wrong schema version.
  - Individual fields within the dict may be None (datasheet didn't specify).
  - Consumers MUST distinguish None (unknown) from False (explicitly no).

Zero external dependencies — stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from datasheet_extract_cache import (
    resolve_extract_dir,
    get_cached_extraction,
    EXTRACTION_VERSION,
    MIN_SCORE,
)

# Add datasheet_types to sys.path for direct import of DatasheetFacts etc.
# This module lives at skills/datasheets/scripts/; the types package is a
# sibling under skills/datasheets/datasheet_types/.
_TYPES_PARENT = str(Path(__file__).resolve().parent.parent)
if _TYPES_PARENT not in sys.path:
    sys.path.insert(0, _TYPES_PARENT)

from datasheet_types.extraction import DatasheetFacts  # noqa: E402


_REGULATOR_TOPOLOGIES = ('boost', 'buck', 'ldo')
_MCU_TOPOLOGIES = ('mcu',)

# v1.3 pin-function name → v1.4 Pin.name patterns. Used by the derivation
# helper to map a pin to its v1.3 functional category.
_PIN_NAME_TO_FUNCTION: dict[str, str] = {
    "VIN": "VIN",
    "VIN+": "VIN",
    "OUT": "VOUT",
    "VOUT": "VOUT",
    "VOUT+": "VOUT",
    "GND": "GND",
    "VSS": "GND",
    "AGND": "GND",
    "DGND": "GND",
}


def _load(mpn, extract_dir=None, analysis_json=None, project_dir=None):
    """Resolve extract dir and load the cached extraction for mpn.

    Returns the extraction dict only if:
      - The entry exists in cache
      - extraction_metadata.extraction_version >= EXTRACTION_VERSION
      - extraction_metadata.score >= MIN_SCORE

    Returns None otherwise.
    """
    if extract_dir is None:
        extract_dir = resolve_extract_dir(
            analysis_json=analysis_json, project_dir=project_dir
        )
    ext = get_cached_extraction(extract_dir, mpn)
    if not ext:
        return None
    meta = ext.get('extraction_metadata') or {}
    if (meta.get('extraction_version') or 0) < EXTRACTION_VERSION:
        return None
    if (meta.get('extraction_score') or 0) < MIN_SCORE:
        return None
    return ext


def _pin_with_function(pins, target_function):
    """Return the first pin whose function matches target_function, or None."""
    for pin in pins or []:
        if pin.get('function') == target_function:
            return pin
    return None


# ---------------------------------------------------------------------------
# v1.4 derivation helpers — translate DatasheetFacts → v1.3 dict shape.
# Used by the public wrappers in Task 2. Pure functions, no filesystem.
# ---------------------------------------------------------------------------

def _derive_regulator_features_v14(facts: DatasheetFacts) -> Optional[dict]:
    """Translate a v1.4 DatasheetFacts into a v1.3 regulator-features dict.

    Returns None when facts has no regulator category (v1.4 MVP scope is
    regulator only; MCU / opamp / etc. land in v1.5).

    Fields derived from v1.4:
      topology:  facts.regulator.topology (already v1.3-compatible enum
                 for ldo/buck/boost; other topologies pass through verbatim,
                 so a v1.3 detector checking `topology in ('boost','buck','ldo')`
                 gets the same behavior it always had).
      has_pg:    True iff facts.regulator.power_good_pin is not None.
      en_pin:    facts.regulator.enable_pin.
      pg_pin:    facts.regulator.power_good_pin.
      vin_pin:   pin number of the Pin named VIN (or VIN+) in base.pinout.
      vout_pin:  pin number of the Pin named OUT / VOUT / VOUT+.

    Fields with no v1.4 schema v1.0 equivalent (has_soft_start, iss_time_us,
    en_v_ih_max, en_v_il_min) return None. v1.3 contract explicitly allows
    this: None means "datasheet didn't specify."
    """
    if facts.regulator is None:
        return None

    topo = facts.regulator.topology

    # Find VIN / VOUT pins by name via the Pinout wrapper (Track 2.2).
    vin_pin_obj = facts.base.pinout.find(name="VIN")
    if vin_pin_obj is None:
        vin_pin_obj = facts.base.pinout.find(name="VIN+")
    vout_pin_obj = facts.base.pinout.find(name="OUT")
    if vout_pin_obj is None:
        vout_pin_obj = facts.base.pinout.find(name="VOUT")
    if vout_pin_obj is None:
        vout_pin_obj = facts.base.pinout.find(name="VOUT+")

    def _first_number(pin) -> Optional[str]:
        return pin.numbers[0] if pin is not None and pin.numbers else None

    return {
        'topology': topo,
        'has_pg': facts.regulator.power_good_pin is not None,
        'has_soft_start': None,      # No v1.4 equivalent.
        'iss_time_us': None,         # No v1.4 equivalent.
        'en_v_ih_max': None,         # No v1.4 equivalent (per-pin VIH/VIL not in schema v1.0).
        'en_v_il_min': None,
        'vin_pin': _first_number(vin_pin_obj),
        'vout_pin': _first_number(vout_pin_obj),
        'en_pin': facts.regulator.enable_pin,
        'pg_pin': facts.regulator.power_good_pin,
    }


def _derive_mcu_features_v14(facts: DatasheetFacts) -> Optional[dict]:
    """Translate a v1.4 DatasheetFacts into a v1.3 mcu-features dict.

    v1.4 MVP has no mcu category — this always returns None. The public
    get_mcu_features wrapper falls through to the v1.3 cache read path
    when this returns None.

    v1.5 adds the mcu category extension; this function will grow real
    derivation logic then.
    """
    # categories is a list[str]; 'mcu' is not in v1.4 MVP scope.
    return None


def _derive_pin_function_v14(facts: DatasheetFacts, pin_id: str) -> Optional[str]:
    """Translate a pin identifier → v1.3-style function string.

    Derivation order:
      1. Check regulator pin refs (enable_pin → 'EN', power_good_pin → 'PG',
         feedback_pin → 'FB') — these give definitive function hits for
         regulator parts.
      2. Find the Pin in base.pinout by number (exact) or name (case-insensitive).
         Map Pin.name → v1.3 function via _PIN_NAME_TO_FUNCTION.
      3. Fall back to None if no match.

    Returns None when the pin_id does not resolve to any known pin, OR
    when the resolved pin's name is not in _PIN_NAME_TO_FUNCTION (unknown
    function category — caller gets the same None signal v1.3 gave for
    unmapped pins).
    """
    target = str(pin_id).strip()

    # 1. Regulator pin refs (strongest signal for regulator parts).
    if facts.regulator is not None:
        if facts.regulator.enable_pin == target:
            return "EN"
        if facts.regulator.power_good_pin == target:
            return "PG"
        if facts.regulator.feedback_pin == target:
            return "FB"

    # 2. Pinout name-based map.
    pin = facts.base.pinout.find(pin=target)
    if pin is None:
        pin = facts.base.pinout.find(name=target)
    if pin is None:
        return None

    return _PIN_NAME_TO_FUNCTION.get(pin.name)


def get_regulator_features(mpn, *, extract_dir=None,
                            analysis_json=None, project_dir=None) -> Optional[dict]:
    """Return regulator-specific features for mpn, or None if not available.

    Returns None when:
      - No extraction is cached for the MPN
      - Extraction is stale (below EXTRACTION_VERSION)
      - Extraction score is below MIN_SCORE
      - Extraction topology is not one of: 'boost', 'buck', 'ldo'

    Returned dict fields (any may be None individually):
      topology:          'boost' | 'buck' | 'ldo'
      has_pg:            bool | None
      has_soft_start:    bool | None
      iss_time_us:       float | None
      en_v_ih_max:       float (V) | None  — from EN pin's threshold_high_v
      en_v_il_min:       float (V) | None  — from EN pin's threshold_low_v
      vin_pin:           str | None        — pin number of the VIN pin
      vout_pin:          str | None
      en_pin:            str | None
      pg_pin:            str | None
    """
    ext = _load(mpn, extract_dir=extract_dir,
                analysis_json=analysis_json, project_dir=project_dir)
    if not ext:
        return None
    topo = ext.get('topology')
    if topo not in _REGULATOR_TOPOLOGIES:
        return None
    pins = ext.get('pins') or []
    features = ext.get('features') or {}
    en_pin = _pin_with_function(pins, 'EN')
    vin_pin = _pin_with_function(pins, 'VIN')
    vout_pin = _pin_with_function(pins, 'VOUT')
    pg_pin = _pin_with_function(pins, 'PG')

    def _pin_number(p):
        if not p:
            return None
        n = p.get('number')
        return str(n) if n is not None else p.get('name')

    return {
        'topology': topo,
        'has_pg': features.get('has_pg'),
        'has_soft_start': features.get('has_soft_start'),
        'iss_time_us': features.get('iss_time_us'),
        'en_v_ih_max': (en_pin or {}).get('threshold_high_v'),
        'en_v_il_min': (en_pin or {}).get('threshold_low_v'),
        'vin_pin': _pin_number(vin_pin),
        'vout_pin': _pin_number(vout_pin),
        'en_pin': _pin_number(en_pin),
        'pg_pin': _pin_number(pg_pin),
    }


def get_mcu_features(mpn, *, extract_dir=None,
                     analysis_json=None, project_dir=None) -> Optional[dict]:
    """Return MCU-specific features for mpn, or None if not available.

    Returns None when:
      - No extraction is cached for the MPN
      - Extraction is stale or below MIN_SCORE
      - Extraction topology is not 'mcu'

    Returned dict fields (any may be None individually):
      usb_speed:              'FS' | 'HS' | 'SS' | None
      has_native_usb_phy:     bool | None
      usb_series_r_required:  bool | None
    """
    ext = _load(mpn, extract_dir=extract_dir,
                analysis_json=analysis_json, project_dir=project_dir)
    if not ext:
        return None
    if ext.get('topology') not in _MCU_TOPOLOGIES:
        return None
    peripherals = ext.get('peripherals') or {}
    usb = peripherals.get('usb') or {}
    return {
        'usb_speed': usb.get('speed'),
        'has_native_usb_phy': usb.get('native_phy'),
        'usb_series_r_required': usb.get('series_r_required'),
    }


def get_pin_function(mpn, pin_identifier, *, extract_dir=None,
                      analysis_json=None, project_dir=None) -> Optional[str]:
    """Return the functional category of a pin ('EN', 'VIN', etc.), or None.

    `pin_identifier` matches against pins[].number (exact) OR pins[].name
    (case-insensitive).
    """
    ext = _load(mpn, extract_dir=extract_dir,
                analysis_json=analysis_json, project_dir=project_dir)
    if not ext:
        return None
    target = str(pin_identifier).strip()
    target_lower = target.lower()
    for p in ext.get('pins') or []:
        if str(p.get('number', '')).strip() == target:
            return p.get('function')
        if str(p.get('name', '')).strip().lower() == target_lower:
            return p.get('function')
    return None


def is_extraction_available(mpn, *, extract_dir=None,
                             analysis_json=None, project_dir=None) -> bool:
    """True iff a v2+, sufficiently-scored extraction exists for mpn."""
    return _load(mpn, extract_dir=extract_dir,
                 analysis_json=analysis_json, project_dir=project_dir) is not None
