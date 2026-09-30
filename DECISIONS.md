# DUO-WARE 2 — Decisions

Why the system is built the way it is. One entry per decision: what, why, and what was rejected.
Only Opus sessions change this file; Sonnet proposes changes in its plan's "Proposed changes".
A superseded decision stays here, marked **Superseded by Dnn**.

Dated 2026-10-01 (M0) unless noted. "Brief" = `docs/PROJECT_BRIEF.md`.

---

### D1 — The phone sends pixel corners; the server does all geometry
**Decision.** The app detects ArUco markers and sends each marker's four raw pixel corners with timing
(PROTOCOL.md §2). Floor fit, parallax, heading, grid and fusion are server-side.
**Why.** The app stays a simple, rarely-updated sensor; all tuning is in Python where it is easy to test
with the simulator; adding phones needs no app change; packets stay small (~50 bytes per marker).
**Rejected.** Phone computes floor coordinates (the earlier advice): every tuning change would need an app
release, and multi-camera fusion would need phones to agree on a frame. Sending video (DUO-WARE 1):
latency and motion blur were its main failure.

### D2 — JSON datagrams for marker frames
**Decision.** Frames, sync and beacon are compact JSON, one message per UDP datagram.
**Why.** At 30 Hz and < 1.5 KB per frame the cost is negligible; it is readable in a packet capture and in
logs; Android has `org.json` built in and Python parses it natively.
**Rejected.** Protobuf/CBOR/custom binary: smaller but needs schemas and tooling on both sides for no
measurable gain at this rate. TCP/WebSocket for frames: head-of-line blocking turns one lost packet into
delay for every later pose.

### D3 — Server-initiated NTP-style clock sync on the frames socket
**Decision.** The server sends `sync` to the address frames come from; the phone replies with `t2`/`t3`.
Offset and drift are fitted over the lowest-RTT samples (PROTOCOL.md §3).
**Why.** Pose age must be measured, not assumed (brief §5); the server owns the result so it decides
when a camera is `UNSYNCED`. Replying from the frames socket needs no extra port or NAT/firewall rule.
Lowest-RTT filtering removes queueing delay.
**Rejected.** Trusting the phone's wall clock (NTP on phones is coarse and can jump). Phone-initiated sync
(the server would have to trust the phone's estimate). PTP (not available on Android without root).

### D4 — The camera's own timestamp clock, detected at run time
**Decision.** The phone's sync clock is whatever clock the sensor timestamps use (`boottime` or
`monotonic`), detected when the camera starts; `unknown` keeps the camera `UNSYNCED`.
**Why.** Camera2 may report `SENSOR_INFO_TIMESTAMP_SOURCE = UNKNOWN`; then sensor timestamps are not
guaranteed to match `elapsedRealtimeNanos()`, and every pose age would be silently wrong. The realme
9 Pro+'s value is not known yet (checked in M2).
**Rejected.** Assuming `elapsedRealtimeNanos()` always.

### D5 — Discovery by per-interface directed broadcast
**Decision.** The server broadcasts a beacon every second to the directed broadcast address of every
IPv4 interface (PROTOCOL.md §4.1). Manual host entry in the app is the fallback.
**Why.** No IP typing (brief 4.1); works over USB tethering and WiFi. Windows sends `255.255.255.255`
out of only one interface, so a plain limited broadcast can miss the tether.
**Rejected.** mDNS/NSD: needs an extra responder on Windows and is often blocked by the firewall;
typing an IP.

### D6 — Pairing code → token; UDP accepted only from a live session's IP with its `sid`
**Decision.** First connection: the phone enters the pairing code shown on the dashboard and receives a
token it stores. Each WebSocket session gets a random `sid`; UDP frames are accepted only with a live
`sid` from that session's IP.
**Why.** Stops a random device on the network from feeding fake poses (DUO-WARE 1 risk R5) at almost
no cost. On a local network this is proportionate.
**Rejected.** Per-packet HMAC (more work in the hot path for a threat that needs someone already on the
tether or LAN); no authentication.

