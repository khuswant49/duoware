# DUO-WARE 2 — Protocol v1

The single source of truth for every wire format between the phone app, the server, the cars and
the dashboard. Code implements this document; when they disagree, the code is wrong. Changing it:
see [§9](#9-changing-this-protocol).

| § | Link | Transport |
| --- | --- | --- |
| [2](#2-marker-frames-phone--server-udp) | Marker frames, phone → server | UDP, `frames_port` 47801 |
| [3](#3-clock-sync-server--phone-udp) | Clock sync, server ↔ phone | UDP, same socket as frames |
| [4](#4-discovery-and-the-phone-websocket) | Discovery beacon; phone session, settings, status, preview | UDP broadcast 47800; WebSocket `/ws/phone` |
| [5](#5-car-line-protocol) | Car commands and replies | Bluetooth SPP (RFCOMM) / serial / TCP (simulator only) |
| [6](#6-dashboard-websocket-server--dashboard) | Live state to the dashboard | WebSocket `/ws/dashboard` |
| [7](#7-rest-api) | Dashboard commands, tag registry, road-network layout, venue presets, cameras, events | HTTP `/api/*` |
| [8](#8-event-records) | Event log records | inside §6 and §7 |

Ports are set in `config/server.toml`; the numbers above are the defaults.

---

## 0. Conventions (apply everywhere)

- **Version.** Every JSON message carries `"v": 1` (protocol major version). Cars report it in `ID`
  (§5.3). A receiver drops a message with another `v`, counts it, and reports "protocol mismatch".
  Adding an optional field or a new message/event type is **not** a version change; receivers must
  ignore unknown fields. Removing, renaming or changing the meaning or unit of anything is.
- **Encoding.** JSON, UTF-8, `snake_case` keys, no `NaN`/`Infinity` (use `null`). Car lines are ASCII.
- **Floor frame** (same as DUO-WARE 1): millimetres seen from above, **+x right, +y down the map**,
  origin at the centre of the *origin* tag, whose TOP edge points to −y (DECISIONS.md D13). All
  positions and headings on the wire are in the floor frame.
- **Grid frame:** the floor frame rotated by the measured `grid_rotation_deg` (§7.5), so that columns
  run along +x and rows along +y. Used for display and for naming directions; convert with
  `heading_grid = heading_floor − grid_rotation_deg` (DECISIONS.md D27).
- **Angles:** degrees, **clockwise** from +x, normalised to (−180, 180].
- **Image frame:** pixels of the analysed image buffer as delivered by the sensor (not rotated for
  display), origin at its top-left corner, +x right, +y down.
- **Marker corners** are in ArUco order: 0 top-left, 1 top-right, 2 bottom-right, 3 bottom-left of the
  marker *as printed*. **Marker heading** = direction from the midpoint of edge 3–2 (bottom) to the
  midpoint of edge 0–1 (top). For a car tag the printed TOP arrow points to the car's front.
- **Clocks.** Phone messages use the phone's *sync clock* in integer nanoseconds (§3.1). The server
  uses its monotonic clock internally and converts. Dashboard messages give durations in `*_ms` and
  wall-clock time only in fields named `wall_ms` or `*_wall_ms` (Unix epoch ms, for display and the
  event log; never used for control).
- **Units in names:** `_mm`, `_deg`, `_px`, `_ns`, `_ms`, `_s`, `_pct`. A name without a unit suffix
  is unitless or documented in its table.

---

## 1. Identities

| Thing | Identity | Assigned by |
| --- | --- | --- |
| Server install | `server_id`: 16 lowercase hex, random, persisted in `data/state.db` | server, first start |
| Phone install | `device_id`: UUID string, persisted in the app | phone, first start |
| Camera | `cam`: integer 1–15, persisted per `device_id` | server, at pairing (lowest free) |
| Phone session | `sid`: 16 lowercase hex, random per WebSocket connection | server, in `welcome` |
| Car | `name` from `config/cars.toml`, e.g. `DUO-A`; the firmware reports the same name | config + firmware |
| Tag | ArUco ID 0–49 (`DICT_4X4_50`); its role comes from the tag registry (§7.4) | admin, Tags page |

---

## 2. Marker frames (phone → server, UDP)

One datagram per analysed camera frame, sent to `<server host>:<frames_port>` from `welcome` (§4.2).
**A frame with no markers is still sent** (`"m": []`), so the server can tell "nothing seen" from
"no frames".

```json
{"v":1,"t":"frame","cam":1,"sid":"9f2c4e1a7b3d5c60","seq":48213,
 "cap_ns":81234567890123,"exp_ns":3000000,"skew_ns":18500000,
 "avail_ns":81234612890123,"sent_ns":81234629890123,
 "w":1280,"h":720,
 "m":[[1,612.4,300.1,660.2,301.0,659.5,348.7,611.8,347.9],
      [10,100.2,80.5,146.0,80.9,145.7,126.6,99.8,126.1]]}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `v`, `t` | int, `"frame"` | version, message type |
| `cam` | int | camera ID from `welcome` |
| `sid` | string | session ID from `welcome`; frames with an unknown `sid` are rejected |
| `seq` | uint32 | +1 per analysed frame in this session, starting at 0 |
| `cap_ns` | int64 | sensor timestamp: start of exposure of the **first row**, sync clock |
| `exp_ns` | int64 | exposure time actually used for this frame |
| `skew_ns` | int64 | rolling-shutter skew (first-row to last-row start); `0` if the phone does not report it |
| `avail_ns` | int64 | sync clock when the image reached the analyzer |
| `sent_ns` | int64 | sync clock just before the datagram was sent |
| `w`, `h` | int | analysed buffer size in px |
| `m` | array | one entry per detected marker: `[id, x0, y0, x1, y1, x2, y2, x3, y3]`, corners in px (§0 order), rounded to 0.1 px; raw (no lens correction on the phone) |

**Per-marker capture time** (computed by the server): `cap_ns + exp_ns / 2 + skew_ns × (cy / h)`,
where `cy` is the mean y of the marker's corners. It is converted to server time with §3.

**Rate:** every analysed frame (target `tuning.camera.fps`, 30 Hz). No retransmission, no batching.
**Size:** aim for ≤ 1400 bytes; hard maximum 8192 bytes; never split across datagrams.

**Phone rules.** The camera thread never waits on the network: it hands the packet to a sender with a
queue of depth 1 (newest wins). Send errors are counted, not retried.

**Server rules (error handling).** Every rejection is counted per camera (`rx_*` counters in §6.2)
and logged at most once per minute per cause.

| Condition | Action | Counter |
| --- | --- | --- |
| Not JSON, missing field, wrong type | drop datagram | `rx_bad` |
| `v` ≠ 1 | drop; camera shows "protocol mismatch" | `rx_version` |
| `sid` not a live session, or source IP ≠ that session's WebSocket peer IP | drop | `rx_unauth` |
| `seq` ≤ last accepted `seq` of the session | drop (late or duplicate) | `rx_late` |
| `seq` jumps by *k* > 1 | accept; `dropped += k − 1` | `dropped` |
| Marker ID outside the dictionary, or a corner non-finite or outside [−w, 2w] × [−h, 2h] | ignore that marker only | `rx_bad_marker` |
| Camera `UNSYNCED` (§3.3) | accept for statistics; produce **no** floor poses | — |
| Computed pose age > `tuning.pose.stale_ms` | accept for statistics; the pose is stale (cars stop) | — |

---

## 3. Clock sync (server ↔ phone, UDP)

### 3.1 The phone's sync clock
The sync clock is the clock of the camera's sensor timestamps. The app decides it once the camera
runs, and reports it in `status.clock` (§4.4):

1. `SENSOR_INFO_TIMESTAMP_SOURCE == REALTIME` → `SystemClock.elapsedRealtimeNanos()` → `"boottime"`.
2. Otherwise, on the first frame: if `elapsedRealtimeNanos() − cap_ns` is in [0, 1 s] → `"boottime"`;
   else if `System.nanoTime() − cap_ns` is in [0, 1 s] → `"monotonic"`; else `"unknown"`.

The same clock stamps `avail_ns`, `sent_ns`, `t2` and `t3`. With `"unknown"` the server keeps the
camera `UNSYNCED` (reason "timestamp clock unknown") and it produces no poses.

### 3.2 Exchange
The server sends to the source address (IP and port) of the session's latest frame; the phone answers
**from the socket that sends its frames**, immediately.

```json
{"v":1,"t":"sync","sid":"9f2c4e1a7b3d5c60","n":123,"t1":5512345678901}
{"v":1,"t":"sync_r","cam":1,"sid":"9f2c4e1a7b3d5c60","n":123,"t1":5512345678901,"t2":81234000000000,"t3":81234000040000}
```

| Field | Meaning |
| --- | --- |
| `n` | uint32 request number, per session |
| `t1` | server monotonic ns at send (echoed back unchanged) |
| `t2` | phone sync clock at receive (stamp as early as possible) |
| `t3` | phone sync clock at reply send (stamp as late as possible) |

Server, at receive time `t4`: `rtt = (t4 − t1) − (t3 − t2)`, `θ = ((t2 − t1) + (t3 − t4)) / 2`
(phone clock minus server clock).

**Rate:** every `clock_sync.fast_interval_ms` (100 ms) for the first `fast_period_s` (3 s) of a
session, then every `interval_ms` (500 ms).

### 3.3 Estimator and states
- Discard a sample with `rtt < 0` or `rtt > clock_sync.max_rtt_ms`, or a reply whose `n` was not sent
  in the last 2 s.
- Keep accepted samples for `window_s`. Fit `θ(t) = a + b·(t − t_ref)` by least squares over the
  `best_fraction` of samples with the lowest `rtt` (at least 4; with fewer, use the lowest-`rtt`
  sample and `b = 0`).
- `server_time = phone_time − θ(now)`. Pose age = `now − server_time(marker capture time)`.
- The camera is **`SYNCED`** while an accepted sample is younger than `unsynced_after_s`, otherwise
  **`UNSYNCED`**. Reported as `sync.ok`, `sync.rtt_ms` (lowest in the window) and `sync.samples`.

---

## 4. Discovery and the phone WebSocket

### 4.1 Discovery beacon (server → broadcast, UDP)
Sent every `udp.beacon_interval_s` to port `udp.beacon_port` on the **directed broadcast address of
every active IPv4 interface** (Windows sends `255.255.255.255` out of one interface only), with
`host` set to that interface's own address.

```json
{"v":1,"t":"beacon","server_id":"a3f9c2d17e804b55","name":"DUO-WARE","host":"192.168.42.129","http_port":8000,"frames_port":47801}
```

The app connects to the first beacon whose `server_id` matches its stored pairing, else the first
beacon it hears (preferring one received on the USB-tether interface). It ignores beacons with
another `v` and shows "server speaks protocol vN". A manual host entry in the app is the fallback.

### 4.2 Session: `ws://<host>:<http_port>/ws/phone`
Text frames carry JSON; one binary message type carries the preview (§4.5). The phone sends
`hello` within 5 s of connecting, or the server closes with code 4000.

```json
{"v":1,"t":"hello","device_id":"5b1e0f5e-8a8c-4f53-9b0e-2f4c1f2a6c11","token":null,"pair_code":"K7QX-M2",
 "app_version":"0.2.0","model":"realme RMX3393","android":"13",
 "camera":{"id":"0","hw_level":"FULL","manual_sensor":true,"ts_source":"REALTIME",
           "intrinsics":[1402.1,1402.1,640.3,361.0,0.0],"distortion":[0.021,-0.043,0.0,0.0,0.0],
           "active_array":[4080,3072]}}
```

| Field | Meaning |
| --- | --- |
| `token` | 32 hex issued at an earlier pairing, or `null` |
| `pair_code` | the code shown on the dashboard (only needed without a valid token), or `null` |
| `camera.hw_level` | Camera2 hardware level: `LEGACY`, `LIMITED`, `FULL`, `LEVEL_3`, `EXTERNAL` |
| `camera.manual_sensor` | the camera supports manual exposure/ISO |
| `camera.ts_source` | `REALTIME` or `UNKNOWN` (Camera2 `SENSOR_INFO_TIMESTAMP_SOURCE`) |
| `camera.intrinsics` | Camera2 `LENS_INTRINSIC_CALIBRATION` `[fx, fy, cx, cy, s]` in active-array px, or `null` |
| `camera.distortion` | Camera2 `LENS_DISTORTION` `[k1, k2, k3, p1, p2]`, or `null` |

**Server decision.** A valid `token` for this `device_id` → accept. Otherwise a `pair_code` equal to
the current code (normalised: upper-case, letters and digits only, constant-time compare) → issue a
token, assign `cam`, accept. Otherwise → `error`. Five failures from one IP lock it out for 60 s.

```json
{"v":1,"t":"welcome","cam":1,"sid":"9f2c4e1a7b3d5c60","token":"f0c1...32hex","frames_port":47801,
 "server_id":"a3f9c2d17e804b55","settings":{ ...as in §4.3... }}
{"v":1,"t":"error","code":"bad_pair_code","message":"Pairing code is wrong or expired."}
```

`token` in `welcome` is present only when newly issued (the app stores it); otherwise `null`.

| `error.code` | Close code | When |
| --- | --- | --- |
| `bad_message` | 4000 | not JSON, no `hello` in 5 s, wrong fields |
| `bad_pair_code`, `bad_token` | 4001 | authentication failed |
| `version` | 4002 | `v` ≠ 1 |
| `locked` | 4003 | too many failures from this IP |
| `replaced` | 4004 | a newer session of the same device took over (sent to the old session) |

**Session lifetime.** One live session per camera; a new accepted `hello` from the same device ends the
old one immediately. When the WebSocket closes the session ends: its `sid` is invalid for UDP at
once, and the camera is `OFFLINE`. The server sends WebSocket pings every 5 s and closes a session
with no pong for 10 s. Tokens are revoked from the dashboard (`DELETE /api/pairing/{device_id}`).

### 4.3 `settings` (server → phone)
In `welcome`, and again whenever the camera settings change. Values come from `config/tuning.toml [camera]`.

```json
{"v":1,"t":"settings","resolution":[1280,720],"fps":30,"exposure_ns":3000000,"iso":800,"focus":"locked",
 "preview":{"fps":3,"width":480,"quality":60}}
```

The phone applies them as closely as the camera allows (clamping to supported ranges) and reports
what it achieved in `status`. `focus`: `"locked"` = autofocus once, then lock. `preview.fps` 0 = off.

### 4.4 `status` (phone → server, 1 Hz)

```json
{"v":1,"t":"status","cam":1,"clock":"boottime","fps":29.8,
 "det_ms":{"p50":12.1,"p95":18.0},"cap_to_sent_ms":{"p50":61.0,"p95":74.2},
 "frames_skipped":3,"exposure_ns":3000000,"iso":800,"focus":"locked",
 "thermal":0,"battery_pct":81,"charging":true,"markers_seen":7}
```

| Field | Meaning |
| --- | --- |
| `clock` | `boottime`, `monotonic` or `unknown` (§3.1) |
| `fps` | analysed frames per second over the last second |
| `det_ms`, `cap_to_sent_ms` | detection time and `sent_ns − cap_ns` over the last 10 s, p50/p95 |
| `frames_skipped` | cumulative frames the analyzer dropped (`KEEP_ONLY_LATEST`) |
| `focus` | `locked`, `searching` or `failed` |
| `thermal` | `PowerManager.getCurrentThermalStatus()` 0 (none) … 6 (shutdown) |

A status older than 5 s is shown as "status stale"; it does not stop cars by itself (frames and sync do).

### 4.5 Preview (phone → server, binary WebSocket message)

| Bytes | Content |
| --- | --- |
| 0–3 | ASCII `DWP1` |
| 4–11 | `cap_ns`, uint64 big-endian |
| 12–13, 14–15 | width, height, uint16 big-endian |
| 16– | JPEG |

At most `preview.fps`, made from a downscaled copy **off the tracking thread**; skipped whenever the
phone's send path is busy. The server keeps only the latest per camera and drops messages over 512 KB
or with a bad header.

---

## 5. Car line protocol

### 5.1 Framing
- Transport: Bluetooth Classic SPP (RFCOMM, channel from `cars.toml`), USB serial, or TCP (simulator
  only). HC-05 UART: 38400 baud 8N1, set once with AT commands.
- ASCII lines ending in `\n`; `\r` is ignored. **At most 31 characters before `\n`** (the Uno's
  buffer is 32 bytes). Fields are separated by one space; numbers are decimal integers.

### 5.2 Server → car

| Line | Arguments | Effect | Reply |
| --- | --- | --- | --- |
| `?` | — | identify | `ID …` |
| `M <l> <r> <ttl>` | `l`, `r`: −255…255; `ttl`: 1…500 ms (larger values are clamped to 500) | left/right motor PWM, sign = direction (+ = forward), replacing any current motion at once; after `ttl` ms without a newer `M` the car stops as for `S`. A wheel at 0 is braked. | none |
| `S` | — | stop now | `OK S` |
| `P <n>` | `n`: 0…65535 | ping | `P <n>` |
| `L <s>` | `s`: 0 or 1 | status LED off/on (latency tests) | none |

**Stop** (for `S`, TTL expiry and every error): brake both motors (L298N: enable high, both inputs
equal) for `BRAKE_MS` = 150 ms, then release (enable low).

`M` doubles as a **timed pulse**: a single `M 140 -140 60` turns the car for exactly 60 ms measured by
the car's own clock, so Bluetooth jitter shifts only when a pulse starts, not its length (DECISIONS.md D12).

### 5.3 Car → server

| Line | Meaning |
| --- | --- |
| `ID <name> <fw> <proto> <caps> <uptime_s>` | e.g. `ID DUO-A 1.0.0 1 ttl,led 734`. `caps` is a comma list without spaces (`ttl`, `led`; reserved for later: `gyro`, `enc`, `batt`). A smaller `uptime_s` than last time means the car rebooted. |
| `P <n>` | ping echo |
| `OK S` | stop done (after the motors were set to brake) |
| `X <count>` | the car stopped by itself because a TTL expired; `count` = expiries since boot (wraps at 65535). Sent once per expiry. |
| `BOOT <name> <fw> <reset>` | once after start-up; `reset` ∈ `power`, `brownout`, `watchdog`, `software`, `external`, `unknown`. ESP32: on the first SPP connection after boot. Uno: at start-up (may be lost if no link yet). |
| `E <code>` | error, `code` ∈ `parse`, `range`, `long`, `unknown`. **Every error also stops the car.** |

### 5.4 Car rules (both firmwares)
- Motors are stopped at power-up and move only on `M`. **Every** motion has a TTL of at most 500 ms.
- ESP32: an SPP disconnect stops the motors at once. Uno: it cannot see the link, so the TTL is its only guard.
- No kick-start, ramps, trims or speed limits in firmware: all of that is server-side (DECISIONS.md D10).
- Lines are handled in arrival order; a complete line is acted on within 5 ms of its `\n`.
- A line longer than 31 characters: discard it up to the next `\n`, reply `E long`, stop.

### 5.5 Server rules
- **One writer per car link**, with a queue. `S` goes to the head of the queue and removes queued `M`s.
- While a car should keep moving, send a fresh `M` every `refresh_ms` with the full `ttl` (values in
  `tuning.toml` from M3; `refresh_ms` ≤ `ttl/3`).
- On (re)connect: send `S`, then `?`. `ID` must arrive within 2 s with `name` equal to the car's
  `cars.toml` name and `proto` = 1, or the server disconnects and logs `link_identity` / `link_version`.
- Ping with `P` at a fixed rate; RTT = echo delay. Link `degraded` after 2 s without an echo, reconnect
  after 5 s (exact values in `tuning.toml` from M3).
- `E`, `X` and `BOOT` lines become events (§8). A line from the car over 64 characters or not
  matching §5.3 is counted (`rx_bad`) and ignored.

---

## 6. Dashboard WebSocket (server → dashboard)

`ws://<host>:<http_port>/ws/dashboard`, local clients only (DECISIONS.md D17). **Read-only:** the
dashboard sends nothing (anything it sends is ignored); commands go through REST (§7).

### 6.1 `hello` (once, on connect)

```json
{"v":1,"t":"hello","server_id":"a3f9c2d17e804b55","version":"0.1.0","mode":"sim","state_hz":20}
```

### 6.2 `state` (full snapshot, `dashboard.state_hz` per second)
A complete snapshot every time (no deltas). If a client's socket cannot keep up, snapshots are
skipped for that client, never queued.

```json
{"v":1,"t":"state","seq":1042,"wall_ms":1790000000000,
 "system":{"estop":false,"estop_since_wall_ms":null,"estop_by":null,"mode":"sim",
           "registry_version":7,"layout_version":3,"events_last_id":1234},
 "cameras":[{"cam":1,"online":true,"model":"realme RMX3393",
   "sync":{"ok":true,"rtt_ms":1.8,"samples":40},
   "calib":{"status":"OK","model":"homography","rms_mm":1.9,"residual_mm":2.2,
            "anchors_used":[10,11,12,13],"anchors_seen":[10,11,12,13]},
   "fps":29.8,"det_ms_p50":12.1,"cap_to_sent_ms_p50":61.0,
   "pose_age_ms":{"p50":58.2,"p95":71.0,"max":95.4,"n":298},
   "dropped":3,"rx_bad":0,"rx_unauth":0,"rx_late":0,"thermal":0,
   "exposure_ms":3.0,"iso":800,"preview_seq":57,"status_age_ms":420}],
 "tags":[{"id":10,"role":"anchor","label":null,"seen":true,"cams":[1],
          "x_mm":0.0,"y_mm":0.0,"heading_deg":-90.0,"age_ms":61,
          "size_mm":90.3,"size_warn":false,"placed":true}],
 "cars":[{"name":"DUO-A","tag":1,"color":"#e4572e",
          "link":{"state":"disconnected","rtt_ms":null,"fw":null},
          "pose":{"x_mm":3.2,"y_mm":-1.7,"heading_deg":7.4,"age_ms":64,"fresh":true,"at_node":10},
          "motion":null,"stopped_reason":"no_link","battery":"not_measured"}]}
```

| Path | Meaning |
| --- | --- |
| `system.mode` | `sim` or `hardware` |
| `system.registry_version`, `layout_version` | refetch `GET /api/tags` / `GET /api/layout` when these change |
| `cameras[].online` | a phone session is live |
| `cameras[].calib.status` | `UNCALIBRATED`, `WEAK` (1 anchor), `OK`, `MISALIGNED` |
| `cameras[].pose_age_ms` | capture→server-receive age of accepted frames over `pose.stats_window_s`; `null` fields while `UNSYNCED` |
| `tags[]` | every tag seen within `pose.stale_ms` by any camera, plus every registered tag. `role` is `"unassigned"` for tags not in the registry. Floor fields are `null` when no calibrated, synced camera sees the tag (a registered unseen tag keeps its last known position with `seen: false`). `size_mm` = measured size on the floor. `placed` (floor tags only) = its position is known and it is used for calibration. |
| `cars[]` | every car in `cars.toml`. `tag` = bound tag ID or `null`. `pose` is `null` without a bound, seen tag. The pose is the car's **rotation centre** (tag position corrected for parallax and the tag's `offset_mm`), heading corrected by `heading_offset_deg`. `pose.fresh` = `age_ms` ≤ `pose.stale_ms`. `pose.at_node` = node tag ID within `tracking.at_node_tol_mm`, else `null`. |
| `cars[].link.state` | `disconnected`, `connecting`, `connected`, `degraded`, `error` |
| `cars[].motion` | last command sent: `{"left":…, "right":…, "ttl_ms":…}` or `null` when stopped |
| `cars[].stopped_reason` | why the car may not move now: `null` (may move), or the first that applies in this order: `estop`, `no_link`, `no_tag`, `unsynced`, `uncalibrated`, `misaligned`, `stale_pose`, `bubble`, `operator`, `idle` |
| `cars[].battery` | always `"not_measured"` in v1 (no battery sensing; never a made-up number) |

### 6.3 `event` (as each event is logged)

```json
{"v":1,"t":"event","event":{ ...one record as in §8... }}
```

---

## 7. REST API

### 7.1 General rules
- JSON in and out. Errors: `{"error":{"code":"…","message":"…","details":{…}}}` with HTTP 400
  (validation), 403 (`remote_forbidden`, `bad_origin`), 404 (`not_found`), 409 (conflicts).
- **Local only:** requests from addresses other than 127.0.0.1 / ::1 get 403 `remote_forbidden`
  (M8 adds a login). Exempt: `GET /api/health` and `POST /api/control/stop_all`.
- **Origin check:** `POST`/`PUT`/`DELETE` with an `Origin` header not in `access.allowed_origins` get 403
  `bad_origin`. Exempt: `POST /api/control/stop_all` (stopping can never do harm).
- The operator recorded in events is `"local"` until M8 adds named logins.

### 7.2 Endpoints

| Method and path | Body → response | Milestone |
| --- | --- | --- |
| `GET /api/health` | → `{"status":"ok","version":"0.1.0","proto":1,"mode":"sim","uptime_s":12.3}` | M0 |
| `GET /api/config` | → read-only summary: `cars` (name, priority, footprint_mm, color, transport), `pose.stale_ms`, `markers` (dictionary, default_size_mm), `roles`, `station_kinds`, `layout_rules` (the `[validation]` and `[tracking]` values of `map.toml`) | M1 |
| `POST /api/control/stop_all` | `{}` → `{"estop":true}`. Latches the E-stop; idempotent | M1 |
| `POST /api/control/resume` | `{"confirm":true}` → `{"estop":false}`; without `confirm: true` → 400 `confirm_required` | M1 |
| `GET /api/tags` | → §7.4 | M1 |
| `PUT /api/tags/{id}` | tag document + `expected_version` → stored document | M1 |
| `DELETE /api/tags/{id}?expected_version=n` | → `{"id":…,"role":"unassigned"}` | M1 |
| `POST /api/tags/batch` | `{"changes":[{"id","doc","expected_version"}]}` → all stored documents; all-or-nothing (`doc: null` = unassign) | M1 |
| `GET /api/layout` | → §7.5 | M1 |
| `POST /api/layout/suggest` | `{}` → `{"grid_rotation_deg","spacing_mm","suggestions":[{"id","grid":[r,c]}],"conflicts":[{"ids":[…],"grid":[r,c]}]}` for every seen floor tag with role `node`; nothing is stored | M1 |
| `POST /api/layout/measure` | `{"expected_layout_version"}` → new layout (§7.5). Stores the live averaged positions of all nodes with a `grid`. 409 `nodes_not_visible` (with `details.ids`) if one is not currently seen by a calibrated camera. Motion-affecting (§7.4 safety rule). | M1 |
| `PUT /api/layout/blocked` | `{"nodes":[ids],"edges":[[a,b]],"expected_layout_version"}` → new layout. Admin-blocked nodes and edges; allowed while cars move (planning uses it from its next plan). | M1 |
| `GET /api/venue-presets` | → `[{"name","description","builtin","tag_count","has_layout","has_camera"}]` | M1 |
| `POST /api/venue-presets` | `{"name","description"?,"overwrite"?}` saves the current registry, layout and camera overrides | M1 |
| `POST /api/venue-presets/{name}/apply` | `{"expected_registry_version"?}` → `{"registry_version","layout_version","check":{"matched":[ids],"moved":[{"id","distance_mm"}],"missing":[ids]}}`. `check` compares the preset's measured positions with what the camera sees now; moved nodes are marked `moved` (§7.5). Motion-affecting. | M1 |
| `DELETE /api/venue-presets/{name}` | → 204; built-in → 409 `preset_builtin` | M1 |
| `GET /api/cameras` | → list, same objects as `state.cameras` | M1 |
| `PUT /api/camera-settings` | `{"exposure_ms"?,"iso"?}` overrides the `tuning.toml` defaults (saved with venue presets); `null` clears an override. Sent to phones as `settings` (§4.3). | M1 |
| `POST /api/cameras/{cam}/recalibrate` | `{}` → drops the fit and refits from visible floor tags | M1 |
| `GET /api/cameras/{cam}/preview.jpg` | → latest JPEG, header `X-Capture-Age-Ms`; 404 if none | M2 |
| `GET /api/pairing` | → `{"pair_code":"K7QX-M2","devices":[{"device_id","cam","model","last_seen_wall_ms"}]}` | M1 |
| `DELETE /api/pairing/{device_id}` | revokes the token and ends its session | M1 |
| `GET /api/events?since_id=&limit=&car=&type=` | → `{"events":[§8…],"last_id":n}`; `limit` ≤ 1000, default 200 | M1 |
| Car link, manual drive, per-car stop | defined in M3 (additive) | M3 |

### 7.3 Tag roles

| Role | What it is | Used for |
| --- | --- | --- |
| `car` | the tag on a car, bound to one `cars.toml` entry | car pose |
| `node` | a floor tag at a road-network node, with a (row, column); may also be a **station** | road network (§7.5); floor calibration |
| `anchor` | a floor tag used only for calibration (for example outside the driving area) | floor calibration |
| `obstacle` | marks something in the way | blocks nodes and edges within `tracking.obstacle_block_radius_mm` |
| `ignore` | a tag to leave alone | nothing |

`node` and `anchor` tags are **floor tags**: fixed on the floor, located automatically relative to the
**origin** (exactly one floor tag has `origin: true`), and used to fit every camera's image→floor
mapping (DECISIONS.md D28). A tag not in the registry is **unassigned**: shown on the Tags page,
ignored by calibration, planning and control. Unassigned is the *absence* of a record, never a stored role.

### 7.4 Tag registry
**Tag document** (what `PUT` takes and what `GET` returns):

| Field | Roles | Type / rule |
| --- | --- | --- |
| `id` | all | 0–49 (from the path; the body may omit it) |
| `role` | all | one of §7.3 |
| `size_mm` | all | printed black-square side, 10–500. Required for `car`, `node`, `anchor`; others default to `markers.default_size_mm` |
| `label` | all | optional free text ≤ 40 chars, or `null` |
| `car` | car | name from `cars.toml` (required) |
| `offset_mm` | car | `[forward, left]` from the car's rotation centre (axle midpoint) to the tag centre, or `null` = not measured yet (used as `[0, 0]`). Normally written by M4 calibration. |
| `heading_offset_deg` | car | car heading minus tag heading, or `null` = not measured (used as 0) |
| `origin` | node, anchor | `true` for the one floor tag that defines the floor frame (§0); default `false` |
| `pose` | node, anchor | `{"x_mm","y_mm","yaw_deg"}` typed in, or `null` = locate automatically (normal). Not allowed on the origin (always `0, 0, −90`). |
| `grid` | node | `[row, col]` (integers ≥ 0), or `null` = not part of the network yet. Unique among nodes. |
| `station` | node | `{"name","kind"}` or `null`. `name` 1–24 chars, unique among stations; `kind` ∈ `pickup`, `dropoff`, `home`, `charging`, `custom` |
| `radius_mm` | obstacle | blocking radius, 0–1000; `null` = `tracking.obstacle_block_radius_mm` |
| `placed` | node, anchor | **read-only**: position known (origin, typed in, or auto-located) |
| `version` | all | **read-only**: +1 on every change of this tag |
| `updated_wall_ms`, `updated_by` | all | **read-only** |

`PUT /api/tags/{id}` replaces the whole record and must carry `"expected_version"`: the version the
client last saw (0 for an unassigned tag). `GET /api/tags` returns
`{"registry_version":n,"tags":[…]}`, where each entry is the document plus the live fields of
`state.tags` (§6.2); unassigned tags that are currently seen appear with `role: "unassigned"` and `version: 0`.

**Validation and conflicts** (HTTP 400 or 409, `details` names the fields or other tags):

| Code | HTTP | When |
| --- | --- | --- |
| `validation` | 400 | a field is missing, has the wrong type, is out of range, or does not belong to the role |
| `bad_tag_id` | 400 | ID outside the dictionary |
| `version_conflict` | 409 | `expected_version` ≠ current version (`details.current` has the stored document) |
| `unknown_car` | 409 | `car` is not in `cars.toml` |
| `car_already_bound` | 409 | another tag is bound to that car (a car has exactly one tag) |
| `grid_position_taken` | 409 | another node has that `[row, col]` |
| `station_name_taken` | 409 | another station has that name |
| `origin_exists` | 409 | another floor tag is already the origin (clear it first) |
| `requires_stopped` | 409 | safety rule below; `details.moving` lists the cars |

**Safety rule.** A change is *motion-affecting* when the old or the new record has role `car`, `node`
or `anchor` (any field), and for layout `measure` and preset `apply`. It is allowed only while the
E-stop is latched **or** every car is stopped (no motion command in effect, no autonomous task).

**Effects.** A change to a floor tag's role, size, origin or pose resets every camera's floor fit
(cameras refit from the floor tags; cars have no fresh pose meanwhile, so they cannot move). A change to
a node's `grid` or `station`, or to an obstacle, bumps `layout_version`; planning uses it from its next
plan. A node whose `grid` changes is `unmeasured` until the next `measure`. Every change (including a
batch or preset apply, logged as one event) is an event of type `registry` with old and new documents (§8).

### 7.5 Layout (`GET /api/layout`)
The road network: nodes from the registry (topology), positions from the last `measure` (geometry).

```json
{"version":3,"measured_wall_ms":1790000000000,"grid_rotation_deg":8.1,
 "nodes":[{"id":10,"grid":[0,0],"x_mm":0.0,"y_mm":0.0,"station":null,"state":"ok","blocked_by":null},
          {"id":2,"grid":[0,1],"x_mm":454.2,"y_mm":57.8,"station":{"name":"pickup","kind":"pickup"},"state":"ok","blocked_by":null},
          {"id":6,"grid":[1,1],"x_mm":null,"y_mm":null,"station":null,"state":"unmeasured","blocked_by":null}],
 "edges":[{"a":10,"b":2,"length_mm":457.9,"bearing_deg":7.3,"angle_error_deg":-0.8,"state":"ok","blocked_by":null}],
 "warnings":[{"code":"edge_angle","ids":[2,11],"message":"Edge 2-11 is 16.2° off its row direction (max 15°)."}],
 "footprints":[{"cam":1,"polygon_mm":[[-910,-209],[1610,-209],[1610,1209],[-910,1209]]}]}
```

| Field | Meaning |
| --- | --- |
| `grid_rotation_deg` | fitted direction of the columns (+x of the grid frame) in the floor frame, from the measured edges; `0` before the first measure |
| `nodes[].state` | `ok`; `unmeasured` (has a `grid` but no measured position); `blocked` (by `blocked_by`: `"admin"`, `"config"` or `"tag:<id>"` for an obstacle); `moved` (seen more than `tracking.node_move_tol_mm` from its measured position for `node_move_confirm_s`; treated as blocked until re-measured or back in place); `outside_view` (not inside any usable camera view) |
| `edges[]` | one per pair of **orthogonal neighbours**: nodes `(r, c)`–`(r, c+1)` and `(r, c)`–`(r+1, c)`, both present. Never diagonal, never across a missing node. `a` is the node with the smaller `(row, col)`. |
| `edges[].length_mm`, `bearing_deg` | measured distance and direction from `a` to `b` (floor frame); `null` if either node is unmeasured |
| `edges[].angle_error_deg` | bearing minus the ideal row/column direction (`grid_rotation_deg`, or +90° for a column edge), in (−180, 180] |
| `edges[].state` | `ok`, `blocked` (`blocked_by` as for nodes; an edge touching a blocked or moved node is blocked too) |
| `warnings[].code` | `edge_angle` (> `validation.max_edge_angle_deg`), `edge_short` (< longest car footprint + `edge_clearance_mm`), `node_outside_view` (within `view_margin_mm` of every view edge), `spacing_mismatch` (only if `expected_spacing_mm` > 0), `isolated_node` (no usable edge). Warnings never change the layout. |
| `footprints[]` | each calibrated camera's view on the floor |

Without a calibrated camera or before the first `measure`, nodes are `unmeasured` and edges have
`null` geometry. The layout (measured positions, admin blocks, rotation) is stored in `data/state.db`
and saved in venue presets.

## 8. Event records

```json
{"id":1234,"wall_ms":1790000000000,"mono_ms":5512345,"type":"registry","car":null,
 "operator":"local","key":"tag:5","value":"car","prev":"unassigned",
 "facts":{"old":null,"new":{"id":5,"role":"car","size_mm":80,"car":"DUO-B"}},
 "reason":"operator assigned a role on the Tags page"}
```

| Field | Meaning |
| --- | --- |
| `id` | increasing integer, never reused within one `events.db` |
| `wall_ms`, `mono_ms` | wall clock (display) and server monotonic (ordering, durations) |
| `type` | v1 types: `registry` (tag changes, batches, preset apply), `layout` (measure, admin blocks, node moved/back, validation warnings), `operator` (E-stop, resume, camera settings, pairing), `camera` (online/offline, sync ok/lost, calibration status, misaligned, floor tag moved), `system` (start/stop). Later milestones add types. |
| `car` | car name, or `null` |
| `operator` | who acted, or `null` for automatic events |
| `key`, `value`, `prev` | for state changes: what changed, the new value and the previous one |
| `facts` | **measured** values only (positions, ages, residuals, what was sent) |
| `reason` | the rule or cause the software applied, in plain words. Never AI text: AI explanations (M7) are stored separately and labelled. |

---

## 9. Changing this protocol

1. Propose the change (a Sonnet session writes it under "Proposed changes" in its plan; Opus decides).
2. Additive changes: update this file and every implementation that should use the new field.
3. Breaking changes: bump `v` to the next integer, and update **the app, both firmwares, the server
   and the dashboard** in the same milestone. Record the reason in DECISIONS.md.
