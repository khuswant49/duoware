# DUO-WARE 2 — Project Brief

Written 2026-10-01. This is the requirements and design-direction document for a rebuild of DUO-WARE.
Put it in the new repository as `docs/PROJECT_BRIEF.md`. It states **what** to build and the decisions
already made; the architect session turns it into the protocol, plans and code structure.

---

## 1. Goal

Two small cars drive on a warehouse floor grid under one overhead Android phone camera. A laptop
server plans their routes, keeps them from colliding, and drives them. An admin watches and controls
everything from a web dashboard. An LLM (Groq) only **explains** what the cars did; it never controls them.

The first version (DUO-WARE 1, in `C:\Users\khusw\Downloads\Duo_Ware\Duo_Ware`) streamed JPEG video
from a phone browser page / IP-camera app to the laptop, which detected markers there. The delay and
motion blur of that path is the main reason for this rebuild.

## 2. Fixed decisions (from the owner)

| Topic | Decision |
| --- | --- |
| Tracking | A **custom Android app** detects ArUco markers **on the phone** and sends marker data over UDP. No video on the tracking path. |
| Phone link | Two modes chosen in the app, same protocol (added 2026-10-01): **WIRED** (USB tethering; adb-reverse TCP fallback) and **WIRELESS** (Wi-Fi: router, laptop hotspot or phone hotspot). See 4.1. |
| Phone performance | Maximum **sustained** tracking performance for multi-hour demos, not a peak (added 2026-10-01). See 4.1. |
| Cameras | **One** overhead phone now. Design so more phones can be added later (every packet carries a camera ID). |
| Car A | **ESP32**, Bluetooth Classic SPP (`BluetoothSerial`). |
| Car B | **Arduino Uno clone** (CH340 USB chip) + **HC-05** Bluetooth module. |
| Gyros / IMUs | **None.** Both MPUs are dead and **will not be replaced.** All turning and heading control is done by the laptop from camera heading. |
| Wheel encoders | None (assumed — confirm). |
| Connect | **One click** on the dashboard connects both cars with no manual port picking. |
| AI | **Groq** API only. It explains and audits decisions from the log. It has **no** path to send commands. |
| Tag roles | The **admin assigns each ArUco tag's role on the dashboard** (car, station, floor anchor, …). Nothing about tag roles is hard-coded. |
| RFID / NFC | Out of scope for now. |
| Dashboard | Architect's choice (recommended below: React + Vite + TypeScript). |
| Dev setup | Windows 11 laptop, Python 3.13 available, Node.js installed, Android Studio installed, Android phone with USB debugging. |

## 3. Hardware facts and open items

**Car A (ESP32)** — wiring taken from the DUO-WARE 1 firmware `DUO_WARE_R2.ino`, which the owner confirmed
is the real wiring (older docs listing other pins are wrong):
L298N `ENA 27, IN1 26, IN2 12, ENB 25, IN3 13, IN4 14`, left/right channels swapped (`SWAP_SIDES = true`).
GPIO 12 is a boot strapping pin: keep it LOW at boot. The MPU wiring (SDA 22 / SCL 21) is now unused.

**Car B (Uno + HC-05)** — **open items, ask the owner:** motor driver model (L298N?), which pins are used,
power supply. Constraints to respect:
- HC-05 on `SoftwareSerial` at **38400 baud** (115200 on SoftwareSerial is unreliable). Set the baud and
  the name `DUO-B` once with AT commands. HC-05 RX needs a 5 V → 3.3 V divider.
- Uno PWM pins are 3, 5, 6, 9, 10, 11; don't put SoftwareSerial or motor PWM on conflicting timers.
- 2 KB RAM: no `String` buffers that grow without bound; fixed-size line buffer.

**Both cars:** 2 DC motors, differential drive, no IMU, no encoders, no battery sensing (show battery as
"not measured" — never a made-up number). They are different hardware and will not drive alike.

**Markers (DICT_4X4_50, IDs 0–49).** Roles are assigned by the admin on the dashboard (section 4.5).
The table below is only the **suggested first setup**, matching the printed DUO-WARE 1 tag kit; it may be
offered as a one-click preset but must not be assumed anywhere in code:

