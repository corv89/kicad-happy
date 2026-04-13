"""Rich finding schema shared by all detectors and validators.

Every detection and validation finding uses make_finding() to produce
a self-describing dict consumable by kidoc, suggest-fixes, and lighter LLMs.
"""

from __future__ import annotations

VALID_SEVERITIES = ('error', 'warning', 'info')
VALID_CONFIDENCES = ('deterministic', 'heuristic')
VALID_EVIDENCE_SOURCES = ('datasheet', 'topology', 'heuristic_rule')
VALID_FIX_TYPES = (
    'resistor_value_change', 'capacitor_value_change',
    'add_component', 'remove_component', 'swap_connection', 'add_protection',
)


def make_finding(
    detector: str,
    rule_id: str,
    category: str,
    summary: str,
    description: str,
    severity: str = 'warning',
    confidence: str = 'heuristic',
    evidence_source: str = 'heuristic_rule',
    components: list | None = None,
    nets: list | None = None,
    pins: list | None = None,
    recommendation: str = '',
    fix_params: dict | None = None,
    report_section: str | None = None,
    impact: str | None = None,
    standard_ref: str | None = None,
    **extra,
) -> dict:
    """Build a rich finding dict with consistent structure.

    Required fields: detector, rule_id, category, summary, description.
    All other fields have sensible defaults.

    Extra kwargs are merged into the finding (e.g., domain-specific data).
    """
    finding = {
        'detector': detector,
        'rule_id': rule_id,
        'category': category,
        'summary': summary,
        'description': description,
        'components': components if components is not None else [],
        'nets': nets if nets is not None else [],
        'pins': pins if pins is not None else [],
        'severity': severity,
        'confidence': confidence,
        'evidence_source': evidence_source,
        'recommendation': recommendation,
    }
    if fix_params is not None:
        finding['fix_params'] = fix_params
    finding['report_context'] = {
        'section': report_section or category.replace('_', ' ').title(),
        'impact': impact or '',
        'standard_ref': standard_ref or '',
    }
    if extra:
        finding.update(extra)
    return finding
