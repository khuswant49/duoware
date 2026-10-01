"""Clean-read ratio vs image speed for one tag, from a frame recording (DECISIONS.md D42, brief §4.1, M2 H9).

    python -m duoware.tools.blur_report data/recordings/<file>.jsonl --tag 1 [--cam 1] [--json]

For every frame in which the phone looked for the tag (a `full` frame, or a `roi` frame with the tag in `searched`),
it records whether the tag was detected and its image speed at that instant: the change of the marker centre between
the nearest detections before and after (by capture time `cap_ns`), each within `SPEED_WINDOW_MS`, in px/s. Frames
without both neighbours are excluded and counted. Speeds are binned like DUO-WARE 1's measurement (100% below
150 px/s, 70-76% at 150-300 px/s, 15-44% above 300 px/s at ~26 ms exposure).
"""

import argparse
import bisect
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from duoware.store.recorder import load

SPEED_WINDOW_MS = 200                         # neighbouring detections used for the speed must be this close
BIN_EDGES_PX_S = (150.0, 300.0)               # DUO-WARE 1's bins (brief §4.1)
BIN_NAMES = ("< 150", "150-300", "> 300")
NS_PER_S = 1_000_000_000
NS_PER_MS = 1_000_000


@dataclass
class Looked:
    cap_ns: int
    detected: bool
    exposure_ns: int
    speed_px_s: float | None = None


@dataclass
class BinRow:
    name: str
    looked: int
    detected: int

    @property
    def ratio(self) -> float | None:
        return None if self.looked == 0 else self.detected / self.looked


def bin_index(speed_px_s: float) -> int:
    """0: below 150 px/s, 1: 150 up to 300, 2: 300 and above."""
    return bisect.bisect_right(BIN_EDGES_PX_S, speed_px_s)


def collect(path: Path, tag: int, cam: int | None = None) -> tuple[list[Looked], int]:
    """(looked frames with their speed, frames excluded for lack of neighbours)."""
    _, lines = load(path)
    looked: list[Looked] = []
    det_t: list[int] = []
    det_c: list[tuple[float, float]] = []
    for line in lines:
        if cam is not None and line["cam"] != cam:
            continue
        f = line["frame"]
        searched = f["scan"] == "full" or tag in f.get("searched", [])
        hit = next((m for m in f["m"] if m[0] == tag), None)
        if hit is not None:
            xs, ys = hit[1::2], hit[2::2]
            det_t.append(f["cap_ns"])
            det_c.append((sum(xs) / 4.0, sum(ys) / 4.0))
        if searched:
            looked.append(Looked(f["cap_ns"], hit is not None, f["exp_ns"]))
    order = sorted(range(len(det_t)), key=det_t.__getitem__)
    det_t = [det_t[i] for i in order]
    det_c = [det_c[i] for i in order]
    window = SPEED_WINDOW_MS * NS_PER_MS
    excluded = 0
    for lk in looked:
        i = bisect.bisect_left(det_t, lk.cap_ns)                   # detections strictly before: indices < i
        j = bisect.bisect_right(det_t, lk.cap_ns)                  # strictly after: indices >= j
        if i == 0 or j >= len(det_t) or lk.cap_ns - det_t[i - 1] > window or det_t[j] - lk.cap_ns > window:
            excluded += 1
            continue
        (x0, y0), (x1, y1) = det_c[i - 1], det_c[j]
        dt = (det_t[j] - det_t[i - 1]) / NS_PER_S
        lk.speed_px_s = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / dt
    return [lk for lk in looked if lk.speed_px_s is not None], excluded


def report(looked: list[Looked]) -> list[BinRow]:
    rows = [BinRow(n, 0, 0) for n in BIN_NAMES]
    for lk in looked:
        r = rows[bin_index(lk.speed_px_s)]
        r.looked += 1
        r.detected += lk.detected
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="duoware.tools.blur_report", description=__doc__.split("\n\n")[0])
    p.add_argument("file", type=Path, help="a recording from `python -m duoware --record-frames`")
    p.add_argument("--tag", type=int, required=True, help="the tag ID to analyse (usually a car tag)")
    p.add_argument("--cam", type=int, default=None, help="only this camera (default: all)")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    a = p.parse_args(argv)
    looked, excluded = collect(a.file, a.tag, a.cam)
    rows = report(looked)
    exposures = sorted({lk.exposure_ns for lk in looked})
    if a.json:
        print(json.dumps({"tag": a.tag, "cam": a.cam, "excluded": excluded,
                          "exposure_ms": [e / NS_PER_MS for e in exposures],
                          "bins": [{"speed_px_s": r.name, "looked": r.looked, "detected": r.detected,
                                    "clean_read_ratio": r.ratio} for r in rows]}))
        return 0
    print(f"tag {a.tag}{'' if a.cam is None else f', camera {a.cam}'}: exposure "
          f"{', '.join(f'{e / NS_PER_MS:g} ms' for e in exposures) or 'n/a'}; "
          f"{excluded} looked-for frames excluded (no detection within {SPEED_WINDOW_MS} ms on both sides)")
    print(f"{'speed px/s':>12} {'looked':>8} {'detected':>9} {'clean reads':>12}")
    for r in rows:
        ratio = "-" if r.ratio is None else f"{100 * r.ratio:.0f} %"
        print(f"{r.name:>12} {r.looked:>8} {r.detected:>9} {ratio:>12}")
    print("DUO-WARE 1 at ~26 ms exposure: 100 % / 70-76 % / 15-44 %")
    return 0


if __name__ == "__main__":
    sys.exit(main())
