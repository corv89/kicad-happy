"""Build the ``inputs`` block for analyzer output envelopes.

Thin helper that centralizes SHA-256 computation, run_id generation, and
config-hash resolution so every analyzer builds its ``inputs`` block the
same way.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_id import generate_run_id  # noqa: E402


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of the file at ``path``."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_inputs(
    source_files: Iterable[Path | str],
    *,
    config_path: Path | str | None = None,
    upstream_artifacts: dict | None = None,
    run_id: str | None = None,
) -> dict:
    """Build a dict matching the ``InputsBlock`` envelope shape.

    Parameters
    ----------
    source_files : Iterable[Path | str]
        Paths of every file read from disk for this run. Hashed
        individually.
    config_path : Path | str | None, optional
        Path to the resolved .kicad-happy.json config file, if any.
    upstream_artifacts : dict | None, optional
        Pre-built mapping of stage name -> UpstreamArtifact dict. Passed
        through as-is; callers are responsible for correct shape.
    run_id : str | None, optional
        Explicit run_id; generated via ``run_id.generate_run_id()`` if
        omitted.
    """
    paths = [Path(f) for f in source_files]
    source_hashes = {str(p): _sha256_file(p) for p in paths}
    config_hash = _sha256_file(Path(config_path)) if config_path else None
    return {
        "source_files": [str(p) for p in paths],
        "source_hashes": source_hashes,
        "run_id": run_id or generate_run_id(),
        "config_hash": config_hash,
        "upstream_artifacts": upstream_artifacts or {},
    }
