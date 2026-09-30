# DUO-WARE 2 — Roadmap (M2–M8 outline)

Short outlines only. Each milestone gets a full `docs/plans/M<n>.md` (files, interfaces, tests,
acceptance criteria, hardware test) written by Opus after reviewing the previous milestone.
Order changed from the brief: the phone app is M2 and the car link is M3 (DECISIONS.md D19).

| # | Milestone | Main risk it retires |
| --- | --- | --- |
| M1 | Server core, simulator, tag registry, road-network layout, dashboard live map ([M1.md](M1.md)) | architecture and protocols, in simulation |
| M2 | Android marker sensor app v1 ([M2.md](M2.md)): Camera2, C++ ROI tracking, wired/wireless modes, sustained performance | pose age, blur at short exposure, lighting, phone timestamps, heat over hours |
| M3 | Car link manager, both firmwares, one-click connect, manual drive, E-stop, TTL | Bluetooth reliability, Uno/HC-05, brownouts |
| M4 | Venue setup wizard, lens, per-car motion calibration, loop-latency step test | the numbers the controller depends on |
| M5 | Single-car motion on the road network | gyro-less control |
| M6 | Two cars: planning, reservations, deadlock, safety bubble | coordination |
| M7 | Missions, "why?" from the event log, Groq explainer/auditor, timeline | explainability |
| M8 | Hardening | security, recovery, docs |

## M2 — Android marker sensor app v1 (Sonnet, high effort) — plan: [M2.md](M2.md)
- **Starts with** the M1 review fixes F1–F14 (`M1.md`, "Opus review").
- **App:** Camera2 directly with one YUV `ImageReader` at the sensor's maximum fps, manual short exposure/ISO,
  locked focus/AWB, capabilities reported (D30); C++ detection with one JNI call per frame and no per-frame
  allocation (D31); ROI tracking with periodic full scans on the fastest cores (D32); connection modes WIRED
  (USB tethering, adb-reverse TCP fallback) and WIRELESS (Wi-Fi) with discovery in each (D33); foreground
  service, wake lock, sustained performance mode, ADPF hints, battery-optimisation exemption, thermal ladder
  (D34); per-stage timings, CPU, thermal on screen; benchmark mode (D36).
- **Server:** TCP frames listener, `adb reverse` runner, `/api/beacon`, preview endpoint, benchmark storage and
  dashboard views; simulator TCP mode.
- **Hardware test:** discovery and link statistics in all three modes; mode switch stops poses and recovers
  without recalibration; capabilities; allocation-free loop; benchmarks incl. the JNI-overhead baseline; a
  2-hour soak; brief §5 targets and the blur test vs DUO-WARE 1.

## M3 — Car link manager and firmware (Sonnet, medium effort)
- **Needs first:** the Car B answers in `docs/HARDWARE.md` (pins, serial choice, power).
- **Firmware:** `firmware/car_esp32/car_esp32.ino` and `firmware/car_uno/car_uno.ino` per PROTOCOL.md §5
  (fixed 32-byte line buffer, TTL, brake-then-release, `BOOT` reset reason, `X` on expiry, `E` + stop on every
  error, GPIO 12 low at boot on the ESP32; HC-05 AT setup instructions for the Uno).
- **Server:** one thread per link (RFCOMM by MAC, COM fallback, TCP for the simulator), writer queue with `S`
  priority, identity check (`?` → `ID` name/proto), ping/RTT, degraded/reconnect with backoff, parallel
  one-click connect with per-car progress, manual drive (hold-to-drive, `M` refreshed every `refresh_ms`,
  released → `S`), per-car stop, E-stop sends `S` to every car and blocks `M`; `BOOT`/`X`/`E` events;
  `stopped_reason` becomes real, computed by **one** pure function (`safety.stop_reason`) used by both the
  controller's gate and the dashboard, in the PROTOCOL.md §6.2 order, with a table-driven test (M1 review).
  Watch the event-loop lag (`/api/health` `loop_lag_ms`, from M2): the `M` refresh must never miss `refresh_ms`;
  if a floor refit (D37) stalls the loop too long, move it to a worker thread. New `[car_link]` section in `tuning.toml`. The simulator's `ScriptDriver` is
  removed: the server drives the sim cars over TCP.