### D7 — Server: Python 3.13, asyncio, FastAPI + uvicorn; car links in threads
**Decision.** One process. FastAPI serves REST and both WebSockets; asyncio runs the UDP endpoint,
sync, broadcaster and control loop. Each car link runs in its own thread with a blocking socket and
talks to asyncio through queues (M3).
**Why.** Brief 4.2. asyncio suits many small streams. Bluetooth RFCOMM sockets are not reliably supported
by the Windows proactor event loop, and a connect to a switched-off car can block for 5–20 s, so they
must not live on the event loop.
**Rejected.** Flask-SocketIO (threads everywhere, harder to reason about timing); one process per car
(cross-process safety state such as the E-stop gets harder).

### D8 — Tag roles live only in the tag registry, set from the dashboard
**Decision.** Roles are stored in `data/state.db` and changed only through the API/Tags page
(PROTOCOL.md §7.4). Venue presets (including the DUO-WARE 1 kit) are data files in
`config/venue_presets/` or saved rows; applying one is an ordinary, logged, safety-checked change (D28). No code refers to a
specific tag ID.
**Why.** Brief 2 and 4.5: the test area changes between sessions; roles must change without code or app
changes, survive restarts, and be audited.
**Rejected.** Roles in config files (a restart per change, no audit, no safety check); fixed ID ranges
per role (DUO-WARE 1's anchors 10–49 convention).

### D9 — Configuration in TOML; learned and runtime state in SQLite
**Decision.** Human-edited settings are TOML files in `config/` (read-only at run time, commented). What the
system learns or the admin sets at run time (tag registry, presets, pairings, per-car calibration) is in
`data/state.db`; the event log is in `data/events.db`. Both are git-ignored.
**Why.** TOML allows comments (the brief asks for commented config) and Python reads it with the standard
library (`tomllib`). Keeping learned values out of config means config never gets rewritten by the
program, and a bad calibration can be reset without touching the files.
**Rejected.** JSON config (no comments; the brief's `cars.json` became `cars.toml`); YAML (extra
dependency, ambiguous typing); writing learned values back into config.

### D10 — Minimal car firmware; everything tunable is server-side
**Decision.** Firmware only parses lines, drives the L298N, enforces the TTL and stops on errors
(PROTOCOL.md §5). No kick-start, ramps, trims, gyro or speed control on the car.
**Why.** Firmware is rarely reflashed (Arduino IDE by hand); the two cars differ, and per-car behaviour is
easier to calibrate, log and test in Python. DUO-WARE 1's firmware logic (kick-start threads, gyro turns)
was hard to observe and to change.
**Rejected.** Closed-loop turns on the car (no gyro any more); per-car firmware constants.

### D11 — Stop means brake, then release
**Decision.** `S`, TTL expiry and every error brake both motors for 150 ms (L298N enable high, inputs
equal), then release.
**Why.** Braking shortens the coast, which is the main stop-error term without encoders; releasing
afterwards leaves the car pushable and the driver cool.
**Rejected.** Coast only (longer, battery-dependent stops); holding the brake indefinitely.

### D12 — Motion control is designed around a 120–250 ms loop delay (outline; details in M5)
**Decision.** Cars move only along the road network (D24): straight runs along the measured line between
two orthogonally adjacent nodes, and in-place turns at a node to the measured bearing of the next edge.
Control from camera poses has two regimes: continuous motion (refreshed `M` commands) for the bulk of a
move, then **car-timed pulses** (`M` with a short TTL) for the final approach. Stops are predictive:
the controller projects the pose forward over the measured delay using the commands already sent
(Smith-predictor style), and learns each car's coast and turn rate online from every move. M4
calibration only supplies starting values.
**Why.** Estimated loop delay (command → effect seen) is 120–250 ms (camera pipeline 30–70 ms,
detection, Bluetooth 10–50 ms with 100 ms+ spikes on Windows, motor response). TT gear motors often
cannot move steadily below ~150–300 mm/s or ~120–300°/s, so continuous control alone would overshoot.
The car timing the pulse length removes Bluetooth jitter from the pulse length. Turn rate, minimum PWM and
coast drift as the (unmeasured) battery drains, so a one-off calibration goes stale.
The controller is designed for pose age p95 ≤ 150 ms; the brief's p50 < 50 ms target is measured
(M2) but not relied on.
**Rejected.** Gyro/encoders (owner decision: none). Continuous low-speed control only. Fixed calibration.

### D13 — No hand measurements anywhere
**Decision.** Nothing needs a tape measure (owner requirement, 2026-10-01):
- **Floor frame** is defined by the *origin* floor tag: its centre is (0, 0) and its TOP edge points to
  −y. All other floor tags (nodes and anchors) are located automatically from it (DUO-WARE 1
  `place_anchor`). The grid's own rotation is measured, not required (D27).
- **Road network geometry** (node positions, edge lengths and bearings) is measured by the camera; the
  admin only gives each node its (row, column) (D24).
- **Scale** comes from the printed tag sizes. A wrong printed size scales the whole map consistently,
  which does not affect driving along measured edges. A typed-in spacing is optional and only a check.
- **Car tag parallax**: the car tag's apparent size on the floor divided by its printed size gives the
  parallax scale `s = H/(H−h)`. Positions are pulled towards the camera nadir (image centre mapped to the
  floor) by `1/s`. Neither camera height nor tag height needs measuring.
- **Tag offset from the rotation centre** and **tag heading offset**: measured by M4 calibration moves
  (spin in place → circle fit; short straight drive → direction of travel vs tag heading).
- **Car footprint** is only a safety margin; an estimate to ±30 mm is enough.
**Why.** The owner cannot measure the cars and floor precisely, and small manual errors would become
large driving errors. Camera-measured values are consistent with the camera that uses them.
**Rejected.** Measured anchor coordinates, camera height, wheel diameter and track width in config.

### D14 — The simulator uses the real interfaces, in a separate process
**Decision.** `python -m duoware.sim` is its own process. Its phone listens for the beacon, pairs over
`/ws/phone`, sends UDP frames and answers sync exactly per PROTOCOL.md. Its cars speak the car line
protocol over localhost TCP. The server in `--sim` mode uses a TCP car transport and never imports the
Bluetooth/serial transports.
**Why.** The server code path is the same in simulation and on hardware, so simulation tests the real
thing (protocol, sessions, clock sync, TTL). "Simulation never touches real hardware" is enforced by
construction (DUO-WARE 1 risk R7).
**Rejected.** In-process fakes behind Python interfaces (they would skip the wire formats); rendering
images and running OpenCV detection (slow; the phone does detection, so the sim models the detector's
output statistically, fitted to DUO-WARE 1 recordings).

### D15 — Dashboard: React + Vite + TypeScript, SVG map, read-only WebSocket
**Decision.** The dashboard receives full `state` snapshots at 20 Hz over `/ws/dashboard` and sends
all commands over REST. The floor map is SVG in floor millimetres. TypeScript types mirror PROTOCOL.md by
hand in one file (`dashboard/src/api/types.ts`); server tests check the PROTOCOL.md examples against the
server's models.
**Why.** Brief recommendation. Full snapshots make reconnects and missed messages harmless; REST commands
get the local-only and Origin checks for free. A few dozen SVG elements at 20 Hz is cheap, and SVG
scales and is easy to click.
**Rejected.** Deltas (state drift bugs); commands over the WebSocket (harder to authorise); canvas (hit
testing and text by hand); generated TS types (another toolchain for a small schema).

### D16 — One port in production; Vite proxy in development
**Decision.** The Python server serves the built dashboard (`dashboard/dist`) at `/`. During
development, Vite on :5173 proxies `/api` and `/ws` to :8000.
**Why.** One URL for the admin; no CORS.
**Rejected.** A separate static server.

### D17 — Bind 0.0.0.0, but the dashboard is local-only until M8
**Decision.** The server listens on all interfaces (phones must reach `/ws/phone`), but REST and
`/ws/dashboard` refuse non-local clients (`access.allow_remote_dashboard = false`) until M8 adds the
access-code login (ported from DUO-WARE 1 `control/auth.py`). Mutating requests are rejected when their
`Origin` is not allowed. `stop_all` is always accepted.
**Why.** Brief 4.2 safety: local-only by default, reject cross-site POSTs, E-stop always reachable.
**Rejected.** Binding 127.0.0.1 (phones could not connect); no protection until M8.

### D18 — Android: Kotlin, plain Views, CameraX + Camera2 interop, OpenCV from Maven Central
**Decision.** Kotlin, `minSdk 29`, `compileSdk`/`targetSdk 36`, AGP 8.13 + Gradle 8.13, built with
Android Studio's bundled JDK 21 (`JAVA_HOME`). The system `java` on this laptop is Java 26, which Gradle
8.13 cannot run on. UI is plain Views (a status screen); no Compose. CameraX `ImageAnalysis`
(`STRATEGY_KEEP_ONLY_LATEST`, Y plane) with Camera2 interop for manual exposure/ISO/focus; OpenCV
`ArucoDetector` from Maven Central (M2).
**Why.** Brief 4.1. The screen is a status display; Compose would add build time and dependencies for
nothing. Versions verified by building the M0 skeleton on 2026-10-01.
**Rejected.** Compose; the NDK with OpenCV C++ (only if detection time in M2 demands it).

### D19 — Milestone order: phone app (M2) before the car link (M3)
**Decision.** Swapped from the brief (approved by the owner 2026-10-01).
**Why.** Phone timing, blur at short exposure and lighting are the risks that could change the
architecture (for example, needing lamps); the car link is a better-known problem.

### D20 — Firmware is built with the Arduino IDE only
**Decision.** No automated firmware compile in the test commands (owner choice). Firmware behaviour is
specified in PROTOCOL.md §5; the simulator's cars implement the same rules and are tested; each firmware
milestone ends with a hardware checklist the owner runs.
**Rejected.** `arduino-cli` in the test suite (declined for now; can be added later without other changes).

### D21 — Server timing uses the monotonic clock
**Decision.** All control timing, pose ages and TTL bookkeeping use `time.monotonic_ns()`. Wall-clock time
appears only as `*_wall_ms` for display and in the event log next to `mono_ms`.
**Why.** Wall clocks can jump (NTP, DST); a jump must never make a pose look fresh.

### D22 — Units and axes carried over from DUO-WARE 1
**Decision.** Floor millimetres, +x right, +y down the map, degrees clockwise, heading from the tag's
bottom edge to its top edge (PROTOCOL.md §0).
**Why.** Brief 4.2; same handedness as the camera image, so signs match between image and map.

### D23 — Event log in SQLite with a background writer
**Decision.** `data/events.db`, one table, written by a dedicated thread from a queue so the control loop
never waits on disk. Records follow PROTOCOL.md §8 (measured `facts` separate from the rule `reason`,
DUO-WARE 1 `event_log.py`).
**Why.** "Why did car B stop at 14:02:31?" needs filtering by car, type and time; SQLite gives that with
indexes and survives crashes. A separate file from `state.db` keeps the append-heavy log away from the
small, precious registry.
**Rejected.** JSONL files (a scan per question; still offered later as an export).

### D24 — The grid is a road network of node tags; topology from the admin, geometry from the camera
**Decision.** (Owner requirement, brief 4.2 and 4.6.) Floor tags with role `node` carry an admin-given
`[row, col]`. Edges exist only between orthogonal neighbours that are both present; never diagonal,
never across a missing node. "Measure layout" stores each node's camera-measured position; edge length
and bearing follow from it. Straight runs follow the measured line between two nodes; turns go to the
measured bearing of the next edge (about 90°, not exactly). Validation (angle off row/column, edge too
short for a car, node outside the view, optional spacing check) only warns. A node seen away from its
measured position is `moved` and treated as blocked until re-measured. Admin blocks for nodes and edges
are stored with the layout.
**Why.** Venues change: spacing differs between events and between edges, and hand-placed rows are
crooked. Nothing in code may assume a spacing, so the only geometry used is what the camera measured.
Freezing the measured layout (rather than following live positions) keeps plans stable while cars
cover node tags, and makes a pushed tag visible instead of silently moving a road.
**Rejected.** A uniform cell grid with a fixed cell size (the first M0 draft, replaced the same day);
following live node positions continuously; diagonal or long-range edges.

### D25 — Uno + HC-05 serial port: proposed AltSoftSerial or hardware serial (OPEN)
**Decision (proposed, awaiting the owner's wiring answer before M3).** Prefer hardware serial on D0/D1
(unplug HC-05 TX to flash) or `AltSoftSerial` (RX 8, TX 9, Timer1; motor PWM then on pins 5 and 6).
**Why.** `SoftwareSerial` cannot receive while sending and blocks interrupts per received byte; replies
(`P`, `ID`) colliding with incoming commands would lose characters.
**Rejected (if the owner agrees).** `SoftwareSerial` at 38400 (the brief's original constraint).

### D26 — AI explainer isolation is enforced by a test (M7)
**Decision.** `duoware.explain` (Groq) may import only the event-log reader and its own modules; a test in
`server/tests/test_architecture.py` fails if it (transitively) imports the car link, control, planning or
the phone ingest. AI text is stored separately from event `reason` and always labelled.
**Why.** Brief 4.2: the AI explains; it can never command.

### D27 — One floor frame for computation; the grid frame is for display
**Decision.** Everything is computed and sent in the floor frame (origin tag, D13). "Measure layout"
also fits `grid_rotation_deg`, the circular mean of the edge bearings folded to one direction (row
edges as measured, column edges minus 90°). The dashboard draws the map in the grid frame and names
directions (up/down/left/right) in it.
**Why.** Headings are defined in the grid frame (brief 4.6) but the grid may be rotated relative to the
origin tag and the camera. Re-basing the floor frame on every re-measure would move stored anchor poses,
car calibration offsets and logged positions; a display rotation does not.
**Rejected.** Redefining the floor frame from the layout; requiring the origin tag to be square to the grid.

### D28 — Node tags are also calibration anchors; venue presets
**Decision.** Both `node` and `anchor` tags are floor tags used for the image→floor fit (DUO-WARE 1 rules:
1 → similarity, 2 → affine, ≥3 → homography with RANSAC). A floor tag that RANSAC rejects while the others
agree has moved: it is un-placed and located again, and the camera stays calibrated; when most floor tags
disagree the camera is `MISALIGNED`. A **venue preset** stores the registry, the measured layout (with admin
blocks) and camera overrides (exposure, ISO); applying it re-checks the stored positions against what the
camera sees and marks moved nodes. Built-in presets live in `config/venue_presets/`, saved ones in
`state.db`.
**Why.** Separate anchor tags would be needed only where nodes are not visible; a 3 × 3 network already
gives 9 well-spread fixed tags. Cars cover node tags while driving, so calibration must work from any
subset, which the RANSAC fit already does. The venue wizard must be fast at an event (brief 4.6).
**Rejected.** Anchors only (extra tags to print and place); trusting a preset without re-checking it.

### D29 — Traffic rules on the road network (outline; details in M6)
**Decision.** A* over the 4-connected node graph with a Manhattan heuristic in (row, col) and edge costs
from measured lengths. Reservations cover **nodes and edges**: a node is never granted to two cars, and an
edge is granted to one car at a time in either direction (stricter than "no head-on swap", because
measured edges may be short). Deadlock back-off moves only to a free orthogonal neighbour. The safety
bubble (camera distance between cars) applies whatever the reservations say. Every planned, recovery and
manual "go to" move is a single edge between row/column neighbours; tests assert it.
**Why.** Brief 4.6; DUO-WARE 1's checkpoint reservation and deadlock handling (`fleet/warehouse/manager.py`)
map directly onto nodes and edges.
