"""Clock-offset estimator for one phone session (PROTOCOL.md §3.3, DECISIONS.md D3).

Pure: no I/O, no clock of its own. Times are integer nanoseconds: `t1`/`t4` server monotonic, `t2`/`t3`
phone sync clock. theta = phone clock minus server clock.
"""

from collections import deque

import numpy as np

from duoware.settings import ClockSyncCfg

MIN_FIT_SAMPLES = 4          # PROTOCOL.md §3.3: fit over at least 4 samples, otherwise the lowest-rtt one with b = 0
# The fitted slope is used only when the chosen samples span at least this fraction of the window. Over shorter
# spans the ~1 ms asymmetry noise of a sample swamps a crystal's ~30 ppm drift and the slope makes predictions
# worse (measured with the simulated 0-3 ms one-way jitter); the intercept alone is then the better estimate.
SLOPE_MIN_WINDOW_FRACTION = 0.25
NS_PER_S = 1_000_000_000
NS_PER_MS = 1_000_000


class ClockSync:
    def __init__(self, cfg: ClockSyncCfg) -> None:
        self._cfg = cfg
        self._samples: deque[tuple[int, int, int]] = deque()      # (t4_ns, theta_ns, rtt_ns)
        self._newest_t4: int | None = None
        self._fit: tuple[int, float, float] | None = None         # (t_ref_ns, theta_ref_ns, slope ns/ns)

    def add(self, t1: int, t2: int, t3: int, t4: int) -> bool:
        """One sync exchange. Returns False (sample discarded) for rtt < 0 or rtt > max_rtt_ms."""
        rtt = (t4 - t1) - (t3 - t2)
        if rtt < 0 or rtt > self._cfg.max_rtt_ms * NS_PER_MS:
            return False
        theta = ((t2 - t1) + (t3 - t4)) // 2
        self._samples.append((t4, theta, rtt))
        self._newest_t4 = t4
        horizon = t4 - int(self._cfg.window_s * NS_PER_S)
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()
        self._fit = None
        return True

    @property
    def samples(self) -> int:
        return len(self._samples)

    @property
    def rtt_ms_min(self) -> float | None:
        return min(s[2] for s in self._samples) / NS_PER_MS if self._samples else None

    def ok(self, now_ns: int) -> bool:
        """SYNCED while an accepted sample is younger than `unsynced_after_s`."""
        return self._newest_t4 is not None and now_ns - self._newest_t4 <= self._cfg.unsynced_after_s * NS_PER_S

    def _fit_line(self) -> tuple[int, float, float] | None:
        if not self._samples:
            return None
        if self._fit is None:
            by_rtt = sorted(self._samples, key=lambda s: s[2])
            n = max(MIN_FIT_SAMPLES, int(len(by_rtt) * self._cfg.best_fraction))
            if len(by_rtt) < MIN_FIT_SAMPLES:
                t4, theta, _ = by_rtt[0]
                self._fit = (t4, float(theta), 0.0)
            else:
                best = by_rtt[:n]
                t_ref = self._newest_t4
                x = np.array([(s[0] - t_ref) for s in best], dtype=np.float64)
                theta0 = best[0][1]
                y = np.array([(s[1] - theta0) for s in best], dtype=np.float64)
                xm, ym = x.mean(), y.mean()
                var = float(((x - xm) ** 2).sum())
                span_ns = float(x.max() - x.min())
                use_slope = span_ns >= SLOPE_MIN_WINDOW_FRACTION * self._cfg.window_s * NS_PER_S and var > 0
                slope = float(((x - xm) * (y - ym)).sum() / var) if use_slope else 0.0
                self._fit = (t_ref, theta0 + ym + slope * (0.0 - xm), slope)
        return self._fit

    def offset_ns(self, at_server_ns: int) -> int | None:
        """theta at a server time, or None before the first accepted sample."""
        fit = self._fit_line()
        if fit is None:
            return None
        t_ref, theta_ref, slope = fit
        return int(round(theta_ref + slope * (at_server_ns - t_ref)))

    def to_server_ns(self, phone_ns: int, now_ns: int) -> int | None:
        """PROTOCOL.md §3.3: server_time = phone_time - theta(now)."""
        theta = self.offset_ns(now_ns)
        return None if theta is None else phone_ns - theta
