# DUO-WARE 2 — Hardware log

Every result measured on real hardware, newest first. Simulation results do **not** go here.
Each entry: date, milestone, setup (phone, cars, firmware versions, venue/layout, lighting, network),
what was measured, how many runs, the numbers, and whether they meet the plan's acceptance criteria.

Template:

```
## YYYY-MM-DD — M<n>: <what was tested>
Setup: ...
Method: ...
Results: ...
Meets acceptance: yes / no / partly (which criteria)
Notes: ...
```

## 2026-10-01 — M2: phone checkpoint 1 (OpenCV native smoke test)
Setup: realme RMX3392, Android 14 (API 34), ABIs arm64-v8a / armeabi-v7a / armeabi, USB debugging over cable.
CI build `duoware-sensor-80a9121` (M2 step 2): `app-debug.apk` + `app-debug-androidTest.apk`. No camera, no network.
Method: `adb install -r` both APKs, then
`adb shell am instrument -w -r com.duoware.sensor.test/androidx.test.runner.AndroidJUnitRunner`. One run.
Results: OK (2 tests).
- `openCvLoadsAndReportsItsVersionThreadsAndParallelFramework`: OpenCV 4.14.0, threads 8, cpus 8,
  parallel framework TBB (ver 2022.1 interface 12150).
- `detectsOneGeneratedMarker`: detected ids [7] in 1.57 ms (generate + detect, 240 x 240 px synthetic frame).
Meets acceptance: yes for phone checkpoint 1 (step 2: OpenCV loads on the phone, version / parallel framework /
thread count reported). H1 as a whole is not covered yet: the synthetic-image corner error, the ROI tracker cases
and the allocation check come in later steps.
Notes: the 1.57 ms figure is a single synthetic call, not a detection benchmark.
