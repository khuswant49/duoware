import { describe, expect, it } from "vitest";
import type { CameraState } from "../api/types";
import { NO_FRAMES_WARN_MS, PREVIEW_MAX_HZ, cameraWarnings, previewUrl, shouldReloadPreview } from "./camera";

function cam(over: Partial<CameraState> = {}): CameraState {
  return {
    cam: 1, online: true, model: "phone", app_mode: "tracking", link: null,
    sync: { ok: true, rtt_ms: 1, samples: 10, rejected: 0 },
    calib: { status: "OK", model: "homography", rms_mm: 1, residual_mm: 1, floor_tags_used: [], floor_tags_seen: [] },
    fps: 30, fps_target: 30, resolution: [1280, 720], stages_ms: null,
    pose_age_ms: { p50: 40, p95: 50, max: 60, n: 100 },
    dropped: 0, rx_bad: 0, rx_unauth: 0, rx_late: 0, cpu_app_pct: null, thermal: null,
    exposure_ms: 3, iso: 800, preview_seq: null, status_age_ms: 300, frame_age_ms: 20, processing_on: [],
    ...over,
  };
}

describe("cameraWarnings", () => {
  it("is empty for a healthy tracking camera", () => {
    expect(cameraWarnings(cam())).toEqual([]);
  });
  it("names the firewall when a live session has no frames for over 2 s", () => {
    expect(cameraWarnings(cam({ frame_age_ms: NO_FRAMES_WARN_MS }))).toEqual([]);
    const w = cameraWarnings(cam({ frame_age_ms: NO_FRAMES_WARN_MS + 1 }));
    expect(w).toHaveLength(1);
    expect(w[0]).toMatch(/firewall/);
    expect(w[0]).toMatch(/47801/);
  });
  it("warns when a paired phone never sent a frame", () => {
    expect(cameraWarnings(cam({ frame_age_ms: null }))[0]).toMatch(/no frame has arrived/);
  });
  it("does not warn about frames for an offline camera", () => {
    expect(cameraWarnings(cam({ online: false, frame_age_ms: 60000 }))).toEqual([]);
  });
  it("warns about non-tracking modes, processing left on and an unknown clock", () => {
    const w = cameraWarnings(cam({ app_mode: "benchmark", processing_on: ["ois", "af"], clock: "unknown" }));
    expect(w.join(" ")).toMatch(/benchmark mode/);
    expect(w.join(" ")).toMatch(/ois, af/);
    expect(w.join(" ")).toMatch(/cannot sync/);
  });
});

describe("preview reload", () => {
  it("builds a cache-busting URL from the sequence number", () => {
    expect(previewUrl(2, 17)).toBe("/api/cameras/2/preview.jpg?seq=17");
  });
  it("reloads only when the sequence changes", () => {
    expect(shouldReloadPreview(null, null, null, 0)).toBe(false);
    expect(shouldReloadPreview(null, 1, null, 0)).toBe(true);
    expect(shouldReloadPreview(1, 1, 0, 10_000)).toBe(false);
  });
  it("reloads at most PREVIEW_MAX_HZ", () => {
    const gap = 1000 / PREVIEW_MAX_HZ;
    expect(shouldReloadPreview(1, 2, 1000, 1000 + gap - 1)).toBe(false);
    expect(shouldReloadPreview(1, 2, 1000, 1000 + gap)).toBe(true);
  });
});
