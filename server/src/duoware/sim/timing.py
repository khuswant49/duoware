"""Windows timer resolution: asyncio sleeps are quantised to the system timer (15.6 ms by default), which would make the
simulated 30 fps stream lumpy. Asking for 1 ms makes it smooth. No effect elsewhere."""

import ctypes
import sys

RESOLUTION_MS = 1


def set_timer_resolution(ms: int = RESOLUTION_MS) -> bool:
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.winmm.timeBeginPeriod(ms)              # type: ignore[attr-defined]
        return True
    except Exception:
        return False
