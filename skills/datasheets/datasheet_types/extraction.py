"""Top-level DatasheetFacts + Source + ExtractionMeta + SchemaVersion.

Mirrors extraction.schema.json. DatasheetFacts is the facade Track 2.3
will return from lookup(mpn). Composes base + zero-or-more category
extensions.

lookup() itself is NOT in this module — Track 2.3 lands that alongside
cache-path resolution + staleness detection. Track 2.2 delivers only
the typed shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .base_block import BaseBlock
from .regulator import Regulator


@dataclass
class SchemaVersion:
    """Split schema versions — base evolves independently of each category."""
    base: str = field(metadata={
        "description": "Base block schema version (MAJOR.MINOR, e.g. '1.0')."})
    categories: dict[str, str] = field(default_factory=dict, metadata={
        "description": "Per-category schema versions, keyed by category name "
                       "(e.g. {'regulator': '0.3'})."})


@dataclass
class Source:
    """Facts about the source PDF — separate from extraction-act facts."""
    manufacturer: str = field(metadata={
        "description": "PDF manufacturer name."})
    mpn: str = field(metadata={
        "description": "Manufacturer part number."})
    sha256: str = field(metadata={
        "description": "PDF SHA-256 with 'sha256:' prefix. Staleness pivot — Track 2.3 "
                       "compares this against the PDF on disk to flag cache staleness."})
    datasheet_revision: Optional[str] = field(default=None, metadata={
        "description": "Datasheet revision label (e.g. 'Rev M')."})
    datasheet_date: Optional[str] = field(default=None, metadata={
        "description": "Datasheet publication date."})
    source_url: Optional[str] = field(default=None, metadata={
        "description": "Origin URL the PDF was downloaded from."})
    local_path: Optional[str] = field(default=None, metadata={
        "description": "Filesystem path relative to datasheets/ (e.g. 'LM2596-ADJ.pdf')."})
    page_count: Optional[int] = field(default=None, metadata={
        "description": "PDF page count."})
    family_ref: Optional[str] = field(default=None, metadata={
        "description": "Path to a _families/ file when this MPN is a variant. Reserved for "
                       "v1.5 Tier 2 dedup; always None in v1.4."})


@dataclass
class ExtractionMeta:
    """Facts about the extraction act itself.

    Separate from Source — a re-extraction updates ExtractionMeta without
    touching Source (unless the PDF itself changed, bumping the sha256).
    """
    extracted_at: str = field(metadata={
        "description": "ISO-8601 UTC timestamp of the extraction run."})
    extractor_schema_version: str = field(metadata={
        "description": "Version of the overall extraction pipeline (MAJOR.MINOR)."})
    extractor_scout: Optional[str] = field(default=None, metadata={
        "description": "Identifier of the scout subagent / model. Opaque string."})
    quality_score: Optional[int] = field(default=None, metadata={
        "description": "Overall extraction quality score (0–100). None when not yet scored."})
    plan_ref: Optional[str] = field(default=None, metadata={
        "description": "Relative path to the orchestration plan JSON."})


@dataclass
class DatasheetFacts:
    """Top-level per-MPN fact envelope.

    Composes SchemaVersion + Source + ExtractionMeta + BaseBlock + zero
    or more category extensions (currently just Regulator; v1.5 adds
    MCU, opamp, diode, transistor, crystal).

    Consumers obtain instances via Track 2.3's lookup(mpn) facade.
    """
    schema_version: SchemaVersion = field(metadata={
        "description": "Per-section schema versions."})
    source: Source = field(metadata={
        "description": "PDF-level facts."})
    extraction: ExtractionMeta = field(metadata={
        "description": "Facts about this extraction run."})
    base: BaseBlock = field(metadata={
        "description": "Universal per-IC facts."})
    categories: list[str] = field(default_factory=list, metadata={
        "description": "List of active category extensions (e.g. ['regulator']). Each entry "
                       "should correspond to a sibling field carrying that category's payload."})
    regulator: Optional[Regulator] = field(default=None, metadata={
        "description": "Regulator category extension. None when 'regulator' not in categories.",
        "omit_if_none": True,
    })
