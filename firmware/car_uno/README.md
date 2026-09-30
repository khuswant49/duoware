# Car B firmware — Arduino Uno clone + HC-05 ("DUO-B")

Not written yet: milestone **M3** creates `car_uno.ino` in this folder.

- Protocol: `PROTOCOL.md` §5 (the only specification; this folder must not add commands).
- **Open before M3** (`docs/HARDWARE.md`): L298N pin mapping, HC-05 pins (hardware serial D0/D1 or
  AltSoftSerial 8/9 proposed, DECISIONS.md D25), power supply.
- HC-05: name `DUO-B`, 38400 baud, set once with AT commands; its RX needs a 5 V → 3.3 V divider.
- 2 KB RAM: fixed 32-byte line buffer, no `String`.
- Build: Arduino IDE, board "Arduino Uno" (CH340 driver for the clone). No automated build (DECISIONS.md D20).