| IDs | Suggested role |
| --- | --- |
| 1 | Car A (existing 80 mm tag, TOP arrow = car front) |
| 5 | Car B (new tag, print at 80 mm) |
| 2, 3, 4 | Stations: pickup / home / drop-off (existing 90 mm tags) |
| 10–13 | Floor anchors for calibration (already printed, 90 mm) |

## 4. System architecture (direction; the architect finalizes)

```
Android phone app                   Laptop server (Python, asyncio)                  Cars
 CameraX + Camera2 controls   UDP    ┌──────────────────────────────────────┐  BT SPP   ESP32 "DUO-A"
 short exposure, locked AF/AE ─────► │ pose receiver → localization → world │ ◄──────►  Uno+HC-05 "DUO-B"
 ArUco detect on gray plane          │ state → planner/reservations →       │  text line protocol
 send marker corners + capture time  │ per-car motion controller → car link │  with command TTL
 low-rate preview over WebSocket ──► │ event log → Groq explainer (async)   │
                                     └──────────────┬───────────────────────┘
                                                    │ WebSocket (state 20–30 Hz) + REST
                                             Web dashboard (React + Vite + TS)
```

### 4.1 Phone app = a fast marker sensor, nothing more
- Kotlin app; the camera and detection pipeline follow the performance requirements below (the
  architect chose Camera2 directly and a C++ detection loop: DECISIONS.md D30, D31). Read the
  **Y (gray) plane directly**; never JPEG-encode on the tracking path. OpenCV `ArucoDetector`.
- **Exposure control is the key feature**: via Camera2 interop set a short exposure (start ~2–4 ms,
  configurable), raise ISO, lock focus and white balance. Motion blur was the measured limit in DUO-WARE 1
  (clean car-tag reads: 100% below 150 px/s, 70–76% at 150–300 px/s, 15–44% above 300 px/s).
- Per frame, send **one UDP packet** with every detected marker: ID and the 4 **pixel corners**, plus the
  **sensor capture timestamp**, frame sequence number, camera ID, and processing time.
- **Recommended: the phone does not compute floor coordinates.** Send pixel corners; the server does the
  image→floor fit, heading, grid and (later) multi-camera fusion. The packet stays small, all tuning
  lives in Python (easy to change and test), and adding phones later needs no app changes.
  (The earlier advice said "phone builds the grid"; this deliberately moves that to the server.)
- **Clock sync:** a small UDP ping/echo between server and phone so the server can convert capture time
  to its own clock and know each pose's true age.
- **Discovery:** the server broadcasts a UDP beacon; the app finds the server with no IP typing.
  A pairing code (entered once, stored) authenticates the phone.
- A **separate** low-rate preview (2–5 fps, small JPEG) over WebSocket for the dashboard. It must never
  slow the tracking path.
- On-screen: fps, detection ms, markers seen, exposure/ISO, link state. Keep the screen on; warn on
  thermal throttling.
- **Connection modes** (owner requirement, 2026-10-01), chosen in the app, both using the same protocol:
  - **WIRED:** USB tethering (the phone shares a network over the USB cable; UDP works unchanged and the
    phone charges). If tethering isn't available, fall back to `adb reverse` over TCP (framing defined in
    `PROTOCOL.md`).
  - **WIRELESS:** Wi-Fi: same router, laptop hotspot or phone hotspot. 5 GHz is fine (the cars use Bluetooth).
  - The server listens on all interfaces; discovery works in both modes; the dashboard shows the active mode
    with its measured latency, jitter and packet loss. If the link drops or the mode is switched, the
    stale-pose watchdog stops the cars until fresh poses arrive again.
- **Sustained maximum performance** (owner requirement, 2026-10-01), for a multi-hour demo, not a peak:
  - Camera2 directly (or CameraX with full Camera2 interop; architect's choice with reasons): the highest
    frame rate the sensor supports through an `ImageReader` at the chosen resolution, manual short
    exposure/ISO, locked focus and white balance. The camera's capabilities (hardware level, fps ranges,
    resolutions) are reported to the server at startup.
  - Zero-copy: read the Y plane directly, reuse buffers, no per-frame allocations; run the detection loop in
    C++ (NDK + OpenCV) if the JNI overhead measures significant.
  - Region-of-interest tracking: every frame, detect car tags only in small windows around their predicted
    positions; a full-frame scan every N frames (and whenever a car is lost) finds new or moved tags. Spread
    the work over the CPU's cores.
  - Android performance features: foreground service, wake lock, keep screen on, sustained performance mode
    where supported, performance hints (API 31+), and ask the user to exclude the app from battery optimisation.
  - Thermal management: watch thermal status and headroom and scale down gracefully (fps or resolution)
    before the phone throttles on its own; report it to the dashboard.
  - The app shows per-stage timings (capture, detect, send), fps, CPU load and thermal status, and has a
    benchmark mode to compare settings.

