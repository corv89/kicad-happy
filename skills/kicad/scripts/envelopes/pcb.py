"""PCB analyzer output envelope (v1.4 SOT).

The runtime emits ~35 top-level keys on the simple fixture: tight core
(analyzer_type, schema_version, summary, trust_summary, file, statistics)
plus board-specific blocks (layers, nets, footprints, tracks, vias,
zones, keepout_zones, silkscreen, ground_domains, ...) and gated
analysis bags (placement_density, power_net_routing, dfm_summary,
design_rule_compliance, ...).

Tightens:
    - analyzer_type + schema_version carry ``const`` discriminators.
    - summary, trust_summary, statistics, setup, board_outline, tracks,
      vias are typed dataclasses mirroring the runtime shape.
    - Nested footprint/layer/zone/net-length/etc. lists are typed as
      ``list[dict]`` for v1.4 — shapes tighten per rule_id in v1.5.

Mode gating:
    - ``--full`` adds ``connectivity_graph`` + ``pad_to_pad_distances``
      at the top level, and adds ``segments``/``arcs`` inside ``tracks``
      and ``vias`` inside ``vias``. These are marked Optional since the
      contract test runs in default mode.

Additional properties are permitted by JSON Schema default so optional
sections that only appear on richer projects (thermal_analysis,
trace_proximity, copper_presence, tombstoning_risk, ...) validate
cleanly whether present or absent.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

# Make shared primitives importable when this module is loaded from
# tests, from the analyzer, or via generator tooling.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from analyzer_envelope import (  # noqa: E402
    TrustSummary, Finding, Assessment, BySeverity, InputsBlock,
)


@dataclass
class PCBSummary:
    """Top-level roll-up (mirrors the runtime's summary dict)."""
    total_findings: int = field(metadata={
        "description": "Count of findings[]."})
    by_severity: BySeverity = field(metadata={
        "description": "Breakdown by severity bucket."})


@dataclass
class PCBStatistics:
    """Board/component/net counts and routing rollup."""
    footprint_count: int = field(metadata={
        "description": "Total footprint count."})
    front_side: int = field(metadata={
        "description": "Footprints on F.Cu."})
    back_side: int = field(metadata={
        "description": "Footprints on B.Cu."})
    smd_count: int = field(metadata={
        "description": "SMD footprints."})
    tht_count: int = field(metadata={
        "description": "Through-hole footprints."})
    copper_layers_used: int = field(metadata={
        "description": "Number of distinct copper layers used."})
    copper_layer_names: list[str] = field(metadata={
        "description": "Copper layer names present on the board."})
    track_segments: int = field(metadata={
        "description": "Track segment count."})
    via_count: int = field(metadata={
        "description": "Total via count."})
    zone_count: int = field(metadata={
        "description": "Copper zone count."})
    total_track_length_mm: float = field(metadata={
        "description": "Sum of all track segment lengths in mm."})
    net_count: int = field(metadata={
        "description": "Distinct net count."})
    routing_complete: bool = field(metadata={
        "description": "True when no pads remain unrouted."})
    unrouted_net_count: int = field(metadata={
        "description": "Count of nets with unrouted pads."})
    board_width_mm: Optional[float] = field(default=None, metadata={
        "description": "Board outline width in mm; null if outline missing."})
    board_height_mm: Optional[float] = field(default=None, metadata={
        "description": "Board outline height in mm; null if outline missing."})
    board_area_mm2: Optional[float] = field(default=None, metadata={
        "description": "Board outline area in mm^2; null if outline missing."})


@dataclass
class PCBSetup:
    """Setup block extracted from (setup ...)."""
    board_thickness_mm: float = field(metadata={
        "description": "Stackup thickness in mm."})
    pad_to_mask_clearance: float = field(metadata={
        "description": "Soldermask clearance in mm."})
    legacy_teardrops: str = field(metadata={
        "description": "Legacy teardrop flag: 'yes' | 'no'."})
    allow_soldermask_bridges: str = field(metadata={
        "description": "Soldermask bridge allowance: 'yes' | 'no'."})


@dataclass
class BoundingBox:
    """Rectangular bounding box in mm."""
    min_x: float = field(metadata={"description": "Minimum X coordinate (mm)."})
    min_y: float = field(metadata={"description": "Minimum Y coordinate (mm)."})
    max_x: float = field(metadata={"description": "Maximum X coordinate (mm)."})
    max_y: float = field(metadata={"description": "Maximum Y coordinate (mm)."})
    width: float = field(metadata={"description": "max_x - min_x (mm)."})
    height: float = field(metadata={"description": "max_y - min_y (mm)."})


@dataclass
class BoardOutline:
    """Board outline geometry."""
    edge_count: int = field(metadata={
        "description": "Number of Edge.Cuts segments/arcs."})
    # TODO(v1.5): tighten edge items to typed EdgeSegment with discriminator.
    edges: list[dict] = field(metadata={
        "description": "Edge.Cuts segments. Each item has `type` and "
                       "geometric params (start/end for lines, center/radius "
                       "for arcs, etc.)."})
    bounding_box: Optional[BoundingBox] = field(default=None, metadata={
        "description": "Axis-aligned bounding box of the outline; null if "
                       "no outline was parsed."})


@dataclass
class ComponentGroup:
    """Refdes prefix roll-up entry."""
    count: int = field(metadata={
        "description": "Count of references with this prefix."})
    references: list[str] = field(metadata={
        "description": "References (e.g. ['R1', 'R2'])."})


@dataclass
class Tracks:
    """Tracks block summary. Adds per-segment arrays under --full."""
    segment_count: int = field(metadata={
        "description": "Line segment count."})
    arc_count: int = field(metadata={
        "description": "Arc segment count."})
    width_distribution: dict[str, int] = field(metadata={
        "description": "Histogram: width_mm_str -> segment count."})
    layer_distribution: dict[str, int] = field(metadata={
        "description": "Histogram: layer_name -> segment count."})
    # TODO(v1.5): tighten segments/arcs to typed records.
    segments: Optional[list[dict]] = field(default=None, metadata={
        "description": "Detailed segment array (--full only): "
                       "[{x1, y1, x2, y2, width, layer, net}]."})
    arcs: Optional[list[dict]] = field(default=None, metadata={
        "description": "Detailed arc array (--full only): "
                       "[{x1, y1, x2, y2, mid_x, mid_y, width, layer}]."})


@dataclass
class Vias:
    """Vias block summary. Adds per-via array under --full."""
    count: int = field(metadata={"description": "Total via count."})
    size_distribution: dict[str, int] = field(metadata={
        "description": "Histogram: '<diameter>/<drill>' -> via count."})
    # TODO(v1.5): tighten via_analysis sub-structure (type_breakdown,
    # annular_ring, current_capacity) once stable.
    via_analysis: dict = field(metadata={
        "description": "Nested analysis: type_breakdown, annular_ring, "
                       "current_capacity."})
    vias: Optional[list[dict]] = field(default=None, metadata={
        "description": "Detailed per-via array (--full only): "
                       "[{x, y, layers, size, drill, net, type}]."})


@dataclass
class Connectivity:
    """Routing completeness rollup."""
    total_nets_with_pads: int = field(metadata={
        "description": "Count of nets with at least one pad."})
    routed_nets: int = field(metadata={
        "description": "Count of nets with all pads routed."})
    unrouted_count: int = field(metadata={
        "description": "Count of unrouted pads."})
    routing_complete: bool = field(metadata={
        "description": "True when unrouted_count == 0."})


@dataclass
class PCBEnvelope:
    """Top-level output of analyze_pcb.py.

    Tight typing on universal fields (analyzer_type, schema_version,
    summary, trust_summary, statistics, setup, tracks, vias,
    connectivity, board_outline). Loose ``dict`` / ``list[dict]`` typing
    on detector-specific blocks (footprints, layers, zones, findings
    extras) for v1.4 — each tightens per rule_id in v1.5.
    """

    # --- Discriminators ---
    analyzer_type: str = field(metadata={
        "description": "Always 'pcb'.",
        "const": "pcb"})
    schema_version: str = field(metadata={
        "description": "Semver. Value: '1.4.0' at Track 1.1 landing.",
        "const": "1.4.0"})

    # --- Provenance ---
    inputs: InputsBlock = field(metadata={
        "description": "Source files, hashes, run_id, config_hash, upstream "
                       "artifacts for this run."})

    # --- Universal core ---
    summary: PCBSummary = field(metadata={
        "description": "Roll-up summary (total + by_severity)."})
    trust_summary: TrustSummary = field(metadata={
        "description": "Trust posture rollup (confidence + evidence source)."})
    kicad_version: str = field(metadata={
        "description": "KiCad generator version string, e.g. '9.0'."})
    file_version: str = field(metadata={
        "description": "KiCad file format version string (e.g. '20241228')."})
    findings: list[Finding] = field(metadata={
        "description": "All PCB findings (flat list)."})
    assessments: list[Assessment] = field(metadata={
        "description": "Informational assessments (empty for PCB at v1.4)."})
    statistics: PCBStatistics = field(metadata={
        "description": "Board/component/net counts and routing rollup."})
    setup: PCBSetup = field(metadata={
        "description": "Board setup block (thickness, soldermask, etc.)."})
    board_outline: BoardOutline = field(metadata={
        "description": "Edge.Cuts outline geometry + bounding box."})
    board_thickness_mm: float = field(metadata={
        "description": "Stackup thickness (mm); duplicated from setup for "
                       "downstream consumers."})
    board_metadata: dict = field(metadata={
        "description": "Board metadata bag (paper size, title block "
                       "fragments, etc.)."})
    connectivity: Connectivity = field(metadata={
        "description": "Routing completeness rollup."})
    tracks: Tracks = field(metadata={
        "description": "Track summary + optional detailed arrays under "
                       "--full."})
    vias: Vias = field(metadata={
        "description": "Via summary + optional detailed array under "
                       "--full."})

    # --- Net maps ---
    nets: dict[str, str] = field(metadata={
        "description": "Net ID (as string) -> net name."})
    net_name_to_id: dict[str, int] = field(metadata={
        "description": "Net name -> integer net ID. Reverse of nets."})

    # --- Layer / footprint / zone / etc. lists ---
    # TODO(v1.5): tighten items to typed Layer, Footprint, Zone, NetClass,
    # NetLength, PowerNetRouting records.
    layers: list[dict] = field(metadata={
        "description": "Layer stackup entries: "
                       "[{number, name, type, alias}]."})
    footprints: list[dict] = field(metadata={
        "description": "Every placed footprint. Fields include reference, "
                       "value, library, footprint, layer, x, y, angle, "
                       "type, mpn, manufacturer, description, pad_count, "
                       "courtyard, pad_nets, connected_nets, sch_path, "
                       "sheetname, sheetfile. Tightens to typed Footprint "
                       "in v1.5."})
    zones: list[dict] = field(metadata={
        "description": "Copper zones: [{net, net_name, layers, clearance, "
                       "min_thickness, thermal_gap, thermal_bridge_width, "
                       "outline_points, outline_area_mm2, is_filled, "
                       "outline_bbox}]."})
    keepout_zones: list[dict] = field(metadata={
        "description": "Keepout zones: [{name, layers, restrictions, "
                       "bounding_box, area_mm2, nearby_components}]."})
    net_classes: list[dict] = field(metadata={
        "description": "Net class definitions: [{name, clearance, "
                       "track_width, via_diameter, via_drill}]."})
    net_lengths: list[dict] = field(metadata={
        "description": "Per-net track length rollup: [{net, net_number, "
                       "total_length_mm, segment_count, via_count, "
                       "layers}]."})
    power_net_routing: list[dict] = field(metadata={
        "description": "Power net routing rollup: [{net, track_count, "
                       "total_length_mm, min_width_mm, max_width_mm, "
                       "widths_used}]."})

    # --- Refdes prefix groups ---
    component_groups: dict[str, ComponentGroup] = field(metadata={
        "description": "Refdes prefix -> {count, references}."})

    # --- Analysis bags (free-form dicts) ---
    ground_domains: dict = field(metadata={
        "description": "Ground topology: domain_count, domains[], "
                       "multi_domain_components."})
    silkscreen: dict = field(metadata={
        "description": "Silkscreen rollup: board_text_count, "
                       "refs_visible_on_silk, refs_hidden_on_silk, "
                       "documentation_warnings[], fab_notes_completeness, "
                       "silkscreen_completeness."})
    placement_density: dict = field(metadata={
        "description": "Placement density: board_area_cm2, "
                       "front_density_per_cm2, optional back_density_per_cm2."})
    dfm_summary: dict = field(metadata={
        "description": "DFM rollup: dfm_tier, metrics, violation_count."})
    design_rule_compliance: dict = field(metadata={
        "description": "Design rule compliance: compliant, rules_checked, "
                       "rules_source."})
    project_settings: dict = field(metadata={
        "description": "Selected settings extracted from .kicad_pro: "
                       "source, net_classes, design_rules."})

    # --- Stage/audience derived blocks ---
    audience_summary: Optional[dict] = field(default=None, metadata={
        "description": "Designer/reviewer/manager summary views; only "
                       "present when output filters ran."})

    # --- Project/intent/config attachments ---
    design_intent: Optional[dict] = field(default=None, metadata={
        "description": "Resolved design intent: product_class, ipc_class, "
                       "target_market, operating_temp_range, "
                       "preferred_passive_size, test_coverage_target, "
                       "approved_manufacturers, expected_lifetime_years, "
                       "detection_signals, confidence, source."})
    project_config: Optional[dict] = field(default=None, metadata={
        "description": "Copy of the resolved project block from "
                       ".kicad-happy.json (when present)."})

    # --- --full mode additions ---
    connectivity_graph: Optional[dict] = field(default=None, metadata={
        "description": "Per-net connectivity graph (island map). "
                       "Emitted only in --full mode."})
    pad_to_pad_distances: Optional[dict] = field(default=None, metadata={
        "description": "Pad-to-pad routing distances keyed by 'R1.2-D1.1' "
                       "style endpoints. Emitted only in --full mode."})

    # --- Optional analysis bags (gated on content) ---
    thermal_analysis: Optional[dict] = field(default=None, metadata={
        "description": "Thermal management analysis (when triggered)."})
    thermal_pad_vias: Optional[dict] = field(default=None, metadata={
        "description": "Thermal pad via audit (when triggered)."})
    trace_proximity: Optional[dict] = field(default=None, metadata={
        "description": "Trace proximity / crosstalk analysis (--proximity)."})
    copper_presence: Optional[dict] = field(default=None, metadata={
        "description": "Copper presence sampling rollup (when triggered)."})
    tombstoning_risk: Optional[dict] = field(default=None, metadata={
        "description": "Tombstoning risk analysis (when triggered)."})
    decoupling_placement: Optional[dict] = field(default=None, metadata={
        "description": "Decoupling capacitor placement audit "
                       "(when triggered)."})
    current_capacity: Optional[dict] = field(default=None, metadata={
        "description": "Current capacity analysis (when triggered)."})
    placement_analysis: Optional[dict] = field(default=None, metadata={
        "description": "Placement analysis details (when triggered)."})
    dfm: Optional[dict] = field(default=None, metadata={
        "description": "Extended DFM analysis (when triggered)."})
