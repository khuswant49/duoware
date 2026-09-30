import type { CameraState } from "../api/types";
import { NOT_MEASURED, int, num, pair, text } from "./fmt";

function Row({ k, v, bad }: { k: string; v: string; bad?: boolean }) {
  return (
    <tr>
      <th>{k}</th>
      <td className={bad ? "bad-text" : undefined}>{v}</td>
    </tr>
  );
}

export function CameraPanel({ cam }: { cam: CameraState }) {
  const l = cam.link;
  const st = cam.stages_ms;
  const th = cam.thermal;
  const mode = cam.app_mode;
  return (
    <section className="panel camera-panel">
      <h3>
        Camera {cam.cam} <span className={`pill ${cam.online ? "ok" : "bad"}`}>{cam.online ? "online" : "offline"}</span>
      </h3>
      {cam.online && mode !== null && mode !== "tracking" && (
        <p className="warning">Phone is in {mode} mode: it gives no poses for control.</p>
      )}
      <table>
        <tbody>
          <Row k="Phone" v={text(cam.model)} />
          <Row k="App mode" v={text(mode)} bad={mode !== null && mode !== "tracking"} />
          <Row k="Connection" v={l ? `${text(l.mode)} over ${text(l.transport)} (${text(l.interface)})` : "-"} />
          <Row k="Wi-Fi" v={l?.wifi ? `${num(l.wifi.band_ghz, "GHz", 0)}, ${int(l.wifi.rssi_dbm, "dBm")}, ${int(l.wifi.link_mbps, "Mbit/s")}` : l ? "not applicable" : "-"} />
          <Row k="Latency p50 / p95" v={pair(l?.latency_ms)} />
          <Row k="Jitter" v={num(l?.jitter_ms, "ms", 2)} />
          <Row k="Loss" v={num(l?.loss_pct, "%", 2)} />
          <Row k="Sync" v={cam.sync.ok ? `ok, rtt ${num(cam.sync.rtt_ms, "ms", 2)}, ${cam.sync.samples} samples` : "UNSYNCED"} bad={!cam.sync.ok} />
          <Row k="Calibration" v={`${cam.calib.status}${cam.calib.model ? ` (${cam.calib.model})` : ""}`} bad={cam.calib.status === "MISALIGNED"} />
          <Row k="Fit rms / residual" v={`${num(cam.calib.rms_mm, "mm")} / ${num(cam.calib.residual_mm, "mm")}`} />
          <Row k="Floor tags used" v={cam.calib.floor_tags_used.join(", ") || "none"} />
          <Row k="Floor tags seen" v={cam.calib.floor_tags_seen.join(", ") || "none"} />
          <Row k="Frame rate" v={`${num(cam.fps, "", 1)} of ${num(cam.fps_target, "fps", 0)} target`} />
          <Row k="Resolution" v={cam.resolution ? cam.resolution.join(" × ") : NOT_MEASURED} />
          <Row k="Exposure / ISO" v={`${num(cam.exposure_ms, "ms", 1)} / ${int(cam.iso)}`} />
          <Row k="Pipeline p50 / p95" v={pair(st?.pipeline)} />
          <Row k="Detect full" v={pair(st?.detect_full)} />
          <Row k="Detect ROI" v={pair(st?.detect_roi)} />
          <Row k="Send" v={pair(st?.send)} />
          <Row k="Capture to sent" v={pair(st?.cap_to_sent)} />
          <Row k="Pose age p50 / p95 / max" v={`${num(cam.pose_age_ms.p50, "", 1)} / ${num(cam.pose_age_ms.p95, "", 1)} / ${num(cam.pose_age_ms.max, "ms", 1)} (n=${cam.pose_age_ms.n})`} />
          <Row k="Dropped / late / bad" v={`${cam.dropped} / ${cam.rx_late} / ${cam.rx_bad}`} />
          <Row k="Unauthorised / version / bad marker" v={`${cam.rx_unauth} / ${cam.rx_version ?? 0} / ${cam.rx_bad_marker ?? 0}`} bad={(cam.rx_version ?? 0) > 0} />
          <Row k="Phone send dropped" v={int(l?.send_dropped)} />
          <Row k="CPU (app)" v={num(cam.cpu_app_pct, "%", 1)} />
          <Row k="Thermal" v={th ? `status ${int(th.status)}, headroom ${num(th.headroom, "", 2)}, level ${int(th.level)}${th.reason ? ` (${th.reason})` : ""}` : "not measured"} />
          <Row k="Status age" v={num(cam.status_age_ms, "ms", 0)} bad={cam.status_age_ms !== null && cam.status_age_ms > 5000} />
        </tbody>
      </table>
    </section>
  );
}
