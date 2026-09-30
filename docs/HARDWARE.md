# DUO-WARE 2 — Hardware facts

What the hardware *is*. Measurements and test results go in [HARDWARE_LOG.md](HARDWARE_LOG.md).
Owner answers from 2026-10-01 unless noted. "Open" items must be answered before the milestone named.

## Car A — "DUO-A"

| Item | Fact |
| --- | --- |
| Board | ESP32 DevKit V1 (classic ESP32, Bluetooth Classic SPP via `BluetoothSerial`) |
| Motor driver | L298N |
| Pins (from DUO-WARE 1 `DUO_WARE_R2.ino`, confirmed by the owner) | `ENA 27, IN1 26, IN2 12, ENB 25, IN3 13, IN4 14`; left/right channels swapped (`SWAP_SIDES = true`) |
| Boot caution | GPIO 12 is a strapping pin: must be LOW at boot (the L298N input must not pull it high) |
| Unused | MPU (dead, not replaced), SDA 22 / SCL 21 |
| Motors | "classic" yellow gear motors (TT type), two, differential drive |
| Firmware | new sketch `firmware/car_esp32/` (M3), Bluetooth name `DUO-A`; replaces `DUO_WARE_R2.ino` entirely |
| Open (before M3) | battery type/voltage; Bluetooth MAC (read it after pairing in Windows) |

## Car B — "DUO-B"

| Item | Fact |
| --- | --- |
| Board | Arduino Uno clone (CH340 USB) + HC-05 Bluetooth module |
| Motor driver | L298N |
| Motors | "classic" gear motors (TT type), two, differential drive |
| Firmware | new sketch `firmware/car_uno/` (M3); HC-05 name `DUO-B`, 38400 baud (set once with AT commands) |
| **Open (before M3)** | which Uno pins drive ENA/IN1/IN2/ENB/IN3/IN4; which pins the HC-05 TX/RX use and whether the 5 V → 3.3 V divider on HC-05 RX is fitted; whether the HC-05 may move to hardware serial D0/D1 or AltSoftSerial 8/9 (DECISIONS.md D25); battery type/voltage and whether the Uno shares the motor battery (brownout risk); HC-05 current baud/PIN and whether it is paired with the laptop |

## Both cars

- No IMU, **no wheel encoders**, no battery sensing (battery is shown as "not measured").
- Dimensions are **not measured and never will be** (owner requirement; DECISIONS.md D13). Tag offset from
  the rotation centre and heading offset are measured by the camera in M4 calibration. `footprint_mm` in
  `config/cars.toml` is a rough safety margin only.
- Car tags: ID 1 (80 mm, existing) and ID 5 (80 mm, to print), TOP arrow = car front. Height above the
  floor is not needed (parallax from apparent size, D13).

## Camera

| Item | Fact |
| --- | --- |
| Phone | realme 9 Pro+ (Android; exact Android version to note in M2) |
| Unknown until M2 | Camera2 hardware level and `MANUAL_SENSOR` support, `SENSOR_INFO_TIMESTAMP_SOURCE`, YUV sizes and their maximum fps, whether lens intrinsics/distortion are reported, detection time at 1280 × 720, sustained performance mode / ADPF support, thermal behaviour over 2 hours |
| Connection modes | WIRED: USB tethering, or `adb reverse` over the USB cable when tethering is unavailable; WIRELESS: Wi-Fi (router, laptop hotspot or phone hotspot; 5 GHz fine). Chosen in the app (PROTOCOL.md §4.1). |
| Battery management | realme UI kills background apps aggressively: exempt the app from battery optimisation **and** allow background activity under Settings → Battery → App battery management (the app shows the steps). |
| Mount height, floor area, lighting | not measured (not needed); lighting adequacy for a 3 ms exposure is checked in M2 |

## Laptop

Windows 11, Python 3.13, Node 24, Android Studio (JBR 21 used for Gradle), Arduino IDE (no `arduino-cli`).
Bluetooth: built-in adapter; cars are paired once in Windows settings (HC-05 PIN usually 1234).

## Tags

DICT_4X4_50. DUO-WARE 1 printed kit: car 1 (80 mm), 2/3/4 (90 mm), 10–13 (90 mm), with TOP arrows and a
100 mm check bar. Roles are assigned on the dashboard; the kit is available as the `duoware1_kit` venue preset.
A 3 × 3 road network needs 9 floor tags; more can be printed from the same dictionary.
