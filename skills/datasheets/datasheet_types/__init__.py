"""Typed Python access layer for datasheet v2 extraction.

Mirrors the Track 2.1 JSON schemas under skills/datasheets/schemas/.
Dataclasses are ergonomic access; the JSON Schema remains source of truth.

Public API:
    DatasheetFacts — top-level per-MPN fact envelope
    SpecValue, Evidence — atomic primitives
    Pin, AltFunction, Pinout — pinout types
    BaseBlock, Package, ComplianceMark, PinRelationship — base block types
    Regulator, StabilityConditions, Sequencing — regulator category

Consumers import:
    from datasheet_types.extraction import DatasheetFacts
    from datasheet_types.codec import from_dict, to_dict
    facts = from_dict(DatasheetFacts, json.load(open('cache/LM2596-ADJ.json')))

lookup() (resolving an MPN to a DatasheetFacts) and trust-gating
helpers (.best(), .trusted()) are NOT in this package — they land in
Tracks 2.3 and 2.4 respectively.
"""
from .spec_value import SpecValue, Evidence
from .pinout import Pin, AltFunction, Pinout
from .base_block import (
    BaseBlock,
    Package,
    BodyMm,
    MoistureSensitivity,
    ComplianceMark,
    PinRelationship,
)
from .regulator import Regulator, StabilityConditions, Sequencing
from .extraction import DatasheetFacts, Source, ExtractionMeta, SchemaVersion

__all__ = [
    "DatasheetFacts",
    "SchemaVersion",
    "Source",
    "ExtractionMeta",
    "BaseBlock",
    "Package",
    "BodyMm",
    "MoistureSensitivity",
    "ComplianceMark",
    "PinRelationship",
    "Pinout",
    "Pin",
    "AltFunction",
    "SpecValue",
    "Evidence",
    "Regulator",
    "StabilityConditions",
    "Sequencing",
]