### 4.2 Server
- Python 3.13, `asyncio`. Recommended: **FastAPI + uvicorn** (REST + native WebSockets), an asyncio UDP
  endpoint for poses, one task per car link. (Flask-SocketIO also works; asyncio is simpler for streams.)
- **Units and axes (same as DUO-WARE 1):** floor millimetres, +x right, +y down the map, angles in degrees
  clockwise, heading read from the tag's bottom edge → top edge.
- **Localization:** fit image→floor from anchor tags at known positions (config). Keep DUO-WARE 1's
  measured rules: 1 anchor → similarity, 2 → affine, ≥3 → homography with RANSAC. Average anchor corners
  over steady frames before fitting. Keep checking anchors while running; if the residual jumps
  (phone bumped), mark the camera MISALIGNED and stop autonomous motion until refit.
  Optional per-phone lens profile later (DUO-WARE 1 `vision/lens.py`).
- **Pose freshness:** a car pose older than **300 ms** (by capture time) → that car is stopped.
- **Grid = road network of node markers (owner requirement, 2026-10-01):** floor tags are placed at grid
  nodes (e.g. 3 × 3). Cars move **only up, down, left or right** between orthogonally adjacent nodes, along
  straight segments, turning only **in place at a node**. **No diagonal moves anywhere**: not in planning,
  deadlock back-off, recovery, manual "go to", or the controller. Details in section 4.6.
- **Motion control without a gyro** (the hardest part; architect designs it, with a simulator first):
  - The laptop closes the loop from camera heading at pose rate (~30 Hz). All latency lands in the loop,
    so speeds stay low and stopping is predictive.
  - In-place turn: spin, and command the stop when the remaining angle ≤ turn rate × (measured latency +
    learned coast time); then short pulses with a fresh-pose check until within tolerance (e.g. ±5°).
  - Straight run: low-speed differential steering on heading error and cross-track error to the cell line;
    predictive stop at the cell centre, learning each stop's overshoot (the DUO-WARE 1 navigator's
    brake-learning idea, `control/navigator.py`).
  - **Per-car calibration, stored per car:** minimum PWM that moves it, left/right trim, turn rate vs PWM,
    coast distance/angle. Measured automatically by a camera-driven calibration routine.
- **Planning and traffic (two cars):** A* on the grid. Each car must **reserve** its next 1–2 cells before
  entering; a cell is never granted to two cars (test this every simulation step). When both want the same
  cell, an auction/priority rule decides. Detect deadlock (wait-for cycle) and resolve it (lower priority
  replans or backs off to a free neighbour). **Safety bubble:** if the camera sees the cars closer than a
  set distance, stop the lower-priority car (both if very close), whatever the plan says.
- **Missions:** tasks like "car X: go to pickup, dwell, go to drop-off, dwell, return home", assigned by
  auction (distance + availability), queued, cancellable.
- **Car link manager (one-click connect):**
  - `cars.json` lists each car: name, Bluetooth MAC, tag ID, protocol, optional COM-port fallback.
  - Connect **by MAC with RFCOMM sockets** (`socket.AF_BLUETOOTH`, `BTPROTO_RFCOMM`, works in Python on
    Windows), both cars **in parallel** with timeouts. On connect, send `?`; the car answers with its ID,
    so the server always knows which car is which. Auto-reconnect with backoff. Pairing in Windows is a
    one-time manual step (HC-05 PIN usually 1234).
  - **One writer per car link**: every command goes through that link's queue; STOP jumps the queue.
  - Never scan random ports, never toggle the Windows Bluetooth radio (DUO-WARE 1 did; it caused trouble).
