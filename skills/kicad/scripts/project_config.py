"""
Project configuration and suppression matching for kicad-happy.

Loads .kicad-happy.json (JSONC with comment stripping) with cascading
config: files found closer to the project override those farther away,
and ~/.kicad-happy.json serves as a user-level base layer.

Merge rules:
  - Dicts: deep-merged recursively, closer keys win
  - "suppressions": concatenated across all layers (additive)
  - Other lists: closer layer wins entirely

Provides suppression matching with fnmatch globs and risk-scoring
utilities shared across all analyzers.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import re
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# JSONC loader (JSON with // and /* */ comments, trailing commas)
# ---------------------------------------------------------------------------

_LINE_COMMENT = re.compile(r'//.*?$', re.MULTILINE)
_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)
_TRAILING_COMMA = re.compile(r',\s*([}\]])')


def _strip_jsonc(text: str) -> str:
    """Strip JS-style comments and trailing commas from JSON text."""
    text = _BLOCK_COMMENT.sub('', text)
    text = _LINE_COMMENT.sub('', text)
    text = _TRAILING_COMMA.sub(r'\1', text)
    return text


def load_jsonc(path: str) -> dict:
    """Load a JSONC file, returning parsed dict."""
    with open(path, 'r', encoding='utf-8') as f:
        raw = f.read()
    return json.loads(_strip_jsonc(raw))


# ---------------------------------------------------------------------------
# Config discovery and loading
# ---------------------------------------------------------------------------

CONFIG_FILENAME = '.kicad-happy.json'

# Recognized values for validated fields
VALID_MARKETS = {'us', 'eu', 'automotive', 'medical', 'military'}
VALID_DERATING_PROFILES = {'hobby', 'commercial', 'conservative', 'automotive'}
VALID_BOARD_CLASSES = {'class_1', 'class_2', 'class_3'}

# Top-level keys whose list values are concatenated across layers
# instead of replaced.  All other lists use closer-wins semantics.
_ADDITIVE_KEYS = {'suppressions'}

# Default project config (used when no file found)
DEFAULT_CONFIG: Dict[str, Any] = {
    'version': 1,
    'project': {},
    'suppressions': [],
}


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any],
                _path: str = '') -> Dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    - Dict values are merged recursively (override keys win on conflict).
    - List values under keys in _ADDITIVE_KEYS are concatenated
      (base items first, then override items).
    - All other values (including non-additive lists) from *override*
      replace the corresponding *base* value entirely.
    """
    merged: Dict[str, Any] = {}
    all_keys = set(base) | set(override)
    for key in all_keys:
        full_key = f'{_path}.{key}' if _path else key
        if key in override and key in base:
            bval = base[key]
            oval = override[key]
            if isinstance(bval, dict) and isinstance(oval, dict):
                merged[key] = _deep_merge(bval, oval, full_key)
            elif isinstance(bval, list) and isinstance(oval, list) \
                    and key in _ADDITIVE_KEYS:
                merged[key] = bval + oval
            else:
                merged[key] = oval
        elif key in override:
            merged[key] = override[key]
        else:
            merged[key] = base[key]
    return merged


# ---------------------------------------------------------------------------
# Discovery and cascading load
# ---------------------------------------------------------------------------

def _discover_config_paths(search_dir: str) -> List[str]:
    """Walk upward from *search_dir* collecting all .kicad-happy.json paths.

    Returns paths ordered from farthest (most general) to closest
    (most specific).  Also includes ~/.kicad-happy.json as the base
    layer if it exists and was not already found during the walk.
    """
    found: List[str] = []
    seen: set = set()
    d = os.path.abspath(search_dir)
    for _ in range(50):  # depth limit
        candidate = os.path.join(d, CONFIG_FILENAME)
        real = os.path.realpath(candidate)
        if os.path.isfile(candidate) and real not in seen:
            found.append(candidate)
            seen.add(real)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent

    # Check ~/.kicad-happy.json as a user-level base layer
    home_cfg = os.path.join(os.path.expanduser('~'), CONFIG_FILENAME)
    if os.path.isfile(home_cfg) and os.path.realpath(home_cfg) not in seen:
        found.append(home_cfg)

    # Reverse: farthest first so closer layers override during merge
    found.reverse()
    return found


