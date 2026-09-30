# DUO-WARE 1 reference code (read-only)

Copies of the DUO-WARE 1 files listed in `docs/PROJECT_BRIEF.md` §6, taken on 2026-10-01 from
`C:\Users\khusw\Downloads\Duo_Ware\Duo_Ware`, so sessions without access to the owner's laptop (cloud) can
read them. **Reference only:** read and port with care; never import from here, never edit these files.
Measurements and history behind them: the old project's `context/context.md` (summarised in DECISIONS.md).

| File | Port into DUO-WARE 2 |
| --- | --- |
| `vision/world.py` | anchor fit rules, steady-frame averaging, auto-placement, camera-moved check → `localization/calibration.py`, `floor_tags.py` (M1) |
| `vision/fusion.py` | multi-camera weighting (later); stale-frame rule |
| `vision/lens.py` | lens profiles (M4) |
| `control/event_log.py` | facts-vs-reason log, `why_stopped` → `store/events.py` (M1), analysis (M7) |
| `control/auth.py` | `generate_code`, `codes_match`, `LoginThrottle` → phone pairing (M1), login (M8) |
| `control/navigator.py` | predictive stop + overshoot learning (M5) |
| `simulator/multi_arena.py` | `SimRealism` noise/blur values (M1 simulator uses them as a statistical model, not rendering) |
| `fleet/warehouse/*`, `tests/test_fleet_manager.py` | checkpoint reservation, deadlock handling, stopped-robot recovery and their tests (M6) |

These files import modules that are not copied (`vision.detector`, `config`, …); they are not meant to run here.