- **Event log (the record of truth):** every command sent, plan, reservation grant/deny, conflict
  decision, stop and its trigger, mode change and operator action, per car, with **measured facts kept
  separate from the rule/reason** that caused it (DUO-WARE 1 `control/event_log.py`). SQLite or JSONL.
  Replayable: "why did car B stop at 14:02:31?" must be answerable from the log alone.
- **AI explainer (Groq):**
  - Runs **asynchronously** on key events (stop, reroute, conflict, deadlock, mission end, fault) and on
    demand ("Why?" button), never per command. Rate-limited; model name in config.
  - Input: the relevant log slice. Output: plain-language explanation and an **audit verdict**
    ("consistent with the rules" / "questionable, because …"), stored next to the event IDs and
    clearly labelled as AI text.
  - Questionable verdicts become **flags for the admin**. The AI module has no import path to the car link
    or controller; enforce this with a test.
  - When Groq is unavailable, show "no explanation available" — never a fake or rule-based text
    labelled as AI.
- **Safety (lessons from the DUO-WARE 1 review):**
  - **Latched E-stop, server-authoritative.** Separate **STOP ALL** and **RESUME** controls (resume asks for
    confirmation); never a toggle; the dashboard shows the server's state. Per-car stop too.
  - Car-side **command TTL**: a car stops by itself when its last motion command expires.
  - Stops on: stale pose, lost link, camera MISALIGNED, safety bubble, E-stop.
  - Dashboard login for other devices, and reject cross-site POSTs (check `Origin`). Local-only by default.
  - Simulation never touches real hardware. No placeholder numbers shown as measurements.

### 4.3 Car firmware (two sketches, one protocol)
Keep firmware **minimal and rarely reflashed**; all tuning lives on the server. Direction for the protocol
(architect finalizes it in `PROTOCOL.md`): newline-terminated ASCII lines, e.g.

| Line | Meaning |
| --- | --- |
| `?` | Identify → `ID <name> <fw_version> <capabilities>` |
| `M <left> <right> <ttl_ms>` | Wheel PWM −255…255, valid for at most `ttl_ms` (clamped ≤ 500), then the car stops |
| `S` | Stop now |
| `P <n>` | Ping → `P <n>` (round-trip time) |
| `L <0/1>` | Status LED (optional, for latency tests) |

Leave room in the capability string for a future gyro/encoder, even though none is planned.

### 4.4 Dashboard (recommended: React + Vite + TypeScript)
- Live floor map: grid, blocked cells, stations, both cars (pose, heading, pose age), planned paths,
  reserved cells, safety bubble.
- **Connect cars** (one button) with per-car link state; phone status (fps, per-stage timings, pose age,
  calibration state, connection mode with latency/jitter/loss, thermal level) and the low-rate preview.
- STOP ALL / RESUME, per-car stop, manual drive per car, task creation and queue, mode switch.
- Event timeline per car with filters and the **Why?** panel (facts, rule reason, AI explanation and audit
  verdict, flags).
- **Tags page** (section 4.5).
- Tools: calibration wizard (anchors, per-car motion calibration), latency test.

### 4.5 Tag roles, assigned by the admin
- The phone app reports **every** tag it sees and knows nothing about roles, so role changes never need an
  app update. The server keeps a **tag registry** (persisted, e.g. SQLite or a JSON file, survives restarts).
- The dashboard's Tags page lists every tag the camera currently sees plus every registered tag, with its
  live position. **Unassigned** tags are shown but ignored by planning and control.