def load_config(search_dir: str) -> Dict[str, Any]:
    """Discover and merge all .kicad-happy.json files from *search_dir* upward.

    Config files are merged with cascading precedence: files closer to
    the project directory override those farther away.  The user-level
    ~/.kicad-happy.json is the base layer (lowest precedence).

    Merge rules:
      - Dict values: deep-merged recursively, closer keys win.
      - "suppressions": concatenated across all layers (additive).
      - Other lists: closer layer wins entirely.

    Returns the merged config dict, or DEFAULT_CONFIG if no files found.
    Prints warnings to stderr on parse errors (those files are skipped).
    """
    paths = _discover_config_paths(search_dir)
    if not paths:
        return dict(DEFAULT_CONFIG)

    merged = dict(DEFAULT_CONFIG)
    for path in paths:
        layer = _load_and_validate(path)
        if layer is not None:
            merged = _deep_merge(merged, layer)

    return merged


def load_config_from_path(path: str) -> Dict[str, Any]:
    """Load config from an explicit file path (for --config CLI arg).

    No cascading — loads only the specified file.
    """
    if not path or not os.path.isfile(path):
        return dict(DEFAULT_CONFIG)
    cfg = _load_and_validate(path)
    return cfg if cfg is not None else dict(DEFAULT_CONFIG)


def _load_and_validate(path: str) -> Optional[Dict[str, Any]]:
    """Load, validate, and return config from *path*.

    Returns None on parse errors (caller should skip this layer).
    """
    import sys
    try:
        cfg = load_jsonc(path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f'Warning: failed to parse {path}: {exc}', file=sys.stderr)
        return None

    if not isinstance(cfg, dict):
        print(f'Warning: {path} root must be an object', file=sys.stderr)
        return None

    # Validate suppressions if present
    raw_suppressions = cfg.get('suppressions')
    if raw_suppressions is not None:
        valid_suppressions = []
        for i, s in enumerate(raw_suppressions):
            if not isinstance(s, dict):
                print(f'Warning: {path}: suppressions[{i}] is not an object, '
                      f'skipping', file=sys.stderr)
                continue
            if 'rule_id' not in s:
                print(f'Warning: {path}: suppressions[{i}] missing required '
                      f'"rule_id", skipping', file=sys.stderr)
                continue
            valid_suppressions.append(s)
        cfg['suppressions'] = valid_suppressions

    return cfg


# ---------------------------------------------------------------------------
# Suppression matching
# ---------------------------------------------------------------------------

def matches_suppression(finding: Dict[str, Any],
                        suppression: Dict[str, Any]) -> bool:
    """Check whether *finding* matches a *suppression* entry.

    Matching rules:
    - rule_id: must match exactly (required).
    - components: if present, at least one finding component must match
      at least one suppression component pattern (fnmatch globs).
    - nets: if present, at least one finding net must match at least one
      suppression net pattern (fnmatch globs).
    """
    # rule_id must match
    if finding.get('rule_id', '') != suppression.get('rule_id', ''):
        return False

    # Component match (optional filter)
    sup_components = suppression.get('components')
    if sup_components:
        finding_components = finding.get('components', [])
        if not finding_components:
            return False
        if not any(fnmatch(fc, sp) for fc in finding_components
                   for sp in sup_components):
            return False

    # Net match (optional filter)
    sup_nets = suppression.get('nets')
    if sup_nets:
        finding_nets = finding.get('nets', [])
        if not finding_nets:
            return False
        if not any(fnmatch(fn, sp) for fn in finding_nets
                   for sp in sup_nets):
            return False

    return True


def apply_suppressions(findings: List[Dict[str, Any]],
                       suppressions: List[Dict[str, Any]],
                       ) -> List[Dict[str, Any]]:
    """Mark findings that match any suppression entry.

    Adds to each finding:
    - "suppressed": bool
    - "suppression_reason": str (reason from matching suppression, or "")

    Findings are never removed — only marked. Returns the same list.
    """
    if not suppressions:
        for f in findings:
            f.setdefault('suppressed', False)
            f.setdefault('suppression_reason', '')
        return findings

    for f in findings:
        matched = False
        reason = ''
        for s in suppressions:
            if matches_suppression(f, s):
                matched = True
                reason = s.get('reason', '')
                break
        f['suppressed'] = matched
        f['suppression_reason'] = reason

    return findings


