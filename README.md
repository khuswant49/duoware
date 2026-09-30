# DUO-WARE 2

Two small cars on a warehouse-floor road network, tracked by one overhead Android phone, planned and
driven by a laptop server, supervised from a web dashboard. Rebuild of DUO-WARE 1.

- Requirements: [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md)
- Wire formats: [PROTOCOL.md](PROTOCOL.md) · Design decisions: [DECISIONS.md](DECISIONS.md)
- Working rules and architecture map: [CLAUDE.md](CLAUDE.md)
- Plans: [docs/plans/](docs/plans/) · Hardware: [docs/HARDWARE.md](docs/HARDWARE.md), [docs/HARDWARE_LOG.md](docs/HARDWARE_LOG.md)

**Status:** M0 (design and skeletons). Only the health endpoint, an empty dashboard and an empty app exist.
Features arrive milestone by milestone ([roadmap](docs/plans/ROADMAP.md)).

## Layout

| Folder | What |
| --- | --- |
| `server/` | Python 3.13 server package `duoware` (and the simulator, `duoware.sim`) |
| `dashboard/` | React + Vite + TypeScript web dashboard |
| `android/` | Kotlin phone app (marker sensor) |
| `firmware/car_esp32/`, `firmware/car_uno/` | Car sketches (Arduino IDE) |
| `config/` | Commented TOML configuration and venue presets |
| `docs/` | Brief, hardware facts and log, milestone plans |
| `data/` | Runtime state, created on first run (git-ignored) |

## One-time setup (Windows 11)

1. Copy `.env.example` to `.env` and fill in what you need (a fixed `DUO_PAIR_CODE` is handy for the simulator).
2. **Server:**
   ```bash
   python -m venv server/.venv
   server/.venv/Scripts/python -m pip install -e "server[dev]"
   ```
3. **Dashboard:**
   ```bash
   cd dashboard && npm install
   ```
4. **Android:** Android Studio installed; the phone connected with USB debugging (`adb devices` lists it).
   From M2: install **NDK (Side by side)** and **CMake** in Android Studio → SDK Manager → SDK Tools.
   Gradle must run on Android Studio's bundled JDK 21 (the system Java 26 is too new for Gradle 8.13).
   Create `android/local.properties` with `sdk.dir=C:/Users/<you>/AppData/Local/Android/Sdk` (forward slashes).
   Android Studio creates this file for you when you open the `android/` folder.
5. **Windows firewall:** on the first server start, allow Python on private networks (UDP 47800/47801, TCP 8000
   and 47802). Without this the phone can't reach the server over tethering or Wi-Fi (adb reverse is unaffected).

## Run

**Server**
```bash
server/.venv/Scripts/python -m duoware          # hardware mode
server/.venv/Scripts/python -m duoware --sim    # simulation mode: never opens Bluetooth or serial ports
```
Health check: <http://127.0.0.1:8000/api/health>. Once built, the dashboard is served at <http://127.0.0.1:8000/>.

**Simulator** (M1+; in a second terminal, with the server in `--sim` mode)
```bash
server/.venv/Scripts/python -m duoware.sim
```
Then on the dashboard's Tags page apply the `sim_3x3` venue preset and press **Measure layout**.

**Dashboard**
```bash
cd dashboard
npm run dev      # development on http://localhost:5173 (proxies /api and /ws to :8000)
npm run build    # production build into dashboard/dist, served by the Python server
```

**Android app**
```bash
cd android
JAVA_HOME="/c/Program Files/Android/Android Studio/jbr" ./gradlew installDebug
```
Or open `android/` in Android Studio and press Run. The M0 app only shows an empty status screen.

**Firmware** (M3+): open `firmware/car_esp32/car_esp32.ino` (board "ESP32 Dev Module") or
`firmware/car_uno/car_uno.ino` (board "Arduino Uno") in the Arduino IDE and upload. See each folder's README.

## Tests

```bash
server/.venv/Scripts/python -m pytest server/tests
cd dashboard && npm test && npm run build
cd android && JAVA_HOME="/c/Program Files/Android/Android Studio/jbr" ./gradlew assembleDebug
```
