"""Consumer API entry point — lookup(mpn) -> DatasheetFacts | None.

Track 2.3 of the v1.4 datasheet extraction work. Pure-read: lookup()
never writes, extracts, or triggers LLM calls (spec §11 Rules 1 & 2).
On cache miss, malformed JSON, or any error, lookup() returns None.

Consumers import:
    from datasheet_lookup import lookup
    from datasheet_types import DatasheetFacts

    ds = lookup("LM2596-ADJ", cache_dir=Path("/path/to/datasheets/extracted"))
    if ds is None:
        ...  # no cache for this MPN
    if ds.stale:
        ...  # PDF on disk has changed since extraction
    if ds.quality is not None and ds.quality < 60:
        ...  # low-confidence extraction
    # Typed access via Track 2.2:
    ds.base.pinout.find(name="EN")
    ds.regulator.topology  # if "regulator" in ds.categories
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# MPN sanitization + cache-path resolution
# ---------------------------------------------------------------------------

# Allowed characters in the sanitized filename component.
# Matches [A-Za-z0-9_-]; everything else is replaced with _.
# Simpler than v1.3's scheme (which appended an MD5 suffix to avoid
# collisions) — the MPN character set is narrow enough in practice.
_UNSAFE_CHAR = re.compile(r"[^A-Za-z0-9_\-]")


def sanitize_mpn(mpn: str) -> str:
    """Convert an MPN to a filename-safe component.

    Strips whitespace; replaces any non-[A-Za-z0-9_-] character with '_'.
    No hash suffix — rare collisions are acceptable for v1.4.

    Examples:
        sanitize_mpn("LM2596-ADJ")   -> "LM2596-ADJ"
        sanitize_mpn("STM32/F103")   -> "STM32_F103"
        sanitize_mpn(" ACME 1234 ")  -> "ACME_1234"
    """
    return _UNSAFE_CHAR.sub("_", mpn.strip())


def cache_path_for(mpn: str, cache_dir: Path) -> Path:
    """Return the cache-file path for an MPN.

    Composes <cache_dir>/<sanitize_mpn(mpn)>.json. Does not check
    existence; does not resolve symlinks.
    """
    return Path(cache_dir) / f"{sanitize_mpn(mpn)}.json"


# ---------------------------------------------------------------------------
# Cache context (attached to DatasheetFacts by lookup())
# ---------------------------------------------------------------------------

@dataclass
class CacheContext:
    """Operational metadata attached to a DatasheetFacts by lookup().

    Not a dataclass field on DatasheetFacts itself — DatasheetFacts.stale
    and DatasheetFacts.cache_path properties read this via getattr on an
    instance attribute. Absent for DatasheetFacts constructed outside
    lookup() (Track 2.2 round-trip tests); properties default safely.
    """
    cache_path: Path
    pdf_path: Optional[Path] = None
    is_stale: bool = False
    stale_reason: Optional[str] = None  # "pdf_hash_mismatch" | "pdf_missing" | None