def count_by_severity(findings: List[Dict[str, Any]],
                      ) -> Dict[str, Dict[str, int]]:
    """Count findings by severity, split into active and suppressed.

    Returns::

        {
            "active": {"CRITICAL": 2, "HIGH": 3, ...},
            "suppressed": {"CRITICAL": 0, "HIGH": 1, ...},
            "total": {"CRITICAL": 2, "HIGH": 4, ...},
        }
    """
    active: Dict[str, int] = {}
    suppressed: Dict[str, int] = {}
    total: Dict[str, int] = {}

    for f in findings:
        sev = f.get('severity', 'INFO')
        total[sev] = total.get(sev, 0) + 1
        if f.get('suppressed'):
            suppressed[sev] = suppressed.get(sev, 0) + 1
        else:
            active[sev] = active.get(sev, 0) + 1

    return {'active': active, 'suppressed': suppressed, 'total': total}


# ---------------------------------------------------------------------------
# Risk scoring (used by top-risk summary, Feature 4)
# ---------------------------------------------------------------------------

SEVERITY_WEIGHTS = {
    'CRITICAL': 15, 'HIGH': 8, 'MEDIUM': 3, 'LOW': 1, 'INFO': 0,
}

CONFIDENCE_WEIGHTS = {
    'deterministic': 1.0,
    'datasheet-backed': 0.9,
    'heuristic': 0.7,
    'ai-inferred': 0.5,
}

# Category → risk bucket(s) with boost multiplier
RESPIN_CATEGORIES = {
    'ground_plane', 'stackup', 'diff_pair', 'board_edge',
    'pdn', 'via_stitching', 'return_path',
}
BRINGUP_CATEGORIES = {
    'thermal_safety', 'switching_emc', 'pdn', 'esd_path',
}
MANUFACTURING_CATEGORIES = {
    'dfm_violation', 'tombstoning', 'documentation', 'thermal_pad_vias',
}

BUCKET_BOOSTS = {
    'respin': 1.5,
    'bringup': 1.3,
    'manufacturing': 1.2,
}


def compute_finding_risk(finding: Dict[str, Any], bucket: str) -> float:
    """Compute risk score for a single finding in a specific bucket."""
    sev = SEVERITY_WEIGHTS.get(finding.get('severity', 'INFO'), 0)
    conf = CONFIDENCE_WEIGHTS.get(finding.get('confidence', 'heuristic'), 0.7)
    boost = BUCKET_BOOSTS.get(bucket, 1.0)
    return sev * conf * boost


def classify_finding_buckets(finding: Dict[str, Any]) -> List[str]:
    """Return which risk buckets a finding belongs to."""
    cat = finding.get('category', '')
    buckets = []
    if cat in RESPIN_CATEGORIES:
        buckets.append('respin')
    if cat in BRINGUP_CATEGORIES:
        buckets.append('bringup')
    if cat in MANUFACTURING_CATEGORIES:
        buckets.append('manufacturing')
    return buckets


def compute_top_risks(all_findings: List[Dict[str, Any]],
                      top_n: int = 3,
                      ) -> Dict[str, List[Dict[str, Any]]]:
    """Compute top-N findings per risk bucket across all analyzers.

    Each finding in *all_findings* should have: severity, confidence,
    category, rule_id, title, source (analyzer name).

    Returns::

        {
            "respin": [top 3 findings],
            "bringup": [top 3 findings],
            "manufacturing": [top 3 findings],
        }

    Only includes active (non-suppressed) findings.
    """
    buckets: Dict[str, List[tuple]] = {
        'respin': [], 'bringup': [], 'manufacturing': [],
    }

    for f in all_findings:
        if f.get('suppressed'):
            continue
        for bucket in classify_finding_buckets(f):
            score = compute_finding_risk(f, bucket)
            if score > 0:
                buckets[bucket].append((score, f))

    result: Dict[str, List[Dict[str, Any]]] = {}
    for bucket, scored in buckets.items():
        scored.sort(key=lambda x: x[0], reverse=True)
        result[bucket] = [f for _, f in scored[:top_n]]

    return result
