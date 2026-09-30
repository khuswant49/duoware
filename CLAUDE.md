# CLAUDE.md — DUO-WARE 2

Two small cars drive on a road network of floor tags under one overhead Android phone. The phone app
detects ArUco markers and sends their pixel corners over UDP; a Python server on the laptop localizes,
plans and drives the cars over Bluetooth; an admin uses a web dashboard. An LLM (Groq) only explains
decisions from the log. It never commands anything. Requirements: `docs/PROJECT_BRIEF.md`.

## Sources of truth (one per concern)

| Concern | Lives in | Changed by |
| --- | --- | --- |
| Requirements | `docs/PROJECT_BRIEF.md` | owner |
| Wire formats (phone, cars, dashboard, REST) | `PROTOCOL.md` | Opus; a breaking change bumps `v` and updates app + both firmwares + server + dashboard together |
| Why things are the way they are | `DECISIONS.md` | Opus |
| Cars, road-network rules, tuning, server ports, sim world | `config/*.toml` (commented) | Opus or owner; Sonnet only as its plan says |
| Tag roles, measured layout, venue presets, pairings, learned calibration | `data/state.db`, set through the dashboard/API | admin at run time; **never** in code or config |
| Hardware facts / hardware results | `docs/HARDWARE.md` / `docs/HARDWARE_LOG.md` | Opus records what the owner reports |
| Work to do | `docs/plans/M<n>.md`, outline in `docs/plans/ROADMAP.md` | Opus writes; Sonnet fills in Results / Proposed changes |

## How the work is split

- **Opus 5.5 (architect):** architecture, `PROTOCOL.md`, `DECISIONS.md`, control/planning design, one plan per
  milestone with acceptance criteria, reviews, diagnosis of hardware logs.
- **Sonnet 5.5 (developer):** implements **exactly one plan, `docs/plans/M<n>.md`**, in its step order. It does
  not edit `PROTOCOL.md` or `DECISIONS.md`. When the plan is ambiguous, contradicts `PROTOCOL.md` or looks
  wrong, it stops and asks, or, if it can continue safely, writes the issue under "Proposed changes" in the plan
  and follows the plan as written. It fills in "Results" at the end.

## Architecture map

```
android/            phone app (M2): Kotlin (Camera2, service, networking) + C++ (NDK, OpenCV ArUco,
                    ROI tracking). Sends PROTOCOL.md §2-4 over UDP (tether/Wi-Fi) or TCP (adb reverse).
server/             Python 3.13 package `duoware` (FastAPI + asyncio)
  src/duoware/
    __main__.py       python -m duoware [--sim]
    app.py            FastAPI app factory; lifespan starts Services
    settings.py       loads config/*.toml + .env into frozen dataclasses (M1)
    clock.py          monotonic/wall clock, FakeClock for tests (M1)
    protocol/         codecs for every PROTOCOL.md message: phone, car, dashboard models (M1)
    store/            state.db (SQLite, migrations) and the event log (events.db) (M1)
    registry/         tag registry, validation, venue presets (M1)
    safety.py         latched E-stop, "may this change happen while cars move?" (M1)
    ingest/           phone sessions/pairing, UDP (M1) and TCP (M2) frames, clock sync, beacon, adb reverse (M2),
                      camera and link stats (M1)
    localization/     floor calibration from floor tags, parallax, car poses, world model (M1)
    layout/           road network: edges, grid rotation, validation, suggestions, measure (M1)
    api/              REST routers and the two WebSockets (M1)
    sim/              simulator process: phone, cars, world (M1). Speaks the real protocols.
    cars/             car link manager (M3)       control/   motion controllers (M5)
    planning/         A*, reservations (M6)       missions/  (M7)      explain/  Groq, isolated (M7)
  tests/            pytest; one test file per module
dashboard/          React + Vite + TypeScript; SVG map; talks only to /api and /ws/dashboard
firmware/car_esp32  ESP32 DevKit V1 sketch (M3)     firmware/car_uno  Uno + HC-05 sketch (M3)
config/             server.toml, cars.toml, map.toml, tuning.toml, sim.toml, venue_presets/*.toml
data/               runtime state (git-ignored): state.db, events.db
docs/               brief, hardware facts/log, plans
```

Data flow: phone → frames (UDP, or TCP over adb reverse) → `ingest` (session check, clock sync) → `localization` (floor fit, poses)
→ world snapshot → `layout` / `planning` / `control` → `cars` link → car. Everything that happens is logged
by the `store.events` log (facts separate from reasons). The dashboard reads snapshots over
`/ws/dashboard` and sends commands through REST.

