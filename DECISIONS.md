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
delay for every later pose (TCP is used only where UDP cannot go: the `wired_adb` fallback, D33).

### D3 — Server-initiated NTP-style clock sync on the frames socket
**Decision.** The server sends `sync` to the address frames come from; the phone replies with `t2`/`t3`.
Offset and drift are fitted over the lowest-RTT samples (PROTOCOL.md §3).
**Why.** Pose age must be measured, not assumed (brief §5); the server owns the result so it decides
when a camera is `UNSYNCED`. Replying from the frames socket needs no extra port or NAT/firewall rule.
Lowest-RTT filtering removes queueing delay.
**Rejected.** Trusting the phone's wall clock (NTP on phones is coarse and can jump). Phone-initiated sync
(the server would have to trust the phone's estimate). PTP (not available on Android without root).
**Amended (M1 review).** The drift slope is used only when the chosen samples span at least
`clock_sync.slope_min_span_fraction` (0.25) of the window; otherwise the offset is their mean. Measured in M1
(simulated 0–3 ms one-way jitter, 20 samples over 2 s): line fit mean error 0.37 ms, worst 2.1 ms; mean only
0.17 ms, worst 0.6 ms. With a sample every 0.5 s the extrapolation is short, so a 30 ppm drift costs only
microseconds; the slope matters only when averaging over most of the 60 s window.

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
out of only one interface, so a plain limited broadcast can miss the tether. In the `wired_adb` fallback
there is no broadcast path, so the phone probes `127.0.0.1` through `adb reverse` instead (D33).
**Rejected.** mDNS/NSD: needs an extra responder on Windows and is often blocked by the firewall;
typing an IP.
**Amended (M1 review).** M1 assumed every interface is a /24 because the standard library cannot read a
netmask on Windows. That holds for USB tethering, phone and Windows hotspots, but not for many venue and campus
networks (/23 to /16), and a wrong broadcast address fails silently. The server now reads each interface's real
netmask with `psutil.net_if_addrs()` (one small, widely used dependency; it also gives interface names for the
logs) and sends each beacon twice from a socket bound to the interface: to the directed broadcast and to
`255.255.255.255`. A Wi-Fi-client phone that hears no beacon probes its gateway's `/api/beacon` (covers a
laptop hotspot, where the laptop is the gateway). Venue Wi-Fi with client isolation defeats every discovery
method and also the frames themselves; the answer there is WIRED or a hotspot (PROTOCOL.md §4.1).
Rejected: parsing `ipconfig` (localised output), `GetAdaptersAddresses` through ctypes (≈100 lines to do
what psutil does).

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

### D18 — Android build toolchain and UI
**Decision.** Kotlin, `minSdk 29`, `compileSdk`/`targetSdk 36`, AGP 8.13 + Gradle 8.13, built with
Android Studio's bundled JDK 21 (`JAVA_HOME`). The system `java` on this laptop is Java 26, which Gradle
8.13 cannot run on. UI is plain Views (a status screen); no Compose. From M2 the app also has a C++ part
(NDK + CMake, D31).
**Why.** Brief 4.1. The screen is a status display; Compose would add build time and dependencies for
nothing. Versions verified by building the M0 skeleton on 2026-10-01.
**Rejected.** Compose.
**Partly superseded (2026-10-01):** the first version chose CameraX `ImageAnalysis` with Camera2 interop and
the OpenCV Java API, with C++ only if needed. Replaced by D30 (Camera2 directly) and D31 (C++ detection loop)
after the owner's sustained-performance requirement.

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
**Refined by D37 and D38 (M1 review):** metric refinement after every homography fit, per-tag steady buffers,
and a stricter "one tag moved" rule.

### D29 — Traffic rules on the road network (outline; details in M6)
**Decision.** A* over the 4-connected node graph with a Manhattan heuristic in (row, col) and edge costs
from measured lengths. Reservations cover **nodes and edges**: a node is never granted to two cars, and an
edge is granted to one car at a time in either direction (stricter than "no head-on swap", because
measured edges may be short). Deadlock back-off moves only to a free orthogonal neighbour. The safety
bubble (camera distance between cars) applies whatever the reservations say. Every planned, recovery and
manual "go to" move is a single edge between row/column neighbours; tests assert it.
**Why.** Brief 4.6; DUO-WARE 1's checkpoint reservation and deadlock handling (`fleet/warehouse/manager.py`)
map directly onto nodes and edges.

