"""
Typed data structures for KiCad analysis.

Provides AnalysisContext — the shared state object passed between all
analysis functions, replacing repeated comp_lookup/parsed_values/known_power_rails
construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_utils import is_ground_name, is_power_net_name, parse_value


@dataclass
class AnalysisContext:
    """Shared state passed between all analysis functions.

    Built once in analyze_schematic() after nets and pin_net are ready.
    Replaces ~10 `comp_lookup = {c["reference"]: c for c in components}` rebuilds,
    2 duplicate `get_two_pin_nets` closure definitions, and repeated
    `known_power_rails` construction.
    """

    components: list[dict]
    nets: dict[str, dict]
    lib_symbols: dict
    pin_net: dict[tuple[str, str], tuple[str | None, str | None]]
    comp_lookup: dict[str, dict] = field(default_factory=dict)
    parsed_values: dict[str, float] = field(default_factory=dict)
    known_power_rails: set[str] = field(default_factory=set)
    ref_pins: dict[str, dict[str, tuple[str | None, str | None]]] = field(default_factory=dict)
    no_connects: list[dict] = field(default_factory=list)
    generator_version: str = "unknown"

    def __post_init__(self) -> None:
        if not self.comp_lookup:
            self.comp_lookup = {c["reference"]: c for c in self.components}
        if not self.parsed_values:
            for c in self.components:
                val = parse_value(c.get("value", ""), component_type=c.get("type"))
                if val is not None:
                    self.parsed_values[c["reference"]] = val
        if not self.known_power_rails:
            for net_name, net_info in self.nets.items():
                for p in net_info.get("pins", []):
                    if p["component"].startswith("#PWR") or p["component"].startswith("#FLG"):
                        self.known_power_rails.add(net_name)
                        break
        if not self.ref_pins:
            rp: dict[str, dict[str, tuple[str | None, str | None]]] = {}
            for (comp_ref, pin_num), val in self.pin_net.items():
                rp.setdefault(comp_ref, {})[pin_num] = val
            self.ref_pins = rp

    def is_power_net(self, name: str | None) -> bool:
        return is_power_net_name(name, self.known_power_rails)

    def is_ground(self, name: str | None) -> bool:
        return is_ground_name(name)

    def get_two_pin_nets(self, ref: str) -> tuple[str | None, str | None]:
        n1, _ = self.pin_net.get((ref, "1"), (None, None))
        n2, _ = self.pin_net.get((ref, "2"), (None, None))
        return n1, n2
