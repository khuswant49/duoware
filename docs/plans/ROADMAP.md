# DUO-WARE 2 — Roadmap (M2–M8 outline)

Short outlines only. Each milestone gets a full `docs/plans/M<n>.md` (files, interfaces, tests,
acceptance criteria, hardware test) written by Opus after reviewing the previous milestone.
Order changed from the brief: the phone app is M2 and the car link is M3 (DECISIONS.md D19).

| # | Milestone | Main risk it retires |
| --- | --- | --- |
| M1 | Server core, simulator, tag registry, road-network layout, dashboard live map ([M1.md](M1.md)) | architecture and protocols, in simulation |
| M2 | Android marker sensor app v1 | pose age, blur at short exposure, lighting, phone timestamps |
| M3 | Car link manager, both firmwares, one-click connect, manual drive, E-stop, TTL | Bluetooth reliability, Uno/HC-05, brownouts |
| M4 | Venue setup wizard, lens, per-car motion calibration, loop-latency step test | the numbers the controller depends on |
| M5 | Single-car motion on the road network | gyro-less control |
| M6 | Two cars: planning, reservations, deadlock, safety bubble | coordination |
| M7 | Missions, "why?" from the event log, Groq explainer/auditor, timeline | explainability |
| M8 | Hardening | security, recovery, docs |

## M2 — Android marker sensor app v1 (Sonnet, high effort)
- **Build:** Kotlin app per PROTOCOL.md §2–4: CameraX `ImageAnalysis` (`KEEP_ONLY_LATEST`, Y plane only),
  Camera2 interop for manual exposure/ISO, focus lock and white-balance lock (fallback when `MANUAL_SENSOR` is
  missing: AE lock at minimum exposure compensation, reported in `status`); OpenCV `ArucoDetector`
  (DICT_4X4_50, sub-pixel corner refinement); UDP sender thread (queue depth 1); sync-clock detection (§3.1)
  and sync replies; beacon listener, pairing-code entry, token storage, WebSocket session; 1 Hz status;
  preview thread (downscaled JPEG, never on the tracking thread); on-screen fps, detection ms, markers,
  exposure/ISO, link state, thermal warning; keep screen on; landscape.
- **Server:** `GET /api/cameras/{cam}/preview.jpg`; dashboard preview panel; camera-settings controls.
- **Hardware test (owner):** over USB tethering and WiFi: fps and detection ms with 2 car tags and 9 floor tags in
  view; server pose age p50/p95/max over 5 minutes; timestamp source and Camera2 level; clean-read ratio of a
  car tag pushed by hand at < 150, 150–300 and > 300 px/s at 3 ms exposure vs DUO-WARE 1's numbers; image
  brightness/ISO needed in the room (decides whether lamps are needed).
- **Acceptance:** targets from brief §5 are measured and logged in HARDWARE_LOG.md (met or not); tracking ≥ 30 fps
  or the reason it is not; no JPEG on the tracking path (code review + profiler trace).

## M3 — Car link manager and firmware (Sonnet, medium effort)
- **Needs first:** the Car B answers in `docs/HARDWARE.md` (pins, serial choice, power).
- **Firmware:** `firmware/car_esp32/car_esp32.ino` and `firmware/car_uno/car_uno.ino` per PROTOCOL.md §5
  (fixed 32-byte line buffer, TTL, brake-then-release, `BOOT` reset reason, `X` on expiry, `E` + stop on every
  error, GPIO 12 low at boot on the ESP32; HC-05 AT setup instructions for the Uno).
- **Server:** one thread per link (RFCOMM by MAC, COM fallback, TCP for the simulator), writer queue with `S`
  priority, identity check (`?` → `ID` name/proto), ping/RTT, degraded/reconnect with backoff, parallel
  one-click connect with per-car progress, manual drive (hold-to-drive, `M` refreshed every `refresh_ms`,
  released → `S`), per-car stop, E-stop sends `S` to every car and blocks `M`; `BOOT`/`X`/`E` events;
  `stopped_reason` becomes real. New `[car_link]` section in `tuning.toml`. The simulator's `ScriptDriver` is
  removed: the server drives the sim cars over TCP.
- **Hardware test:** one-click connect both cars; manual drive; kill the server mid-drive → both cars stop within
  TTL (film at 60 fps, count frames); power-cycle each car → auto-reconnect and `BOOT` reason logged; 10 minutes of
  pings: RTT p50/p95/max per car.

## M4 — Venue setup and calibration (Sonnet, medium effort; Opus designs the calibration moves)
- **Venue wizard** (brief 4.6): place tags → auto-detect → assign roles (bulk) → suggest rows/columns → confirm →
  measure → validate → camera exposure check → save venue preset; loading a preset re-checks it. Target: a
  3 × 3 layout set up in under 5 minutes.
- **Lens:** use Camera2 intrinsics/distortion from `hello` when present, else a ChArUco profile tool (port
  DUO-WARE 1 `vision/lens.py`); corners corrected before the floor fit.
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
