"""Copper connectivity graph via union-find over pads, vias, and zone fills.

Builds a per-net island map from PCB data: pads connected by tracks or zone
copper are grouped into islands. Nets with multiple islands have plane splits
or routing gaps. Gap locations are estimated from island bounding boxes.

Called from analyze_pcb.py in --full mode. Requires track segment coordinates,
via positions, footprint pad positions, and ZoneFills polygon data.
"""

from __future__ import annotations

import math


class UnionFind:
    """Weighted union-find with path compression."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}

    def make_set(self, x: str) -> None:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0

    def find(self, x: str) -> str:
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1

    def components(self) -> dict[str, list[str]]:
        """Return {root: [members]} for all sets."""
        groups: dict[str, list[str]] = {}
        for x in self._parent:
            root = self.find(x)
            groups.setdefault(root, []).append(x)
        return groups


class _SpatialGrid:
    """Simple 2D grid index for fast nearest-neighbor lookup."""

    def __init__(self, cell_size: float = 0.5) -> None:
        self._cell = cell_size
        self._grid: dict[tuple[int, int], list[tuple[str, float, float]]] = {}

    def add(self, key: str, x: float, y: float) -> None:
        cx, cy = int(x / self._cell), int(y / self._cell)
        self._grid.setdefault((cx, cy), []).append((key, x, y))

    def nearest(self, x: float, y: float, tolerance: float) -> str | None:
        """Find nearest key within tolerance. Returns None if nothing close."""
        cx, cy = int(x / self._cell), int(y / self._cell)
        best_key = None
        best_dist = tolerance * tolerance
        r = max(1, int(math.ceil(tolerance / self._cell)))
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for key, kx, ky in self._grid.get((cx + dx, cy + dy), []):
                    d2 = (kx - x) ** 2 + (ky - y) ** 2
                    if d2 < best_dist:
                        best_dist = d2
                        best_key = key
        return best_key


def build_connectivity_graph(
    footprints: list[dict],
    tracks: dict,
    vias: dict,
    zone_fills,
    zones: list[dict],
    net_id_map: dict[int, str],
) -> dict[str, dict]:
    """Build per-net connectivity graph using union-find.

    Args:
        footprints: PCB footprint list with pads (abs_x, abs_y, net_name).
        tracks: Track dict with 'segments' key.
        vias: Via dict with 'vias' key.
        zone_fills: ZoneFills instance with filled polygon data.
        zones: Zone list matching zone_fills index.
        net_id_map: {net_id: net_name} mapping.

    Returns:
        Dict keyed by net_name with islands, components, gaps, disconnected_pads.
    """
    segments = tracks.get('segments', [])
    via_list = vias.get('vias', [])

    # Collect all nodes per net
    net_nodes: dict[str, list[tuple[str, float, float, str]]] = {}

    for fp in footprints:
        ref = fp.get('reference', '')
        for pad in fp.get('pads', []):
            net_name = pad.get('net_name', '')
            if not net_name:
                continue
            x = pad.get('abs_x')
            y = pad.get('abs_y')
            if x is None or y is None:
                continue
            pad_key = f"{ref}:{pad.get('number', '?')}"
            layers = pad.get('layers', [])
            for layer in layers:
                if '.Cu' in layer:
                    net_nodes.setdefault(net_name, []).append((pad_key, x, y, layer))

    for i, via in enumerate(via_list):
        net_id = via.get('net', 0)
        net_name = net_id_map.get(net_id, '') if isinstance(net_id, int) else str(net_id)
        if not net_name:
            continue
        x = via.get('x')
        y = via.get('y')
        if x is None or y is None:
            continue
        via_key = f"via_{i}"
        layers = via.get('layers', [])
        for layer in layers:
            if '.Cu' in layer:
                net_nodes.setdefault(net_name, []).append((via_key, x, y, layer))

    result: dict[str, dict] = {}

    for net_name, nodes in net_nodes.items():
        if len(nodes) < 2:
            if nodes:
                result[net_name] = {
                    'islands': 1,
                    'components': {nodes[0][0]: 0},
                    'gaps': [],
                    'disconnected_pads': [],
                }
            continue

        uf = UnionFind()
        all_keys: set[str] = set()
        for key, x, y, layer in nodes:
            uf.make_set(key)
            all_keys.add(key)

        # Spatial index per layer
        layer_grids: dict[str, _SpatialGrid] = {}
        key_positions: dict[str, tuple[float, float]] = {}
        for key, x, y, layer in nodes:
            grid = layer_grids.setdefault(layer, _SpatialGrid(0.5))
            grid.add(key, x, y)
            key_positions[key] = (x, y)

        # Phase 1: Track segments
        for seg in segments:
            seg_net_id = seg.get('net', 0)
            seg_net = net_id_map.get(seg_net_id, '') if isinstance(seg_net_id, int) else str(seg_net_id)
            if seg_net != net_name:
                continue
            layer = seg.get('layer', '')
            grid = layer_grids.get(layer)
            if not grid:
                continue
            x1, y1 = seg.get('x1', 0), seg.get('y1', 0)
            x2, y2 = seg.get('x2', 0), seg.get('y2', 0)
            k1 = grid.nearest(x1, y1, 0.15)
            k2 = grid.nearest(x2, y2, 0.15)
            if k1 and k2 and k1 != k2:
                uf.union(k1, k2)
            elif k1 and not k2:
                k2 = grid.nearest(x2, y2, 0.5)
                if k2 and k1 != k2:
                    uf.union(k1, k2)
            elif k2 and not k1:
                k1 = grid.nearest(x1, y1, 0.5)
                if k1 and k1 != k2:
                    uf.union(k1, k2)

        # Phase 2: Zone fills
        if zone_fills is not None and zone_fills.has_data:
            for layer, grid in layer_grids.items():
                layer_keys = [(key, x, y) for key, x, y, l in nodes if l == layer]
                if len(layer_keys) < 2:
                    continue
                zone_groups: dict[int, list[str]] = {}
                for key, x, y in layer_keys:
                    matching_zones = zone_fills.zones_at_point(x, y, layer, zones)
                    for z in matching_zones:
                        if z.get('net_name', '') == net_name:
                            zone_groups.setdefault(id(z), []).append(key)
                for z_idx, members in zone_groups.items():
                    for i in range(1, len(members)):
                        uf.union(members[0], members[i])

        # Build island map
        components_map = uf.components()
        island_id_map: dict[str, int] = {}
        island_keys: dict[int, list[str]] = {}
        for idx, (root, members) in enumerate(sorted(components_map.items())):
            for m in members:
                island_id_map[m] = idx
            island_keys[idx] = members

        num_islands = len(components_map)

        # Find gaps
        gaps = []
        if num_islands > 1:
            island_bboxes: dict[int, tuple[float, float, float, float]] = {}
            for island_idx, members in island_keys.items():
                xs = [key_positions[k][0] for k in members if k in key_positions]
                ys = [key_positions[k][1] for k in members if k in key_positions]
                if xs and ys:
                    island_bboxes[island_idx] = (min(xs), min(ys), max(xs), max(ys))

            island_ids_sorted = sorted(island_bboxes.keys())
            for i in range(len(island_ids_sorted)):
                for j in range(i + 1, len(island_ids_sorted)):
                    id_a, id_b = island_ids_sorted[i], island_ids_sorted[j]
                    ba = island_bboxes[id_a]
                    bb = island_bboxes[id_b]
                    gx1 = min(ba[2], bb[2])
                    gy1 = min(ba[3], bb[3])
                    gx2 = max(ba[0], bb[0])
                    gy2 = max(ba[1], bb[1])
                    if gx1 > gx2:
                        gx1, gx2 = gx2, gx1
                    if gy1 > gy2:
                        gy1, gy2 = gy2, gy1
                    layer_guess = ''
                    for key, x, y, l in nodes:
                        if key in island_keys.get(id_a, []) or key in island_keys.get(id_b, []):
                            layer_guess = l
                            break
                    gaps.append({
                        'layer': layer_guess,
                        'bbox': [round(gx1, 2), round(gy1, 2), round(gx2, 2), round(gy2, 2)],
                        'between_islands': [id_a, id_b],
                    })

        # Find disconnected pad pairs
        disconnected = []
        pad_keys = [k for k in all_keys if not k.startswith('via_')]
        if num_islands > 1 and len(pad_keys) > 1:
            island_rep_pads: dict[int, str] = {}
            for pk in pad_keys:
                isl = island_id_map.get(pk)
                if isl is not None and isl not in island_rep_pads:
                    island_rep_pads[isl] = pk
            rep_list = list(island_rep_pads.values())
            for i in range(len(rep_list)):
                for j in range(i + 1, len(rep_list)):
                    disconnected.append([rep_list[i], rep_list[j]])

        result[net_name] = {
            'islands': num_islands,
            'components': {k: island_id_map[k] for k in sorted(all_keys)},
            'gaps': gaps,
            'disconnected_pads': disconnected,
        }

    return result
