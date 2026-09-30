"""Phone sessions and pairing (PROTOCOL.md §1, §4.3, DECISIONS.md D6, D35).

One live session per camera. A session owns its clock-sync estimator and link statistics (a new path is a new
measurement, D35); the camera's statistics history and calibration belong to `cam` and survive sessions.
"""

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from duoware.clock import Clock
from duoware.ingest.auth import LoginThrottle, codes_match, generate_code
from duoware.ingest.clocksync import ClockSync
from duoware.ingest.stats import CameraStats, LinkStats
from duoware.protocol.phone import Hello, PhoneError, Status
from duoware.settings import Settings
from duoware.store.db import StateDb
from duoware.store.events import EventLog

SID_BYTES = 8                # PROTOCOL.md §1: sid is 16 lowercase hex characters
TOKEN_BYTES = 16             # PROTOCOL.md §4.3: token is 32 hex characters
CAM_ID_RANGE = range(1, 16)  # PROTOCOL.md §1: camera IDs 1-15
STATUS_SAMPLE_S = 60         # PROTOCOL.md §4.5: a status sample every 60 s goes to the event log
NS_PER_S = 1_000_000_000


@dataclass
class Session:
    cam: int
    sid: str
    device_id: str
    peer_ip: str
    opened_ns: int
    hello: Hello
    clock_sync: ClockSync
    stats: CameraStats
    link_stats: LinkStats
    transport: str = "udp"                       # "udp" or "tcp": the transport of the latest accepted frame (PROTOCOL.md §6.2)
    last_status: Status | None = None
    status_at_ns: int | None = None
    frame_addr: tuple[str, int] | None = None    # UDP only: source of the latest frame
    reply: Callable[[bytes], None] | None = None  # where `sync` requests go: set by the latest accepted frame
                                                  # (UDP: sendto its source; TCP: a framed write on its connection)
    last_seq: int | None = None
    sync_sent: dict[int, int] = field(default_factory=dict)      # n -> t1 of requests sent in the last 2 s
    sync_n: int = 0
    synced_before: bool = False
    last_sample_ns: int = 0

    @property
    def link_mode(self) -> str:
        return (self.last_status.link.mode if self.last_status and self.last_status.link else self.hello.link.mode)

    @property
    def app_mode(self) -> str | None:
        return self.last_status.app_mode if self.last_status else None


