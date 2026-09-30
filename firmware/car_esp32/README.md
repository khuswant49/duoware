# Car A firmware — ESP32 DevKit V1 ("DUO-A")

Not written yet: milestone **M3** creates `car_esp32.ino` in this folder.

- Protocol: `PROTOCOL.md` §5 (the only specification; this folder must not add commands).
- Wiring (`docs/HARDWARE.md`): L298N `ENA 27, IN1 26, IN2 12, ENB 25, IN3 13, IN4 14`, left/right swapped.
  GPIO 12 is a boot strapping pin: drive it LOW before anything else and keep it LOW at boot.
- Bluetooth Classic SPP (`BluetoothSerial`), device name `DUO-A`.
- Build: Arduino IDE, board "ESP32 Dev Module" (esp32 core by Espressif). No automated build (DECISIONS.md D20).