- **Hardware test:** one-click connect both cars; manual drive; kill the server mid-drive → both cars stop within
  TTL (film at 60 fps, count frames); power-cycle each car → auto-reconnect and `BOOT` reason logged; 10 minutes of
  pings: RTT p50/p95/max per car.

## M4 — Venue setup and calibration (Sonnet, medium effort; Opus designs the calibration moves)
- **Venue wizard** (brief 4.6): place tags → auto-detect → assign roles (bulk) → suggest rows/columns → confirm →
  measure → validate → camera exposure check → save venue preset; loading a preset re-checks it. Target: a
  3 × 3 layout set up in under 5 minutes.
- **Lens:** use Camera2 intrinsics/distortion from `hello` when present, else a ChArUco profile tool (port
  DUO-WARE 1 `vision/lens.py`); corners corrected before the floor fit. With a camera model, compute the true
  nadir and camera height (M1 P10; today the image centre mapped to the floor, up to ~3.5 mm car-pose error).
- **Persist automatically located floor-tag positions** in `state.db`, keyed by the floor-tag signature, so a
  restart can refit from any visible placed tags instead of needing the origin tag (M1 review).
- Dashboard: the Live page fits 800 px without horizontal scrolling; map bounds from nodes and cars, not the whole
  camera footprint (M1 known gap).
- **Per-car motion calibration** (camera-driven, stored per car in `state.db`): minimum PWM and kick per wheel
  direction, trim for straight driving, turn rate vs PWM, coast distance and coast angle after `S`, tag offset
  from the rotation centre (spin in place → circle fit) and heading offset (short straight drive).
- **Loop-latency step test:** command a spin from rest, time until the camera sees the heading change
  (and the optional LED test); per-car distributions.
- **Hardware test:** fit rms per venue; calibration repeatability (3 runs per car); loop latency p50/p95 per car.

## M5 — Single-car motion on the road network (Sonnet, high effort; Opus writes the controller design)
- Edge controller: follow the measured line between two nodes (heading and cross-track), per-edge speed profile
  from the measured length, predictive stop at the node with online coast learning, pulsed final approach.
- Turn controller: turn to the next edge's measured bearing with a predictive stop, then car-timed pulses to ±5°.
- Pose projection over the measured loop delay using commands already sent (DECISIONS.md D12).
- "Go to node" for one car: A* on the 4-connected graph; tests assert every step is one orthogonal edge.
- Stops: stale pose, unsynced/misaligned camera, lost link, E-stop, moved/blocked node ahead.
- **Acceptance:** simulation over uneven, crooked and rotated layouts; hardware: stop-error and turn-error
  distributions over a stated number of runs per car.

## M6 — Two cars (Sonnet, high effort; Opus writes the traffic rules)
- Reservations of nodes and edges (D29), priority with waiting-time aging, deadlock detection (wait-for cycle)
  and orthogonal back-off, safety bubble from camera distance, replanning around blocked/moved nodes.
- Port DUO-WARE 1's `fleet/warehouse` reservation/deadlock tests to the node graph.
- **Acceptance:** long random simulations (≥ 10,000 steps, random layouts and targets): never a double-granted
  node or edge, never a diagonal step, no permanent deadlock; then hardware runs with both cars.

## M7 — Missions, "why?", Groq (Sonnet, medium effort)
- Missions (go to pickup, dwell, drop-off, dwell, home), assignment by distance and availability, queue, cancel.
- Complete event coverage (commands, plans, grants/denials, conflicts, stops with triggers) and `why_stopped`
  (port DUO-WARE 1 `event_log.py`), dashboard timeline with filters and the **Why?** panel.
- Groq explainer/auditor: asynchronous on key events and on demand, rate-limited, model in config, AI text stored
  separately and labelled, verdict flags for the admin, "no explanation available" when Groq fails;
  `test_architecture.py` proves the explainer cannot reach the car link or controller (D26).

## M8 — Hardening (Sonnet, medium effort)
- Access-code login for remote dashboards (port DUO-WARE 1 `auth.py`), throttle, session cookie; E-stop always open.
- Recovery: server restart mid-mission (cars stop by TTL; state reloaded; no node assumed empty), phone
  replaced mid-run, car reboot mid-edge, Bluetooth adapter unplugged.
- Log size limits, docs pass, a final hardware checklist.
