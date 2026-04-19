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

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# datasheet_types is a sibling package under skills/datasheets/.
# The consumer is expected to add skills/datasheets/ to sys.path so
# `import datasheet_types` resolves — but in case this module is loaded
# directly (e.g. from scripts dir only), add the parent dir once.
_TYPES_PARENT = str(Path(__file__).resolve().parent.parent)
if _TYPES_PARENT not in sys.path:
    sys.path.insert(0, _TYPES_PARENT)

from datasheet_types.codec import from_dict  # noqa: E402
from datasheet_types.extraction import DatasheetFacts  # noqa: E402


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


# ---------------------------------------------------------------------------
# lookup — the consumer API entry point
# ---------------------------------------------------------------------------


def lookup(mpn: str, *, cache_dir: Path) -> Optional[DatasheetFacts]:
    """Read-only MPN → DatasheetFacts lookup.

    Resolves `cache_dir / <sanitize_mpn(mpn)>.json`, parses it into a
    typed DatasheetFacts via the Track 2.2 codec, attaches a CacheContext
    (staleness detection comes in Task 4), and returns the instance.

    Returns None when:
        - cache_dir does not exist
        - cache file for this MPN does not exist
        - cache file contains malformed JSON
        - cache file is JSON but does not satisfy DatasheetFacts shape
          (required fields missing)

    Per spec §11: pure read — never writes, never extracts, never
    triggers an LLM call. On cache miss, caller is responsible for
    triggering `datasheets sync` or equivalent.

    Args:
        mpn: Manufacturer part number. Sanitized for filename lookup.
        cache_dir: Path to the datasheets/extracted/ directory.

    Returns:
        A DatasheetFacts instance with attached _cache_context, or None.
    """
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir():
        return None

    cache_file = cache_path_for(mpn, cache_dir)
    if not cache_file.is_file():
        return None

    try:
        data = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    try:
        facts = from_dict(DatasheetFacts, data)
    except (KeyError, TypeError, ValueError):
        # Missing required fields, wrong shape, invalid unit string, etc.
        return None

    # Attach cache context. Staleness detection lands in Task 4 —
    # for now is_stale stays at its CacheContext default (False).
    facts._cache_context = CacheContext(cache_path=cache_file)
    return facts