- Roles (the architect finalizes the list; each with its own settings):
  - **Car** — bound to one entry in `cars.json` (which Bluetooth car it is), with printed size and the
    marker's offset from the car's centre. A car tag must be bound to exactly one car and vice versa.
  - **Station** — a name and type (pickup / drop-off / home / charging / custom); snapped to a grid cell.
  - **Floor anchor** — printed size and floor position: typed in, or measured automatically from the
    first anchor (DUO-WARE 1's auto-location of unplaced anchors, `WorldConfig.place_anchor`).
  - **Blocked cell / obstacle** — marks its grid cell as not drivable.
  - **Ignore**.
- Each tag also stores its printed size (mm); the detector warns when the measured size doesn't match.
- **Safety rules:** changing the role of a car tag or an anchor, or rebinding a car, is only allowed while
  the affected cars are stopped (or the whole system is paused). Anchor changes trigger a recalibration.
  Planning uses the new stations/blocked cells only from the next plan.
- Validation: one role per tag ID; clear errors for conflicts (e.g. an ID used as both car and anchor).
- Every change goes into the event log with the operator, old and new value.
- A **preset** button can apply the suggested setup from section 3, and presets can be saved and loaded,
  since the test area changes between sessions.

### 4.6 Grid of node markers, orthogonal moves only
- **Allowed** (left) vs **forbidden** (right): nodes connect only to their neighbour above, below, left and
  right. Diagonal and long-range links never exist.

  ```
  allowed                forbidden
  [ ]--[ ]--[ ]          [ ]--[ ]--[ ]
   |    |    |            | \  |  / |
  [ ]--[ ]--[ ]          [ ]--[ ]--[ ]
   |    |    |            | /  |  \ |
  [ ]--[ ]--[ ]          [ ]--[ ]--[ ]
  ```
- New tag role **Grid node**, with a (row, column) position. The dashboard suggests row/column from the
  detected positions and the admin confirms. A node can also be a station. The grid need not be full;
  missing nodes are allowed. Edges are created automatically between orthogonal neighbours only; the admin
  can disable a node or an edge (blocked) on the dashboard.
- **Spacing is not fixed (owner requirement, 2026-10-01).** The project is shown at different
  hackathons/competitions, so the distance between tags changes from venue to venue and can differ
  between edges in the same layout. Nothing in code may assume a spacing:
  - **Topology comes from the admin** (row, column); **geometry comes from the camera.** Node positions,
    and each edge's length and direction, are measured, not assumed.
  - Scale comes from the tags' **printed size** (first anchor = origin; other tags located automatically,
    like DUO-WARE 1's `WorldConfig.place_anchor`), so no tape measure is needed. A typed-in spacing is
    optional, and only used as a check.
  - Hand-placed rows won't be perfectly straight. Each straight run follows the **measured line between its
    two nodes**, and each turn is to the measured bearing of the next edge (about 90°, not exactly).
    Validate the layout: warn when an edge is more than a set angle (e.g. 15°) off its row/column
    direction, when an edge is too short for a car plus margin, or when a node is outside the camera view.
  - Per-edge speed profile and predictive stop use that edge's measured length. The safety bubble and
    reservation rules must still hold when neighbouring nodes are close together.
  - **Venue setup wizard**, fast enough to run at an event: place tags → auto-detect → assign roles and
    suggested row/column → measure → validate → save as a **venue preset** (tags, roles, measured layout,
    camera exposure). Loading a preset re-checks it against what the camera sees now.
- Headings are defined in the **grid frame** (the grid may be rotated relative to the camera). A car stops
  centred on the node, using its tag-to-centre offset.
- Planner: A* on the 4-connected node graph (Manhattan heuristic).
- Reservations cover **nodes and edges**: two cars must never be granted the same node, and never the same
  edge in opposite directions (a head-on swap). Deadlock back-off moves only to an orthogonal free node.
- Cars drive over node tags and hide them; calibration and node tracking must tolerate that.
- Tests: no plan, recovery move or manual command ever contains a diagonal step (every move is along an
  edge between row/column neighbours); layouts with uneven spacing, slightly crooked rows and a rotated
  grid all plan and drive in simulation; no node or edge is ever double-granted over long random
  simulations.

## 5. Latency — measure, don't assume

Build measurement in from the first milestone:
- Phone: capture → packet sent (on-device) and per stage (capture pipeline, detection, send), fps, CPU load,
  thermal status/headroom.
- Server: capture → received (via clock sync), pose age distribution p50/p95/max, dropped packets; per
  connection mode: network latency, jitter and packet loss.
- Sustained: fps, pose age and thermal level over a multi-hour run, not just the first minutes.
- **Loop latency step test:** command a car to start spinning from rest; time until the camera reports the
  heading changing. This is the number the controller must plan around.
- Targets to measure against (not claims): pose age at the server p50 < 50 ms, p95 < 80 ms; tracking
  ≥ 30 fps with 2 cars and 4 anchors in view.

Every hardware result goes into `docs/HARDWARE_LOG.md` with date, setup and numbers.

## 6. What to reuse from DUO-WARE 1 (read, port, don't copy blindly)

Folder: `C:\Users\khusw\Downloads\Duo_Ware\Duo_Ware` (the current copy; `OneDrive\Documents\Duo_Ware` is an
old snapshot). Its `context/context.md` has the full history and measurements.

| Take | From |
| --- | --- |
| Anchor fit rules, steady-frame averaging, camera-moved check | `vision/world.py`, `vision/fusion.py` |
| Predictive stop + overshoot learning | `control/navigator.py` |
| Facts-vs-reason event log, `why_stopped` | `control/event_log.py` |
| Access code login, throttle | `control/auth.py` |
| Checkpoint reservation, deadlock handling, stopped-robot recovery, their tests | `fleet/warehouse/` + `tests/test_fleet_manager.py` |
| Lens calibration (later) | `vision/lens.py`, `tools/lens_calibration.py` |
| Realistic camera noise/blur model for the simulator | `simulator/multi_arena.py` (`SimRealism`) |

Don't carry over: the 1,500-line `server.py`, the Bluetooth radio-reset auto-connector, the kick-start
threads, the LLM driving pilot, hard-coded pixel coordinates.

## 7. Suggested milestones (the architect may reorder)

| # | Milestone | Done when |
| --- | --- | --- |
| M0 | Repo, docs, protocol v1, skeleton, test runner | `CLAUDE.md`, `PROTOCOL.md`, `DECISIONS.md`, `docs/plans/M1.md` exist; empty test suite runs |
| M1 | Server core + **simulator** (fake phone sending UDP marker packets with noise/latency/dropouts; simulated cars with per-car asymmetry, dead zone, coast and link delay) + **tag registry and Tags page** + dashboard live map | Roles assigned on the Tags page drive what appears on the map (cars, stations, anchors); roles persist across restarts; pose-age stats shown |
| M2 | Car link manager + both firmwares + one-click connect + manual drive + E-stop + TTL | Hardware: both cars connect with one click; manual drive; kill the server → cars stop within TTL; power-cycle a car → auto-reconnect |
| M3 | Android app v1 (detect, exposure control, UDP, discovery, clock sync, preview, stats) | Hardware: measured fps, pose age p50/p95, blur vs speed compared with DUO-WARE 1 numbers |
| M4 | Floor calibration, grid, per-car motion calibration, latency step test | Hardware: calibration fit error, measured loop latency per car |
| M5 | Single-car grid motion (turns, straight runs, predictive stops) | Hardware: stop-error and turn-error distributions over a stated number of runs |
| M6 | Two cars: A*, reservations, conflicts, deadlock, safety bubble | Simulation: never a double-granted cell over long random runs; then hardware |
| M7 | Missions + event log + Groq explainer/auditor + dashboard timeline | "Why did car X stop?" answered from the log; AI text labelled; AI cannot command (test) |
| M8 | Hardening: auth, Origin check, reconnect edge cases, docs | Checklist in the plan passes |

## 8. How the work is split between models

- **Opus 5.5 (architect / hard problems):** architecture, `PROTOCOL.md`, the gyro-less control design,
  planning/reservation/deadlock rules, one plan file per milestone with acceptance criteria, reviewing
  Sonnet's work, and diagnosing real hardware logs.
- **Sonnet 5.5 (developer / tester):** implements one milestone plan at a time, writes and runs tests,
  records results in the plan, commits.

Rules both follow (put them in `CLAUDE.md`):
- One source of truth per concern: `PROTOCOL.md` for wire formats, the tag registry (set from the dashboard) for tag roles, config files for cars/map/tuning,
  `DECISIONS.md` for why. Changing the protocol means a version bump and updating both firmwares and the app.
- Git from day one; commit after each working step; push after each milestone. Keep the repo **outside
  OneDrive** (sync breaks `.venv`, `node_modules` and Gradle builds). The repo lives at `C:\Users\khusw\Downloads\duoware`.
- One Claude session per folder at a time; parallel work only in a separate worktree/branch.
- Never write "verified" for anything not run on hardware; say "simulation only".
- Small modules (aim < 400 lines per file); no magic numbers in code — they go in config with a comment.
- Secrets (`GROQ_API_KEY`, access codes) only in `.env`, which is git-ignored; `.env.example` holds placeholders.