### D30 — Camera2 directly, one YUV `ImageReader` stream, manual exposure and frame duration
**Decision.** The app drives Camera2 itself (the owner's requirement of 2026-10-01 allowed Camera2 or CameraX
with full interop). While tracking, the capture session has **one** stream: an `ImageReader`
(`YUV_420_888`, `maxImages` 3) at the chosen size. AE is off; `SENSOR_EXPOSURE_TIME`, `SENSOR_SENSITIVITY`
and `SENSOR_FRAME_DURATION = 1e9 / fps` are set directly (fps 0 → `getOutputMinFrameDuration` for that
size, the sensor's maximum). Autofocus and AWB run once, then lock. The camera callback thread only hands the
`Image` to the detection thread, and the image is closed as soon as detection is done. On-screen aiming in
`setup` mode uses a second, small surface that is removed for `tracking`. The dashboard preview is made from
the Y plane (greyscale), so it needs no extra stream. Capabilities (hardware level, capability list, fps
ranges, YUV sizes with their maximum fps, exposure/ISO ranges, timestamp source, intrinsics) go to the server
in `hello` (PROTOCOL.md §4.3).
**Why.** The highest frame rate through an `ImageReader` is set by the minimum frame duration of that single
stream; every extra stream (CameraX adds preview/analysis use cases and its own buffering) can lower it on
`LIMITED`/`FULL` devices. With AE off, `SENSOR_FRAME_DURATION` sets the rate exactly. Direct Camera2 also
controls buffer counts, callback threads and stream sizes, with no library conversions in between. The app is
a single-purpose sensor, so CameraX's lifecycle conveniences are worth little here.
**Rejected.** CameraX `ImageAnalysis` with Camera2 interop (less control over streams, frame duration and
buffering; resolution selection can differ per device). Constrained high-speed sessions (120/240 fps): they
accept only preview/video surfaces, not an `ImageReader`.
**Unknown until M2:** the realme 9 Pro+'s hardware level, `MANUAL_SENSOR` support and maximum YUV fps. Without
`MANUAL_SENSOR` the app locks AE at the lowest exposure compensation and reports that.

### D31 — Detection in C++ (NDK + OpenCV), one JNI call per frame, no per-frame allocation
**Decision.** The per-frame work (ROI prediction, window detection, full scans, corner refinement, packing the
result) is a C++ library. Kotlin passes the Y plane's **direct** `ByteBuffer` and its row stride in one JNI call
per frame; C++ wraps it in a `cv::Mat` header without copying and writes results into a preallocated direct
result buffer. All working memory (window headers, detector objects, result arrays) is allocated once and
reused. OpenCV comes from the Maven Central `org.opencv:opencv` AAR through Prefab (fallback: the OpenCV
Android SDK zip referenced from CMake). A Java-API pipeline (`java_full`) is kept only as the benchmark
baseline, so the JNI and allocation overhead is **measured** (PROTOCOL.md §4.7), as the owner asked.
**Why.** ROI tracking turns one detection per frame into several small ones. Through the Java API each call
allocates `Mat`/`List` objects and crosses JNI; at 60 fps for hours that means GC pauses and overhead that grow
with the number of windows. One native call per frame keeps the hot path allocation-free and makes thread
placement and timing controllable.
**Rejected.** Kotlin/Java OpenCV as the production path (kept as the baseline); a custom ArUco decoder (OpenCV's
is well tested; revisit only if benchmarks show detection itself is the limit).

### D32 — Region-of-interest tracking with periodic full scans
**Decision.** Every frame the phone searches small windows around the predicted positions of the tags in
`settings.tracking.track_ids` (the server's car tags) and of any tag seen moving between full scans. A
full-frame scan runs every `full_scan_every` frames and on the frame after a tracked tag is lost. Prediction is
constant velocity from the last two detections, to the new frame's capture time. Windows are processed in
parallel on the fastest cores. Each frame says what was searched (`scan`, `searched`), and the server treats a
tag as "not seen" only where the phone looked (PROTOCOL.md §2). Floor-tag work uses full frames only.
**Why.** Car tags are the only ones that move quickly; floor tags only need checking a few times a second. A
few 128 px windows cost a fraction of a full 1280 × 720 scan, which is what makes the sensor's maximum frame
rate reachable. Sending the search scope keeps the server honest about absence.
**Rejected.** A full scan every frame (caps fps at the full-scan time; kept as `native_full` for benchmarks);
the phone learning roles (roles stay server-side, D8; `track_ids` is only a hint list).

### D33 — Connection modes: WIRED (USB tethering, adb-reverse TCP fallback) and WIRELESS (Wi-Fi)
**Decision.** (Owner requirement, 2026-10-01.) Three modes, one protocol (PROTOCOL.md §4.1): `wired_tether`
(UDP over the USB tethering network), `wired_adb` (`adb reverse` carries only TCP, so frames and sync use the
length-prefixed TCP framing of §2.1), and `wireless` (UDP over Wi-Fi: router, laptop hotspot or phone
hotspot). Discovery uses the beacon on the chosen interface for tether and Wi-Fi, and an HTTP probe of
`127.0.0.1` for adb. The server listens on all interfaces and runs `adb reverse` itself. The phone binds its
sockets to the chosen network and holds a low-latency Wi-Fi lock in `wireless`. The server **measures** each
link: one-way latency from `sent_ns` through clock sync, RFC 3550 jitter, and loss from `seq` gaps. The phone
reports the interface and Wi-Fi details. A mode switch is a new session, and the stale-pose watchdog stops the
cars until fresh, synced poses arrive.
**Why.** USB tethering gives the lowest jitter and charges the phone, but not every phone/laptop pair offers it;
`adb reverse` works wherever USB debugging does. Wi-Fi gives freedom of placement at venues. Measuring the link
makes a bad venue network visible before a demo.
**Rejected.** A TCP-only protocol (head-of-line blocking on Wi-Fi); UDP over adb (not supported); letting the OS
pick the route (mobile data or the wrong interface could carry frames).

### D34 — Sustained performance: Android features and a thermal ladder
**Decision.** Tracking runs in a **foreground service** (type `camera`) holding a partial wake lock. The
activity keeps the screen on, with a dim, dark "demo" screen to cut heat.
`Window.setSustainedPerformanceMode(true)` is used where
`PowerManager.isSustainedPerformanceModeSupported()`. An ADPF **performance hint session** (API 31+) covers the
detection threads, with the frame interval as the target and each frame's work time reported. The app asks for
the battery-optimisation exemption and shows vendor-specific steps (realme "App battery management") when it
cannot be granted. **Thermal ladder:** from `getThermalHeadroom(forecast_s)` (API 30+) and the thermal-status
listener, step down one level at a time *before* Android throttles: fps first (100% → 75% → 50%), then
resolution steps with the same aspect ratio. Step back up slowly, with hysteresis. Levels, reasons and headroom
are reported to the dashboard. Resolution steps change `w`/`h`; the server rescales the camera's fit by the
pixel ratio and re-checks it against the floor tags before trusting it (same aspect ratio normally means the
same field of view; the check catches devices where it does not).
**Why.** The requirement is a multi-hour demo, not a peak. A phone that overheats throttles hard and
unpredictably, which is worse for control than a planned, lower, steady frame rate. Lowering fps keeps the
geometry unchanged, so it comes first.
**Rejected.** Fixed maximum settings (throttles after minutes); dropping resolution first (forces a fit check).

### D35 — A new phone session starts unsynced; calibration belongs to the camera
**Decision.** Each session (first connection, reconnection, mode switch) starts `UNSYNCED` with an empty
clock-sync estimator. The camera's floor calibration and statistics history are kept per `cam` across sessions.
**Why.** A different path (tether, Wi-Fi, adb) has different delays, so old sync samples are wrong for it;
starting unsynced makes the stale-pose watchdog stop the cars until the new path is measured. The camera has not
moved, so throwing its fit away would only add a pointless recalibration; the continuous floor-tag check still
catches a bumped phone.

### D36 — Benchmark and setup modes never feed control
**Decision.** In `app_mode` `benchmark` or `setup` the phone keeps sending frames (for statistics and the map),
but the server gives no poses for control, so the cars stop. Benchmark results (PROTOCOL.md §4.7) are stored
per camera and shown on the dashboard.
**Why.** A benchmark changes resolution, fps and pipeline every few seconds; driving on those poses would be
unsafe and would pollute the controller's learning.

---

Entries D37–D42: M1 review, 2026-10-01.

### D37 — Metric refinement of the floor fit from the known tag shape
**Decision.** After every successful homography fit with status `OK`, the mapping (8 parameters) and the pose of
every automatically located floor tag are adjusted together (Levenberg-Marquardt, numpy only,
`localization/refine.py`) so that each tag's image is a square of its printed size at its pose. The origin and
typed-in tags stay fixed. A refinement that would move a tag more than 150 mm is discarded (a failed fit, not a
refinement). Tags whose measured size disagrees with their printed size (`size_warn`) are left out of it,
because they break the assumption it rests on.
**Why.** Auto-location through the first similarity fit carries the phone's tilt into every located position
(a 2° tilt is about 2 % scale across 1 m), and a homography fitted to those positions reproduces the error.
Measured in M1 (simulation only): node errors up to 29.9 mm before, 1.9–2.2 mm after. It keeps D13: nothing is
measured by hand; the tags' printed size is the only scale.
**Cost.** About 63 ms per fit with 9 tags on the development laptop (M1 review measurement), on the event loop.
Fits happen while the camera is not usable for control (initial, requested, after `MISALIGNED`, WEAK → OK),
so cars are already stopped; from M3 the event-loop lag is monitored, and if a larger layout makes it too slow
the refinement moves to a worker thread.
**Rejected.** Hand-measured anchor positions (D13); a full camera model (intrinsics + pose) in M1: planned for
M4 with the lens profile, and it will also give the true nadir (M1 uses the image centre mapped to the floor,
which costs up to ~3.5 mm on an 80 mm-high car tag with the simulated tilt).

### D38 — Fit from whichever floor tags are steady; a tag "moved" only when one tag disagrees
**Decision.** Each floor tag keeps its own steady-frame buffer (cleared only when that tag's corners move or the
camera turns `MISALIGNED`), and a fit uses the visible tags whose buffers are full. A tag hidden by a car keeps
its location progress. During the locked-fit check a tag is un-placed as "moved" only when **exactly one**
automatically located tag is out of tolerance and **at least three** other placed tags agree with the fit;
any other disagreement makes the camera `MISALIGNED` (cars stop) and it refits after `drift_frames`. In a new
fit, RANSAC-rejected tags are un-placed only when the inliers are at least three tags and more than half of the
tags fitted; otherwise the fit is refused.
**Why.** DUO-WARE 1 restarted averaging whenever the visible set changed, so with two cars driving over the
nodes a fit never completed (M1: 19 s to `OK`, 7 of 9 tags). The M1 implementation un-placed up to half the
tags as "moved"; a small rotation of the phone moves far tags more than near ones, so half the tags could
exceed the tolerance while the camera stayed `OK` and the map was silently rebuilt around a wrong fit. A single
disagreeing tag among several agreeing ones is the only case where "the tag moved" is clearly the better
explanation.
**Rejected.** A fixed set of tags that must stay visible together; treating up to half the tags as moved.

### D39 — `WEAK` calibration is never used for control or the layout
**Decision.** A camera fitted from a single floor tag (`WEAK`) maps tags and cars for display and locates
other floor tags (which then upgrade it to `OK`), but its poses have `usable_for_control: false`
(`stopped_reason` `uncalibrated`), and node-moved detection, `measure` and preset checks ignore it.
**Why.** A similarity from one 90 mm tag extrapolates badly: in the M1 manual check positions far from the
tag were about 30 mm off, which produced a false "node moved" event (M1 P9). The same error would steer a car.
It only lasts until a second floor tag is located (seconds).

### D40 — Tests do not depend on the Windows timer resolution
**Decision.** The end-to-end test runs at the default Windows timer resolution; `python -m duoware.sim` still
asks for 1 ms (`timeBeginPeriod`, released at exit) so a manual simulation streams smoothly. Every timing
criterion compares the server's measurement with the simulator's truth recorded at the instant things really
happened, so coarse sleeps change the numbers but not the verdict.
**Why.** Checked in the M1 review: without the 1 ms timer the test passed (17 s) with pose age 70 ms instead of
60 ms, and the server matched the truth as closely as before (A2(e) 0.8 ms, A14 0.7 ms). A test that passes only
with a process-wide timer tweak would hide a real dependency.

### D41 — The camera's own image processing that moves or warps pixels is off
**Decision.** While tracking, the app turns off optical image stabilisation (`LENS_OPTICAL_STABILIZATION_MODE_OFF`),
video stabilisation (`CONTROL_VIDEO_STABILIZATION_MODE_OFF`) and distortion correction
(`DISTORTION_CORRECTION_MODE_OFF`) where the device supports the keys, locks focus by setting `AF_MODE_OFF` with
the focus distance the one-time autofocus found, and uses the `FAST` noise-reduction and edge modes. What it
could not turn off is reported in `status` (M2 plan).
**Why.** The floor fit assumes that a fixed floor point always lands on the same pixel. OIS and stabilisation
shift the image by pixels without the phone moving, which looks like drift (or a moved tag) to the fit;
distortion correction changes the geometry that `hello.camera.distortion` describes (PROTOCOL.md §2 sends raw
corners). An autofocus that keeps hunting changes the scale slightly. `FAST` modes never lower the frame rate.
**Rejected.** Leaving device defaults (they differ between phones and some enable OIS for all streams).

### D42 — Frame recordings for offline diagnosis
**Decision.** `python -m duoware --record-frames` writes every accepted frame of every camera, with the
server's receive time and clock-sync offset, to `data/recordings/<start wall time>.jsonl` from a background
writer thread. Offline tools read these files (M2: the blur report; later: replaying a hardware run through the
localization code).
**Why.** Hardware results must be measured, and questions like "why did car B stop at 14:02:31" or "how many
clean reads at 300 px/s" need the raw observations, not only statistics. JSONL of the wire messages is simple and
replayable. Off by default: an hour at 60 fps is about 200 MB.
**Rejected.** Always recording (disk growth); recording video (not on the tracking path, D1).
