"""The `settings` message a phone gets (PROTOCOL.md §4.4): tuning.toml defaults, overridden by
`PUT /api/camera-settings` / venue presets, with the registry's car tags as the tracking hint list."""

from duoware.protocol.phone_session import CameraSettings, PreviewSettings, ThermalPolicy, Tracking
from duoware.registry.service import TagRegistry
from duoware.settings import Settings

NS_PER_MS = 1_000_000


def build_camera_settings(settings: Settings, overrides: dict, registry: TagRegistry) -> CameraSettings:
    cam, phone, th = settings.tuning.camera, settings.tuning.phone, settings.tuning.phone.thermal
    exposure_ms = overrides.get("exposure_ms", cam.exposure_ms)
    return CameraSettings(
        resolution=tuple(overrides.get("resolution", cam.resolution)),       # type: ignore[arg-type]
        fps=float(overrides.get("fps", cam.fps)),
        exposure_ns=round(exposure_ms * NS_PER_MS),
        iso=int(overrides.get("iso", cam.iso)),
        focus=cam.focus, awb=cam.awb,
        tracking=Tracking(tuple(t.id for t in registry.snapshot().car_tags()),
                          int(overrides.get("full_scan_every", phone.full_scan_every)), phone.roi_margin,
                          phone.roi_min_px, int(overrides.get("threads", phone.threads)), phone.demote_after_scans,
                          phone.corner_refine, bool(overrides.get("aruco3", phone.aruco3))),
        thermal=ThermalPolicy(th.forecast_s, th.headroom_down, th.headroom_up, th.up_after_s, th.status_down,
                              th.fps_steps, th.resolution_steps),
        preview=PreviewSettings(cam.preview_fps, cam.preview_width, cam.preview_jpeg_quality))
