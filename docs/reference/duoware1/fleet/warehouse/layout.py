"""
DUO-WARE warehouse map: NFC/RFID checkpoints joined by aisle segments, plus routing.

Checkpoints are the trusted position fixes. Between them a robot relies on its own motion
sensing, so the map only needs the checkpoint graph, not continuous coordinates.
"""

import heapq
import math
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple


@dataclass
class Checkpoint:
    cp_id: str
    kind: str              # INTERSECTION, RACK, PICKUP, DROP, PACKING, CHARGER, DOCK, RESTRICTED
    x: float
    y: float


def edge_key(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a <= b else (b, a)


class WarehouseMap:
    def __init__(self):
        self.checkpoints: Dict[str, Checkpoint] = {}
        self.adj: Dict[str, Dict[str, float]] = {}
        self.rough: Set[Tuple[str, str]] = set()

    def add_checkpoint(self, cp_id: str, kind: str, x: float, y: float):
        self.checkpoints[cp_id] = Checkpoint(cp_id, kind, x, y)
        self.adj.setdefault(cp_id, {})

    def connect(self, a: str, b: str, length: Optional[float] = None, rough: bool = False):
        if length is None:
            pa, pb = self.checkpoints[a], self.checkpoints[b]
            length = math.hypot(pa.x - pb.x, pa.y - pb.y)
        self.adj[a][b] = length
        self.adj[b][a] = length
        if rough:
            self.rough.add(edge_key(a, b))

    def of_kind(self, kind: str) -> List[str]:
        return [c for c, cp in self.checkpoints.items() if cp.kind == kind]

    def adjacent(self, a: str, b: str) -> bool:
        return b in self.adj.get(a, {})

    def path_length(self, path: Iterable[str]) -> float:
        path = list(path)
        return sum(self.adj[a][b] for a, b in zip(path, path[1:]))

    def shortest(self, start: str, goal: str,
                 edge_cost: Optional[Callable[[str, str, float], float]] = None,
                 avoid_nodes: Iterable[str] = (),
                 avoid_edges: Iterable[Tuple[str, str]] = ()) -> Optional[List[str]]:
        """Dijkstra. Returns [start, ..., goal] or None. edge_cost may return math.inf to forbid."""
        if start == goal:
            return [start]
        avoid_nodes = set(avoid_nodes) - {start, goal}
        avoid_edges = {edge_key(*e) for e in avoid_edges}
        dist = {start: 0.0}
        prev: Dict[str, str] = {}
        heap = [(0.0, start)]
        while heap:
            d, u = heapq.heappop(heap)
            if u == goal:
                break
            if d > dist.get(u, math.inf):
                continue
            for v, length in self.adj[u].items():
                if v in avoid_nodes or edge_key(u, v) in avoid_edges:
                    continue
                # Leaf stations (racks, chargers, docks) are destinations, never short-cuts.
                if v != goal and self.checkpoints[v].kind != "INTERSECTION":
                    continue
                c = edge_cost(u, v, length) if edge_cost else length
                if math.isinf(c):
                    continue
                nd = d + c
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        if goal not in dist:
            return None
        path = [goal]
        while path[-1] != start:
            path.append(prev[path[-1]])
        return path[::-1]

    def distance(self, start: str, goal: str) -> float:
        path = self.shortest(start, goal)
        return math.inf if path is None else self.path_length(path)


def demo_warehouse() -> WarehouseMap:
    """
    3 x 3 grid of intersections C01..C09 (6 m apart) with racks, packing, chargers and docks.

        DOCK_R01 DOCK_R02 DOCK_R03 DOCK_R04 DOCK_R05
          |        |        |        |        |      (docks hang off the south row)
        C07 ---- C08 ---- C09 -- PACKING_P03
         |        |        |
        C04 ---- C05 ---- C06 -- PACKING_P01
         |        |        |
        C01 ---- C02 ---- C03 -- CHARGER_01 / CHARGER_02
        RACK_A12 RACK_A14 RACK_B03 hang off C01, C02, C04; RACK_B07 off C05.
    """
    m = WarehouseMap()
    s = 6.0
    for i in range(9):
        col, row = i % 3, i // 3
        m.add_checkpoint(f"C0{i + 1}", "INTERSECTION", col * s, row * s)
    for a, b in [("C01", "C02"), ("C02", "C03"), ("C04", "C05"), ("C05", "C06"), ("C07", "C08"),
                 ("C08", "C09"), ("C01", "C04"), ("C04", "C07"), ("C02", "C05"), ("C05", "C08"),
                 ("C03", "C06"), ("C06", "C09")]:
        m.connect(a, b)
    # C05-C06 has a floor joint: fine for normal loads, avoided for highly fragile parcels.
    m.rough.add(edge_key("C05", "C06"))

    leaves = [
        ("RACK_A12", "RACK", "C01", -2.0, -2.0), ("RACK_A14", "RACK", "C02", 6.0, -2.0),
        ("RACK_B03", "RACK", "C04", -2.0, 6.0), ("RACK_B07", "RACK", "C05", 6.0, 8.0),
        ("PACKING_P01", "PACKING", "C06", 14.0, 6.0), ("PACKING_P03", "PACKING", "C09", 14.0, 12.0),
        ("CHARGER_01", "CHARGER", "C03", 14.0, 0.0), ("CHARGER_02", "CHARGER", "C03", 12.0, -2.0),
        ("DOCK_R01", "DOCK", "C07", -2.0, 14.0), ("DOCK_R02", "DOCK", "C07", 0.0, 14.0),
        ("DOCK_R03", "DOCK", "C08", 6.0, 14.0), ("DOCK_R04", "DOCK", "C09", 12.0, 14.0),
        ("DOCK_R05", "DOCK", "C09", 14.0, 14.0),
    ]
    for cp_id, kind, parent, x, y in leaves:
        m.add_checkpoint(cp_id, kind, x, y)
        m.connect(cp_id, parent)
    return m
