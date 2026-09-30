# DUO-WARE 2

Two small cars on a warehouse-floor road network, tracked by one overhead Android phone, planned and
driven by a laptop server, supervised from a web dashboard. Rebuild of DUO-WARE 1.

- Requirements: [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md)
- Wire formats: [PROTOCOL.md](PROTOCOL.md) · Design decisions: [DECISIONS.md](DECISIONS.md)
- Working rules and architecture map: [CLAUDE.md](CLAUDE.md)
- Plans: [docs/plans/](docs/plans/) · Hardware: [docs/HARDWARE.md](docs/HARDWARE.md), [docs/HARDWARE_LOG.md](docs/HARDWARE_LOG.md)

**Status:** M1 done in simulation (server core, simulator, tag registry, road-network layout, dashboard live map).
Nothing has been run on hardware yet; the Android app and firmware are still M0 skeletons. Features arrive
milestone by milestone ([roadmap](docs/plans/ROADMAP.md)).

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
   server/.venv/Scripts/python -m pip install -e "server[dev]"   # also installs psutil (real netmasks for the beacon)
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
Options: `--config-dir`, `--data-dir`, `--host`, `--port`. At start it prints the phone pairing code (or uses
`DUO_PAIR_CODE` from `.env`) and the dashboard URL. Health check: <http://127.0.0.1:8000/api/health>. The built
dashboard (`npm run build`) is served at <http://127.0.0.1:8000/>.

**Simulator** (second terminal, server in `--sim` mode; give both the same `DUO_PAIR_CODE`, in `.env` or the
environment)
```bash
server/.venv/Scripts/python -m duoware.sim
```
The simulated phone finds the server by its beacon (or uses `config/sim.toml [server]`), pairs, streams frames and
answers clock sync; two scripted cars drive back and forth. Then on the dashboard's **Tags** page apply the
`sim_3x3` venue preset, wait ~10 s for the camera to calibrate, and press **Measure layout** (press it again if a
moving car hides a node). The **Live** page shows the map, the camera panel and the latest events.

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
Or open `android/` in Android Studio and press Run. The app only shows a placeholder screen until M2 step 10; the native
library (OpenCV) is covered by the instrumented smoke test.

### Installing a CI build (the phone app built by GitHub Actions)

`.github/workflows/android.yml` builds the app on every push that touches `android/**` (cloud sessions have no Android
SDK). Each run uploads the artifact `duoware-sensor-<short sha>` with `app-debug.apk`, `app-release.apk`,
`app-debug-androidTest.apk` and the JVM unit-test reports. Windows PowerShell, with the `gh` CLI logged in
(`gh auth login`) and the phone on `adb devices`:

```powershell
gh run list --branch m2 --workflow android.yml -L 3
gh run download <run id> -D build\ci-apks
adb install -r build\ci-apks\duoware-sensor-<sha>\app-release.apk
# instrumented tests: the debug app plus the test APK
adb install -r build\ci-apks\duoware-sensor-<sha>\app-debug.apk
adb install -r build\ci-apks\duoware-sensor-<sha>\app-debug-androidTest.apk
adb shell am instrument -w -r com.duoware.sensor.test/androidx.test.runner.AndroidJUnitRunner
```

`build/` at the repository root is git-ignored. After the instrumented tests, reinstall the release APK for
measurements (every hardware measurement uses the release build). The smoke test prints the device, the OpenCV version,
thread count and parallel framework as `INSTRUMENTATION_STATUS: opencv=...` lines in the `am instrument` output (and in
logcat under the tag `DuoSmoke`, `adb logcat -d -s DuoSmoke`); `INSTRUMENTATION_CODE: -1` at the end means all tests passed.

**Shared test signing key (once).** Every machine has its own debug key, so an APK built by CI would not install over one
built on the laptop, and uninstalling wipes the app's `device_id` and pairing token. One key signs every build instead.
It is a test key for sideloading only and never signs anything published. Keep the file **outside** the repository:

```powershell
mkdir $env:USERPROFILE\.duoware -Force
& "C:\Program Files\Android\Android Studio\jbr\bin\keytool.exe" -genkeypair -v -keystore $env:USERPROFILE\.duoware\duo-test.jks -alias duo -keyalg RSA -keysize 2048 -validity 10000
```

Add four repository secrets (GitHub → Settings → Secrets and variables → Actions, or `gh secret set NAME`):
`DUO_KEYSTORE_B64` (the output of `[Convert]::ToBase64String([IO.File]::ReadAllBytes("$env:USERPROFILE\.duoware\duo-test.jks"))`),
`DUO_KEYSTORE_PASSWORD`, `DUO_KEY_ALIAS` (`duo`) and `DUO_KEY_PASSWORD`. For laptop builds create the git-ignored
`android/keystore.properties`:

```properties
storeFile=C:/Users/<you>/.duoware/duo-test.jks
storePassword=<the keystore password>
keyAlias=duo
keyPassword=<the key password>
```

Without the key, builds fall back to the machine's debug key and print "shared test key missing". The job summary of each
CI run says whether the shared key was used and shows the release APK's certificate digest.

**Windows firewall (once, administrator PowerShell).** Tether and hotspot networks are usually classified "Public", where
Python's first-run prompt often does not apply:

```powershell
New-NetFirewallRule -DisplayName "DUO-WARE frames UDP" -Direction Inbound -Protocol UDP -LocalPort 47801 -Action Allow -Profile Any
New-NetFirewallRule -DisplayName "DUO-WARE HTTP and phone WebSocket" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any
New-NetFirewallRule -DisplayName "DUO-WARE frames TCP" -Direction Inbound -Protocol TCP -LocalPort 47802 -Action Allow -Profile Any
```

**Firmware** (M3+): open `firmware/car_esp32/car_esp32.ino` (board "ESP32 Dev Module") or
`firmware/car_uno/car_uno.ino` (board "Arduino Uno") in the Arduino IDE and upload. See each folder's README.

## Tests

```bash
server/.venv/Scripts/python -m pytest server/tests
cd dashboard && npm test && npm run build
cd android && JAVA_HOME="/c/Program Files/Android/Android Studio/jbr" ./gradlew assembleDebug
```
