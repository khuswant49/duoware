"""Builds the dashboard `state` snapshot (PROTOCOL.md §6.2) and the per-camera objects of `GET /api/cameras`."""

from typing import TYPE_CHECKING, Any

from duoware import PROTOCOL_VERSION, __version__
from duoware.ingest.sessions import Session

if TYPE_CHECKING:
    from duoware.services import Services

NS_PER_MS = 1_000_000


def _r(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(v, nd)


def _pct(stage) -> dict | None:
    return None if stage is None else {"p50": stage.p50, "p95": stage.p95}


def camera_state(sv: "Services", cam: int, now_ns: int, world) -> dict[str, Any]:
    """One `state.cameras[]` object. Everything the phone did not report is `null` (never invented)."""
    s: Session | None = sv.sessions.by_cam(cam)
    calib = world.cameras.get(cam)
    st = s.last_status if s else None
    stats = sv.sessions.camera_stats(cam)
    synced = bool(s and s.clock_sync.ok(now_ns))
    link_obj = None
    if s is not None:
        link = st.link if st and st.link else s.hello.link
        ls = s.link_stats.summary()
        link_obj = {"mode": link.mode, "transport": s.transport, "interface": link.interface,
                    "wifi": None if link.wifi is None else link.wifi.to_dict(),
                    "latency_ms": {"p50": _r(ls.latency_p50_ms, 2), "p95": _r(ls.latency_p95_ms, 2)},
                    "jitter_ms": _r(ls.jitter_ms, 2), "loss_pct": _r(ls.loss_pct, 2),
                    "send_dropped": st.send_dropped if st else None}
    age = stats.pose_age()
    stages = None
    if st is not None:
        stages = {n: _pct(st.stages_ms.get(n)) for n in ("pipeline", "detect_full", "detect_roi", "send", "cap_to_sent")}
    calib_info = calib.calib if calib else {"status": "UNCALIBRATED", "model": None, "rms_mm": None,
                                            "residual_mm": None, "floor_tags_used": [], "floor_tags_seen": []}
    caps = sv.sessions.caps(cam)
    th = st.thermal if st else None
    return {
        "cam": cam, "online": s is not None, "model": (s.hello.model if s else None), "app_mode": st.app_mode if st else None,
        "link": link_obj,
        "sync": {"ok": synced, "rtt_ms": _r(s.clock_sync.rtt_ms_min, 2) if s else None,
                 "samples": s.clock_sync.samples if s else 0,
                 "rejected": s.clock_sync.rejected(now_ns) if s else 0},
        "calib": calib_info,
        "fps": _r(st.fps, 1) if st else None, "fps_target": st.fps_target if st else None,
        "resolution": list(st.resolution) if st and st.resolution else None, "stages_ms": stages,
        "pose_age_ms": {"p50": _r(age.p50) if synced else None, "p95": _r(age.p95) if synced else None,
                        "max": _r(age.max) if synced else None, "n": age.n if synced else 0},
        "dropped": stats.dropped, "rx_bad": stats.rx_bad, "rx_unauth": stats.rx_unauth, "rx_late": stats.rx_late,
        "rx_version": stats.rx_version, "rx_bad_marker": stats.rx_bad_marker,
        "cpu_app_pct": st.cpu_app_pct if st else None,
        "thermal": None if th is None else {"status": th.status, "headroom": th.headroom, "level": th.level, "reason": th.reason},
        "exposure_ms": _r(st.exposure_ns / NS_PER_MS, 2) if st and st.exposure_ns else None,
        "iso": st.iso if st else None,
        "preview_seq": sv.previews[cam].seq if cam in sv.previews else None,
        "status_age_ms": _r((now_ns - s.status_at_ns) / NS_PER_MS) if s and s.status_at_ns else None,
        "clock": st.clock if st else None,
        "frame_age_ms": _r((now_ns - s.last_frame_ns) / NS_PER_MS) if s and s.last_frame_ns else None,
        "processing_on": list(st.processing_on) if st and st.processing_on is not None else None,
        "caps": caps,
    }


def build_state(sv: "Services", now_ns: int, seq: int) -> dict[str, Any]:
    world = sv.world.snapshot(now_ns)
    reg = sv.registry.snapshot()
    cams = [camera_state(sv, cam, now_ns, world) for cam in sorted(set(sv.sessions.known_cams()) | set(world.cameras))]
    for c in cams:
        c.pop("caps", None)
    tags = []
    for tid in sorted(set(world.tags) | set(reg.tags)):
        o, doc = world.tags.get(tid), reg.get(tid)
        ft = sv.floor.get(tid)
        tags.append({"id": tid, "role": doc.role if doc else "unassigned", "label": doc.label if doc else None,
                     "seen": bool(o and o.seen), "cams": list(o.cams) if o else [],
                     "x_mm": _r(o.x) if o else None, "y_mm": _r(o.y) if o else None,
                     "heading_deg": _r(o.heading) if o else None,
                     "age_ms": _r((now_ns - o.capture_ns) / NS_PER_MS) if o and o.capture_ns else None,
                     "size_mm": _r(o.size_mm) if o else None, "size_warn": bool(o and o.size_warn),
                     "placed": None if ft is None else ft.placed})
    cars = []
    for c in sv.settings.cars:
        doc = reg.car_tag(c.name)
        obs = world.cars.get(c.name)
        pose = None
        if obs is not None and doc is not None:
            pose = {"x_mm": _r(obs.x), "y_mm": _r(obs.y), "heading_deg": _r(obs.heading), "age_ms": _r(obs.age_ms),
                    "fresh": obs.fresh, "at_node": sv.layout.node_at(obs.x, obs.y),
                    "usable_for_control": obs.usable_for_control}
        cars.append({"name": c.name, "tag": doc.id if doc else None, "color": c.color,
                     "link": {"state": "disconnected", "rtt_ms": None, "fw": None}, "pose": pose, "motion": None,
                     "stopped_reason": "estop" if sv.safety.estop else "no_link", "battery": "not_measured"})
    sf = sv.safety
    return {"v": PROTOCOL_VERSION, "t": "state", "seq": seq, "wall_ms": sv.clock.wall_ms(),
            "system": {"estop": sf.estop, "estop_since_wall_ms": sf.estop_since_wall_ms, "estop_by": sf.estop_by,
                       "mode": sv.mode, "registry_version": reg.version, "layout_version": sv.layout.version,
                       "events_last_id": sv.events.last_id},
            "cameras": cams, "tags": tags, "cars": cars}


def hello_msg(sv: "Services") -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "t": "hello", "server_id": sv.db.server_id, "version": __version__,
            "mode": sv.mode, "state_hz": sv.settings.server.dashboard.state_hz}
