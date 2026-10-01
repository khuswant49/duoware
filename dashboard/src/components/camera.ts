// Pure camera-panel logic (M2 step 5): warnings and when to reload the preview image.
import type { CameraState } from "../api/types";

/** PROTOCOL.md §6.2 frame_age_ms: a live session with no frames for this long gets the firewall warning. */
export const NO_FRAMES_WARN_MS = 2000;
/** The preview is reloaded at most this often, whatever the phone sends (brief §4.1: 2-5 fps preview). */
export const PREVIEW_MAX_HZ = 5;

export function cameraWarnings(cam: CameraState): string[] {
  const w: string[] = [];
  if (cam.online && cam.frame_age_ms !== undefined && cam.frame_age_ms !== null && cam.frame_age_ms > NO_FRAMES_WARN_MS) {
    w.push(
      `No frames from the phone for ${(cam.frame_age_ms / 1000).toFixed(0)} s: check the Windows firewall for UDP ` +
        "47801 (and TCP 47802), see README.",
    );
  } else if (cam.online && (cam.frame_age_ms === undefined || cam.frame_age_ms === null) && cam.status_age_ms !== null) {
    w.push("The phone is paired but no frame has arrived yet: check the Windows firewall for UDP 47801, see README.");
  }
  if (cam.online && cam.app_mode !== null && cam.app_mode !== "tracking") {
    w.push(`Phone is in ${cam.app_mode} mode: it gives no poses for control.`);
  }
  if (cam.processing_on && cam.processing_on.length > 0) {
    w.push(`The phone keeps image processing on that it was asked to turn off: ${cam.processing_on.join(", ")}.`);
  }
  if (cam.online && cam.clock === "unknown") {
    w.push("The phone's camera clock is unknown: the server cannot sync it, so it gives no poses.");
  }
  return w;
}

export function previewUrl(cam: number, seq: number): string {
  return `/api/cameras/${cam}/preview.jpg?seq=${seq}`;
}

/** Reload only when the server has a newer preview, and not more often than PREVIEW_MAX_HZ. */
export function shouldReloadPreview(
  shownSeq: number | null,
  latestSeq: number | null,
  lastLoadMs: number | null,
  nowMs: number,
): boolean {
  if (latestSeq === null || latestSeq === shownSeq) return false;
  return lastLoadMs === null || nowMs - lastLoadMs >= 1000 / PREVIEW_MAX_HZ;
}