## Conventions

- **Units and frames** (PROTOCOL.md §0): mm, degrees clockwise, +x right, +y down, floor frame from the origin
  tag; `_mm`, `_deg`, `_ms`, `_ns`, `_s` suffixes on every name that carries a unit.
- **Time:** control and ages use the monotonic clock via `Clock` (never `time.time()` for logic). Wall time
  is for display and the event log only.
- **No magic numbers:** tuning values come from `config/*.toml` through `settings.py`, each with a comment giving
  its unit and origin. Numbers fixed by PROTOCOL.md are named module constants with a `# PROTOCOL.md §x` comment.
- **Small modules:** aim < 400 lines per file; one responsibility per module; pure logic (geometry, estimators,
  graph, validation) separate from I/O so it can be unit-tested with fake clocks.
- **Nothing hard-codes a tag ID or a spacing.** Roles come from the registry; geometry from the camera.
- **Phone hot path:** no per-frame allocation (Kotlin or C++), no JPEG, no blocking network calls on the
  camera/detection threads; every per-frame number the app shows is measured, `null` when it can't be.
- **Simulation never touches hardware:** `--sim` uses TCP car transports only; the Bluetooth/serial transport
  modules are never imported in sim mode.
- **Honest numbers:** never show a placeholder as a measurement (battery is `"not_measured"`). Never write
  "verified" for anything not run on hardware. Say "simulation only".
- Python: type hints, dataclasses for internal data, pydantic only at the API/WebSocket boundary, `logging`
  (no prints outside `__main__`). TypeScript: strict mode, no `any` in `api/types.ts`.

## Running the tests

Run every suite you touched, and the server suite always, before saying you are done.

```bash
# Server (from the repo root). First time: see README "Server setup".
server/.venv/Scripts/python -m pytest server/tests

# Dashboard
cd dashboard && npm test && npm run build

# Android (Gradle needs JDK 21: Android Studio's bundled JBR; the system Java 26 is too new).
# From M2 the build also needs the NDK and CMake from the SDK Manager.
cd android && JAVA_HOME="/c/Program Files/Android/Android Studio/jbr" ./gradlew assembleDebug
```

On Linux/macOS (cloud sessions) the venv interpreter is `server/.venv/bin/python`; the server needs Python
3.13 (install it, e.g. with `uv python install 3.13`, if the machine has an older one). The Android build
needs the Android SDK: it runs on the owner's laptop and in GitHub Actions (`.github/workflows/android.yml`, from
M2 step 2), which uploads the APKs as artifacts. A cloud session without an SDK relies on that workflow (check it
with `gh run list` / `gh run view` when `gh` is available, otherwise ask the owner) and never claims an Android
build or test passed without seeing the run.

PowerShell equivalent for Android:
`$env:JAVA_HOME="C:\Program Files\Android\Android Studio\jbr"; cd android; .\gradlew.bat assembleDebug`

Firmware is compiled and flashed with the Arduino IDE only (DECISIONS.md D20); there is no automated
firmware build. Firmware behaviour is covered by the simulator's car tests against PROTOCOL.md §5, and each
firmware milestone ends with a hardware checklist the owner runs.

## Working rules

- **Git from day one.** Commit after each working step with a clear message (`M1 step 3: event log`).
  Push after each milestone (only when the owner asks, for Sonnet sessions).
- The repo lives at `C:\Users\khusw\Downloads\duoware`, **outside OneDrive** (sync breaks `.venv`,
  `node_modules` and Gradle builds).
- **One Claude session per folder at a time.** Parallel work only in a separate git worktree/branch.
- **Secrets** (`GROQ_API_KEY`, `DUO_PAIR_CODE`, `DUO_ACCESS_CODE`) only in `.env` (git-ignored);
  `.env.example` holds placeholders.
- Hardware results go to `docs/HARDWARE_LOG.md` with date, setup, method and numbers.
- Don't carry over from DUO-WARE 1: its monolithic `server.py`, the Bluetooth radio-reset auto-connector,
  kick-start threads, the LLM driving pilot, hard-coded pixel coordinates. Never scan random COM ports or
  toggle the Windows Bluetooth radio.
- The old project (`C:\Users\khusw\Downloads\Duo_Ware\Duo_Ware`) is reference only: read, port with care,
  never modify. The files the brief says to reuse are copied in `docs/reference/duoware1/` (read-only, for
  sessions without the owner's laptop, e.g. cloud).
