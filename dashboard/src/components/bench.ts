// Benchmark table rows and the "Apply" patch (M2 step 5; PROTOCOL.md §4.7, §7.2).
import type { BenchResult, BenchRun } from "../api/types";
import { NOT_MEASURED, num } from "./fmt";

export interface BenchRow {
  key: string;
  runId: string;
  receivedMs: number;
  resolution: string;
  fps: string; // "achieved / target"
  pipeline: string;
  aruco3: string;
  fullScanEvery: string;
  detectFull: string;
  detectRoi: string;
  capToSent: string;
  cpu: string;
  headroom: string;
  markers: string;
  applicable: boolean; // only native_roi is a production pipeline
  result: BenchResult;
}

function pct(p: { p50: number | null; p95: number | null } | null | undefined): string {
  return p ? `${num(p.p50, "", 1)} / ${num(p.p95, "", 1)}` : NOT_MEASURED;
}

/** Newest run first (as the server returns them), results in the order the phone ran them. */
export function benchRows(runs: BenchRun[]): BenchRow[] {
  const rows: BenchRow[] = [];
  for (const run of runs) {
    run.results.forEach((r, i) => {
      rows.push({
        key: `${run.run_id}-${i}`,
        runId: run.run_id,
        receivedMs: run.received_wall_ms,
        resolution: r.resolution ? `${r.resolution[0]} × ${r.resolution[1]}` : NOT_MEASURED,
        fps: `${num(r.fps, "", 1)} / ${r.fps_target === 0 ? "max" : num(r.fps_target, "", 0)}`,
        pipeline: r.pipeline ?? NOT_MEASURED,
        aruco3: r.aruco3 === undefined || r.aruco3 === null ? NOT_MEASURED : r.aruco3 ? "on" : "off",
        fullScanEvery: r.full_scan_every === undefined || r.full_scan_every === null ? NOT_MEASURED : String(r.full_scan_every),
        detectFull: pct(r.detect_full_ms),
        detectRoi: pct(r.detect_roi_ms),
        capToSent: pct(r.cap_to_sent_ms),
        cpu: num(r.cpu_app_pct, "%", 0),
        headroom: `${num(r.headroom_start, "", 2)} → ${num(r.headroom_end, "", 2)}`,
        markers: num(r.markers_seen, "", 1),
        applicable: r.pipeline === "native_roi",
        result: r,
      });
    });
  }
  return rows;
}

/** The PUT /api/camera-settings body for "Apply": resolution, fps target, full-scan interval, aruco3. */
export function applyPatch(r: BenchResult): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  if (r.resolution) patch.resolution = r.resolution;
  if (r.fps_target !== undefined && r.fps_target !== null) patch.fps = r.fps_target;
  if (r.full_scan_every !== undefined && r.full_scan_every !== null) patch.full_scan_every = r.full_scan_every;
  if (r.aruco3 !== undefined && r.aruco3 !== null) patch.aruco3 = r.aruco3;
  return patch;
}
