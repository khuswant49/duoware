"""Layout service: the stored road network, live node states, measuring and blocking (PROTOCOL.md §7.5,
DECISIONS.md D24, D27, D28). Topology comes from the tag registry, geometry from the last `measure`."""

import json
import threading
from typing import Any

from duoware.clock import Clock
from duoware.errors import DuoError
from duoware.layout import graph
from duoware.layout.graph import GridPos, Point
from duoware.layout.suggest import suggest_grid
from duoware.localization.world import WorldModel
from duoware.registry.model import NodeRole, RegistrySnapshot
from duoware.registry.service import TagRegistry
from duoware.safety import SafetyState
from duoware.settings import Settings
from duoware.store.db import StateDb
from duoware.store.events import EventLog

NS_PER_S = 1_000_000_000


class LayoutService:
    def __init__(self, db: StateDb, registry: TagRegistry, world: WorldModel, settings: Settings,
                 safety: SafetyState, events: EventLog, clock: Clock) -> None:
        self._db, self._registry, self._world, self._settings = db, registry, world, settings
        self._safety, self._events, self._clock = safety, events, clock
        self._lock = threading.RLock()
        doc = db.get_json("SELECT doc FROM layout WHERE id = 1")
        self._doc: dict[str, Any] = doc or self._empty(db.get_int_meta("layout_version"))
        self._moved: set[int] = set()
        self._away_since: dict[int, int] = {}
        registry.on_change(self._on_registry_change)

    # ------------------------------------------------------------------------------ stored document

    @staticmethod
    def _empty(version: int) -> dict[str, Any]:
        return {"version": version, "measured_wall_ms": None, "grid_rotation_deg": 0.0, "positions": {},
                "blocked_nodes": [], "blocked_edges": []}

    @property
    def version(self) -> int:
        return int(self._doc["version"])

    def _store(self, doc: dict[str, Any]) -> None:
        with self._db.tx() as c:
            c.execute("INSERT INTO layout(id, doc) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET doc = excluded.doc",
                      (json.dumps(doc),))
            c.execute("INSERT INTO meta(key, value) VALUES('layout_version', ?) ON CONFLICT(key) DO UPDATE SET "
                      "value = excluded.value", (str(doc["version"]),))
        self._doc = doc

    def _bump(self, **changes: Any) -> dict[str, Any]:
        doc = {**self._doc, **changes, "version": self.version + 1}
        self._store(doc)
        return doc

    def export(self) -> dict | None:
        """The stored layout for a venue preset, or None if nothing was measured or blocked."""
        d = self._doc
        if not d["positions"] and not d["blocked_nodes"] and not d["blocked_edges"]:
            return None
        return {k: d[k] for k in ("measured_wall_ms", "grid_rotation_deg", "positions", "blocked_nodes", "blocked_edges")}

    def import_(self, doc: dict | None) -> None:
        """Replaces the stored layout (venue preset apply). Logs nothing: the preset apply is one event."""
        with self._lock:
            d = doc or {}
            self._bump(measured_wall_ms=d.get("measured_wall_ms"), grid_rotation_deg=float(d.get("grid_rotation_deg", 0.0)),
                       positions={str(k): list(v) for k, v in d.get("positions", {}).items()},
                       blocked_nodes=list(d.get("blocked_nodes", [])), blocked_edges=[list(e) for e in d.get("blocked_edges", [])])
            self._moved.clear()
            self._away_since.clear()

    def check_against_live(self, doc: dict | None) -> dict:
        """Compares a preset's measured positions with what the camera sees now. Moved nodes are marked `moved`."""
        out: dict[str, list] = {"matched": [], "moved": [], "missing": []}
        tol = self._settings.map_rules.tracking.node_move_tol_mm
        with self._lock:
            for sid, pos in sorted((doc or {}).get("positions", {}).items(), key=lambda kv: int(kv[0])):
                nid = int(sid)
                live = self._world.live_position(nid)
                if live is None:
                    out["missing"].append(nid)
                    continue
                dist = ((live[0] - pos[0]) ** 2 + (live[1] - pos[1]) ** 2) ** 0.5
                if dist <= tol:
                    out["matched"].append(nid)
                else:
                    out["moved"].append({"id": nid, "distance_mm": round(dist, 1)})
                    self._moved.add(nid)
        return out

    # ------------------------------------------------------------------------------ topology and geometry

    def _nodes_grid(self, snap: RegistrySnapshot | None = None) -> dict[int, GridPos]:
        snap = snap or self._registry.snapshot()
        return {n.id: n.grid for n in snap.nodes() if n.grid is not None}

    def _positions(self, nodes: dict[int, GridPos]) -> dict[int, Point | None]:
        stored = self._doc["positions"]
        return {nid: (tuple(stored[str(nid)]) if str(nid) in stored else None) for nid in nodes}

    def _obstacle_centres(self, now_ns: int) -> list[tuple[int, Point, float]]:
        snap, w = self._registry.snapshot(), self._world.snapshot(now_ns)
        default = self._settings.map_rules.tracking.obstacle_block_radius_mm
        out = []
        for o in snap.obstacles():
            obs = w.tags.get(o.id)
            if obs is not None and obs.x is not None:
                out.append((o.id, (obs.x, obs.y), o.fields.radius_mm if o.fields.radius_mm is not None else default))
        return out

    # ------------------------------------------------------------------------------ the §7.5 document

    def current(self, now_ns: int | None = None) -> dict:
        now = self._clock.mono_ns() if now_ns is None else now_ns
        with self._lock:
            snap = self._registry.snapshot()
            nodes = self._nodes_grid(snap)
            pos = self._positions(nodes)
            edges = graph.build_edges(nodes)
            rot = float(self._doc["grid_rotation_deg"])
            cfg = self._settings.map_rules
            world = self._world.snapshot(now)
            footprints = [{"cam": c.cam, "polygon_mm": c.footprint} for c in world.cameras.values()
                          if c.usable and c.footprint]
            obstacles = self._obstacle_centres(now)
            admin_nodes, admin_edges = set(self._doc["blocked_nodes"]), {tuple(e) for e in self._doc["blocked_edges"]}
            cfg_nodes, cfg_edges = set(cfg.blocked.nodes), {tuple(e) for e in cfg.blocked.edges}

            def node_block(nid: int) -> str | None:
                if nid in admin_nodes:
                    return "admin"
                if nid in cfg_nodes:
                    return "config"
                p = pos[nid]
                if p is not None:
                    for oid, c, r in obstacles:
                        if ((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2) ** 0.5 <= r:
                            return f"tag:{oid}"
                return None

            node_out, blocked_by = [], {}
            for nid, g in sorted(nodes.items(), key=lambda kv: kv[1]):
                tag = snap.get(nid)
                b = node_block(nid)
                blocked_by[nid] = b
                p = pos[nid]
                if p is None:
                    st = "unmeasured"
                elif b is not None:
                    st = "blocked"
                elif nid in self._moved:
                    st = "moved"
                elif not any(graph.point_in_polygon(p, f["polygon_mm"]) for f in footprints):
                    st = "outside_view"
                else:
                    st = "ok"
                station = tag.station
                node_out.append({"id": nid, "grid": list(g), "x_mm": None if p is None else round(p[0], 1),
                                 "y_mm": None if p is None else round(p[1], 1),
                                 "station": None if station is None else {"name": station.name, "kind": station.kind},
                                 "state": st, "blocked_by": b})

            edge_out, blocked_keys = [], set()
            for a, b in edges:
                row = graph.is_row_edge(nodes[a], nodes[b])
                geo = graph.edge_geometry(a, b, pos, rot, row)
                by = None
                if (a, b) in admin_edges or (b, a) in admin_edges:
                    by = "admin"
                elif (a, b) in cfg_edges or (b, a) in cfg_edges:
                    by = "config"
                else:
                    for n in (a, b):
                        if blocked_by[n] is not None:
                            by = blocked_by[n]
                        elif n in self._moved:
                            by = f"moved:{n}"
                        if by:
                            break
                    if by is None and pos[a] is not None and pos[b] is not None:
                        for oid, c, r in obstacles:
                            if graph.point_segment_distance(c, pos[a], pos[b]) <= r:
                                by = f"tag:{oid}"
                                break
                if by:
                    blocked_keys.add((a, b))
                edge_out.append({"a": a, "b": b, "length_mm": None if geo is None else round(geo[0], 1),
                                 "bearing_deg": None if geo is None else round(geo[1], 1),
                                 "angle_error_deg": None if geo is None else round(geo[2], 1),
                                 "state": "blocked" if by else "ok", "blocked_by": by})
            warnings = graph.validate(nodes, pos, edges, rot, cfg.validation, self._settings.cars,
                                      [f["polygon_mm"] for f in footprints], blocked_keys,
                                      {n for n in nodes if blocked_by[n] or n in self._moved})
            return {"version": self.version, "measured_wall_ms": self._doc["measured_wall_ms"],
                    "grid_rotation_deg": round(rot, 2), "nodes": node_out, "edges": edge_out,
                    "warnings": [{"code": w.code, "ids": list(w.ids), "message": w.message} for w in warnings],
                    "footprints": [{"cam": f["cam"], "polygon_mm": [[round(x, 1), round(y, 1)] for x, y in f["polygon_mm"]]}
                                   for f in footprints]}

    # ------------------------------------------------------------------------------ measure / block

    def _check_version(self, expected: Any) -> None:
        if expected != self.version:
            raise DuoError("version_conflict", 409, "The layout changed since you last looked.",
                           {"current_layout_version": self.version})

    def measure(self, expected_version: Any, operator: str = "local") -> dict:
        """Stores the live averaged position of every node that has a `grid`. Motion-affecting."""
        with self._lock:
            self._safety.require_motion_allowed()
            self._check_version(expected_version)
            nodes = self._nodes_grid()
            if not nodes:
                raise DuoError("validation", 400, "No node has a row/column yet: assign them on the Tags page first.",
                               {"fields": ["grid"]})
            now = self._clock.mono_ns()
            positions = {nid: self._world.node_position(nid, now) for nid in nodes}
            missing = sorted(nid for nid, p in positions.items() if p is None)
            if missing:
                raise DuoError("nodes_not_visible", 409,
                               f"Node(s) {missing} are not currently seen by a calibrated camera.", {"ids": missing})
            edges = graph.build_edges(nodes)
            rot = graph.fit_grid_rotation(nodes, positions, edges)
            self._bump(measured_wall_ms=self._clock.wall_ms(), grid_rotation_deg=rot,
                       positions={str(k): [round(v[0], 2), round(v[1], 2)] for k, v in positions.items()})
            self._moved.clear()
            self._away_since.clear()
            cur = self.current(now)
            self._events.log("layout", operator=operator, key="measure", value=self.version, prev=self.version - 1,
                             facts={"positions": self._doc["positions"], "grid_rotation_deg": round(rot, 2),
                                    "warnings": cur["warnings"]},
                             reason="operator measured the layout from the camera")
            return cur

    def set_blocked(self, nodes: Any, edges: Any, expected_version: Any, operator: str = "local") -> dict:
        with self._lock:
            self._check_version(expected_version)
            topo = self._nodes_grid()
            if not isinstance(nodes, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in nodes):
                raise DuoError("validation", 400, "nodes must be a list of tag IDs.", {"fields": ["nodes"]})
            bad = sorted(n for n in nodes if n not in topo)
            if bad:
                raise DuoError("validation", 400, f"Tag(s) {bad} are not nodes of the road network.", {"fields": ["nodes"], "ids": bad})
            valid_edges = set(graph.build_edges(topo))
            if not isinstance(edges, list) or not all(isinstance(e, list) and len(e) == 2 for e in edges):
                raise DuoError("validation", 400, "edges must be a list of [a, b] pairs.", {"fields": ["edges"]})
            norm = []
            for a, b in edges:
                key = (a, b) if (a, b) in valid_edges else (b, a)
                if key not in valid_edges:
                    raise DuoError("validation", 400, f"{[a, b]} is not an edge of the road network.",
                                   {"fields": ["edges"], "edge": [a, b]})
                norm.append(list(key))
            old = (self._doc["blocked_nodes"], self._doc["blocked_edges"])
            self._bump(blocked_nodes=sorted(set(nodes)), blocked_edges=sorted(map(list, {tuple(e) for e in norm})))
            self._events.log("layout", operator=operator, key="blocked", value=self.version, prev=self.version - 1,
                             facts={"old": {"nodes": old[0], "edges": old[1]},
                                    "new": {"nodes": self._doc["blocked_nodes"], "edges": self._doc["blocked_edges"]}},
                             reason="operator changed the admin blocks on the map")
            return self.current()

    # ------------------------------------------------------------------------------ suggestion

    def suggest(self, now_ns: int | None = None) -> dict:
        """Row/column suggestion for every seen floor tag with role `node` (nothing is stored)."""
        now = self._clock.mono_ns() if now_ns is None else now_ns
        snap = self._registry.snapshot()
        pos = {}
        for n in snap.nodes():
            p = self._world.node_position(n.id, now)
            if p is not None:
                pos[n.id] = p
        s = suggest_grid(pos, self._settings.map_rules.suggest.gap_fraction)
        return {"grid_rotation_deg": round(s.rotation_deg, 2), "spacing_mm": round(s.spacing_mm, 1),
                "suggestions": [{"id": i, "grid": list(g)} for i, g in sorted(s.grid.items())],
                "conflicts": [{"ids": list(ids), "grid": list(g)} for ids, g in s.conflicts]}

    # ------------------------------------------------------------------------------ tick and registry changes

    def tick(self, now_ns: int | None = None) -> None:
        """Node-moved detection (map.toml [tracking]): called with every state snapshot."""
        now = self._clock.mono_ns() if now_ns is None else now_ns
        tol = self._settings.map_rules.tracking.node_move_tol_mm
        confirm_ns = int(self._settings.map_rules.tracking.node_move_confirm_s * NS_PER_S)
        with self._lock:
            nodes = self._nodes_grid()
            pos = self._positions(nodes)
            for nid, p in pos.items():
                live = self._world.live_position(nid, now) if p is not None else None
                if live is None:
                    continue                                      # not seen (for example under a car): no change
                dist = ((live[0] - p[0]) ** 2 + (live[1] - p[1]) ** 2) ** 0.5
                if dist > tol:
                    since = self._away_since.setdefault(nid, now)
                    if now - since >= confirm_ns and nid not in self._moved:
                        self._moved.add(nid)
                        self._events.log("layout", key="node_moved", value=nid, prev=None,
                                         facts={"id": nid, "distance_mm": round(dist, 1), "measured": list(p),
                                                "seen": [round(live[0], 1), round(live[1], 1)]},
                                         reason=f"node {nid} was seen {dist:.0f} mm from its measured position for "
                                                f"{self._settings.map_rules.tracking.node_move_confirm_s:g} s: treated as blocked until re-measured")
                else:
                    self._away_since.pop(nid, None)
                    if nid in self._moved:
                        self._moved.discard(nid)
                        self._events.log("layout", key="node_back", value=nid, prev=None,
                                         facts={"id": nid, "distance_mm": round(dist, 1)},
                                         reason=f"node {nid} is back within {tol:g} mm of its measured position")
            for nid in list(self._away_since):
                if nid not in pos:
                    del self._away_since[nid]

    def _on_registry_change(self, old: RegistrySnapshot, new: RegistrySnapshot) -> None:
        """A node's `grid` change makes it unmeasured; grid/station/obstacle changes bump the layout version."""
        def sig(s: RegistrySnapshot) -> dict:
            out: dict[int, Any] = {}
            for t in s.tags.values():
                if isinstance(t.fields, NodeRole):
                    out[t.id] = ("node", t.grid, t.station)
                elif t.role == "obstacle":
                    out[t.id] = ("obstacle", t.fields.radius_mm)
            return out
        a, b = sig(old), sig(new)
        if a == b:
            return
        with self._lock:
            changed_grid = {i for i in set(a) | set(b) if (a.get(i) or (None, None))[:2] != (b.get(i) or (None, None))[:2]
                            and (a.get(i, ("", None))[0] == "node" or b.get(i, ("", None))[0] == "node")}
            positions = {k: v for k, v in self._doc["positions"].items() if int(k) not in changed_grid}
            self._bump(positions=positions)
            self._moved -= changed_grid
