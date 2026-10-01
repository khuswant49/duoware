import { describe, expect, it } from "vitest";
import type { BenchRun } from "../api/types";
import { applyPatch, benchRows } from "./bench";

const RUN: BenchRun = {
  v: 1, t: "bench", cam: 1, run_id: "20261001-140200", received_wall_ms: 1790000000000,
  results: [
    {
      resolution: [1280, 720], fps_target: 0, pipeline: "native_roi", full_scan_every: 10, aruco3: false,
      duration_s: 30, fps: 59.6, cap_to_sent_ms: { p50: 33.1, p95: 40.2 }, detect_full_ms: { p50: 14, p95: 17.5 },
      detect_roi_ms: { p50: 1.8, p95: 2.6 }, cpu_app_pct: 22, headroom_start: 0.4, headroom_end: 0.47, markers_seen: 11,
    },
    { resolution: [1280, 720], fps_target: 30, pipeline: "java_full", fps: 21.5, cpu_app_pct: null },
  ],
};

describe("benchRows", () => {
  it("formats one row per result, keeping the run order", () => {
    const rows = benchRows([RUN]);
    expect(rows.map((r) => r.pipeline)).toEqual(["native_roi", "java_full"]);
    const r = rows[0];
    expect(r.resolution).toBe("1280 × 720");
    expect(r.fps).toBe("59.6 / max");
    expect(r.aruco3).toBe("off");
    expect(r.detectRoi).toBe("1.8 / 2.6");
    expect(r.headroom).toBe("0.40 → 0.47");
    expect(r.applicable).toBe(true);
  });
  it("shows missing measurements as not measured, never as zero", () => {
    const r = benchRows([RUN])[1];
    expect(r.fps).toBe("21.5 / 30");
    expect(r.cpu).toBe("not measured");
    expect(r.detectFull).toBe("not measured");
    expect(r.aruco3).toBe("not measured");
    expect(r.applicable).toBe(false);
  });
});

describe("applyPatch", () => {
  it("sends resolution, fps target, full-scan interval and aruco3", () => {
    expect(applyPatch(RUN.results[0])).toEqual({ resolution: [1280, 720], fps: 0, full_scan_every: 10, aruco3: false });
  });
  it("leaves out what the run did not report", () => {
    expect(applyPatch({ resolution: [960, 540] })).toEqual({ resolution: [960, 540] });
  });
});