@dataclass(frozen=True)
class Opened:
    session: Session
    token: str | None            # present only when newly issued (PROTOCOL.md §4.3)
    replaced: Session | None     # the old session of the same device: the handler closes it with 4004


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PhoneSessions:
    def __init__(self, db: StateDb, settings: Settings, events: EventLog, clock: Clock) -> None:
        self._db, self._settings, self._events, self._clock = db, settings, events, clock
        acc = settings.server.access
        self.throttle = LoginThrottle(acc.pair_max_failures, acc.pair_lock_s, clock)
        self.pair_code = settings.env.get("DUO_PAIR_CODE") or generate_code()
        self._by_sid: dict[str, Session] = {}
        self._by_device: dict[str, Session] = {}
        self._cam_stats: dict[int, CameraStats] = {}
        self.unknown_rx = 0                      # datagrams that belong to no live session
        self.on_opened: list[Callable[[Session], None]] = []
        self.on_closed: list[Callable[[Session], None]] = []

    # ------------------------------------------------------------------------------ lookup

    def camera_stats(self, cam: int) -> CameraStats:
        if cam not in self._cam_stats:
            self._cam_stats[cam] = CameraStats(self._settings.tuning.pose.stats_window_s, self._clock)
        return self._cam_stats[cam]

    def by_sid(self, sid: str) -> Session | None:
        return self._by_sid.get(sid)

    def by_cam(self, cam: int) -> Session | None:
        return next((s for s in self._by_sid.values() if s.cam == cam), None)

    def live(self) -> list[Session]:
        return sorted(self._by_sid.values(), key=lambda s: s.cam)

    def known_cams(self) -> list[int]:
        """Every camera that has ever paired or is live (the dashboard lists them all)."""
        rows = {r["cam"] for r in self._db.query("SELECT cam FROM pairings")}
        return sorted(rows | {s.cam for s in self._by_sid.values()})

    def devices(self) -> list[dict]:
        return [{"device_id": r["device_id"], "cam": r["cam"], "model": r["model"],
                 "last_seen_wall_ms": r["last_seen_wall_ms"]}
                for r in self._db.query("SELECT * FROM pairings ORDER BY cam")]

    def caps(self, cam: int) -> dict | None:
        return self._db.get_json("SELECT doc FROM camera_caps WHERE cam = ?", (cam,))

    # ------------------------------------------------------------------------------ open / close

    def open(self, hello: Hello, peer_ip: str) -> Opened | PhoneError:
        """PROTOCOL.md §4.3 server decision. Version checking is the caller's job (it has the raw `v`)."""
        locked = self.throttle.locked_for_s(peer_ip)
        if locked > 0:
            return PhoneError("locked", f"Too many wrong codes. Try again in {locked:.0f} s.")
        row = self._db.query("SELECT * FROM pairings WHERE device_id = ?", (hello.device_id,))
        token_ok = bool(row) and bool(hello.token) and hmac.compare_digest(row[0]["token_sha256"], _hash(hello.token))
        new_token: str | None = None
        if not token_ok:
            if hello.pair_code is None or not codes_match(hello.pair_code, self.pair_code):
                self.throttle.failure(peer_ip)
                self._events.log("operator", key="pairing", value="rejected", facts={"ip": peer_ip, "device_id": hello.device_id},
                                 reason="wrong pairing code" if hello.pair_code is not None else "invalid token")
                if hello.pair_code is None:
                    return PhoneError("bad_token", "The saved pairing is not valid. Enter the pairing code.")
                return PhoneError("bad_pair_code", "Pairing code is wrong or expired.")
            new_token = secrets.token_hex(TOKEN_BYTES)
        self.throttle.success(peer_ip)
        now_wall = self._clock.wall_ms()
        with self._db.tx() as c:
            if row:
                cam = row[0]["cam"]
                if new_token:
                    c.execute("UPDATE pairings SET token_sha256 = ?, model = ? WHERE device_id = ?",
                              (_hash(new_token), hello.model, hello.device_id))
                c.execute("UPDATE pairings SET last_seen_wall_ms = ?, model = ? WHERE device_id = ?",
                          (now_wall, hello.model, hello.device_id))
            else:
                used = {r[0] for r in c.execute("SELECT cam FROM pairings")}
                free = [i for i in CAM_ID_RANGE if i not in used]
                if not free:
                    return PhoneError("bad_message", "All camera slots are taken; revoke a device first.")
                cam = free[0]
                c.execute("INSERT INTO pairings VALUES (?,?,?,?,?,?)",
                          (hello.device_id, cam, _hash(new_token or ""), hello.model, now_wall, now_wall))
            c.execute("INSERT INTO camera_caps VALUES (?,?,?) ON CONFLICT(cam) DO UPDATE SET doc = excluded.doc, "
                      "updated_wall_ms = excluded.updated_wall_ms",
                      (cam, json.dumps(self._caps_doc(hello)), now_wall))
        if new_token:
            self._events.log("operator", key="pairing", value="paired", car=None,
                             facts={"device_id": hello.device_id, "cam": cam, "model": hello.model, "ip": peer_ip},
                             reason="phone entered the pairing code")
        stats = self.camera_stats(cam)
        sess = Session(cam, secrets.token_hex(SID_BYTES), hello.device_id, peer_ip, self._clock.mono_ns(), hello,
                       ClockSync(self._settings.tuning.clock_sync), stats,
                       LinkStats(self._settings.tuning.link.stats_window_s, self._clock),
                       last_sample_ns=self._clock.mono_ns())
        old = self._by_device.get(hello.device_id)
        if old is not None:
            self._drop(old, "replaced by a newer session of the same device", announce=False)
        self._by_sid[sess.sid] = sess
        self._by_device[hello.device_id] = sess
        prev_mode = self._db.get_meta(f"cam{cam}_link_mode")
        self._db.set_meta(f"cam{cam}_link_mode", hello.link.mode)
        self._events.log("camera", key="online", value="true", prev="false" if old is None else "true",
                         facts={"cam": cam, "sid": sess.sid, "link_mode": hello.link.mode, "model": hello.model,
                                "interface": hello.link.interface, "ip": peer_ip},
                         reason="phone session opened" + (" (replacing the previous one)" if old else ""))
        if prev_mode is not None and prev_mode != hello.link.mode:
            self._events.log("camera", key="link_mode", value=hello.link.mode, prev=prev_mode,
                             facts={"cam": cam, "interface": hello.link.interface},
                             reason="the phone reconnected over a different connection mode")
        for cb in self.on_opened:
            cb(sess)
        return Opened(sess, new_token, old)

    @staticmethod
    def _caps_doc(h: Hello) -> dict:
        from duoware.protocol.phone import encode_hello
        d = json.loads(encode_hello(h))
        return {"sdk": d["sdk"], "cpu": d["cpu"], "camera": d["camera"]}

    def _drop(self, s: Session, reason: str, announce: bool = True) -> None:
        self._by_sid.pop(s.sid, None)
        if self._by_device.get(s.device_id) is s:
            del self._by_device[s.device_id]
        for cb in self.on_closed:
            cb(s)
        if announce:
            self._events.log("camera", key="online", value="false", prev="true",
                             facts={"cam": s.cam, "sid": s.sid}, reason=reason)

    def close(self, sid: str, reason: str = "phone session ended") -> Session | None:
        s = self._by_sid.get(sid)
        if s is not None:
            self._drop(s, reason)
        return s

    def revoke(self, device_id: str, operator: str = "local") -> Session | None:
        """Deletes the pairing; returns the live session (if any) for the handler to close."""
        with self._db.tx() as c:
            c.execute("DELETE FROM pairings WHERE device_id = ?", (device_id,))
        s = self._by_device.get(device_id)
        self._events.log("operator", operator=operator, key="pairing", value="revoked", facts={"device_id": device_id},
                         reason="operator revoked the device on the dashboard")
        return s

    # ------------------------------------------------------------------------------ status and monitoring

    def on_status(self, s: Session, st: Status) -> None:
        """Stores the latest `status` and logs the changes PROTOCOL.md §4.5 names."""
        prev = s.last_status
        s.last_status, s.status_at_ns = st, self._clock.mono_ns()
        old_mode = prev.link.mode if prev and prev.link else s.hello.link.mode
        new_mode = st.link.mode if st.link else old_mode
        if new_mode != old_mode:
            self._events.log("camera", key="link_mode", value=new_mode, prev=old_mode, facts={"cam": s.cam},
                             reason="phone reported a different connection mode")
        if prev is not None and prev.app_mode != st.app_mode:
            self._events.log("camera", key="app_mode", value=st.app_mode, prev=prev.app_mode, facts={"cam": s.cam},
                             reason="the phone app changed mode")
        elif prev is None and st.app_mode != "tracking":
            self._events.log("camera", key="app_mode", value=st.app_mode, facts={"cam": s.cam},
                             reason="the phone app is not in tracking mode")
        old_lvl = prev.thermal.level if prev and prev.thermal else None
        new_lvl = st.thermal.level if st.thermal else None
        if new_lvl is not None and new_lvl != old_lvl and (old_lvl is not None or new_lvl > 0):
            self._events.log("camera", key="thermal_level", value=new_lvl, prev=old_lvl,
                             facts={"cam": s.cam, "headroom": st.thermal.headroom, "status": st.thermal.status},
                             reason=st.thermal.reason or "thermal ladder level changed")

    def tick(self) -> None:
        """Called about once a second: sync ok/lost events and the 60 s status sample."""
        now = self._clock.mono_ns()
        for s in self._by_sid.values():
            ok = s.clock_sync.ok(now)
            if ok != s.synced_before:
                self._events.log("camera", key="sync", value="ok" if ok else "lost", prev="lost" if ok else "ok",
                                 facts={"cam": s.cam, "rtt_ms": s.clock_sync.rtt_ms_min, "samples": s.clock_sync.samples},
                                 reason="clock sync established" if ok else "no accepted sync sample for too long")
                s.synced_before = ok
            if s.last_status is not None and now - s.last_sample_ns >= STATUS_SAMPLE_S * NS_PER_S:
                s.last_sample_ns = now
                st = s.last_status
                self._events.log("camera", key="status_sample", value=st.app_mode,
                                 facts={"cam": s.cam, "fps": st.fps, "cpu_app_pct": st.cpu_app_pct,
                                        "thermal": None if st.thermal is None else {"status": st.thermal.status,
                                                                                     "headroom": st.thermal.headroom,
                                                                                     "level": st.thermal.level},
                                        "send_dropped": st.send_dropped, "markers_seen": st.markers_seen},
                                 reason="periodic status sample")

    def touch(self, s: Session) -> None:
        with self._db.tx() as c:
            c.execute("UPDATE pairings SET last_seen_wall_ms = ? WHERE device_id = ?", (self._clock.wall_ms(), s.device_id))

