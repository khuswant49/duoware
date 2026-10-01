package com.duoware.sensor.ui

import android.content.Context
import android.graphics.Typeface
import android.widget.TextView
import com.duoware.sensor.proto.Pct
import com.duoware.sensor.proto.Status
import com.duoware.sensor.service.SensorCore
import java.util.Locale

/** The numbers screen (M2 plan step 10), refreshed at 2 Hz from the core's last 1 Hz status. `-` = not measured. */
class StatsView(ctx: Context) : TextView(ctx) {
    init {
        typeface = Typeface.MONOSPACE
        textSize = 12f
    }

    fun update(core: SensorCore) {
        text = format(core)
    }

    companion object {
        fun ms(p: Pct?): String = if (p == null) "-" else String.format(Locale.ROOT, "%.1f/%.1f", p.p50, p.p95)
        fun num(v: Double?, fmt: String = "%.1f"): String = if (v == null) "-" else String.format(Locale.ROOT, fmt, v)

        fun format(core: SensorCore): String {
            val s: Status? = core.snapshot
            val sb = StringBuilder()
            val f = core.found
            sb.append("link   ${core.linkState} | ${core.step}\n")
            sb.append("server ${f?.beacon?.name ?: "-"} ${f?.host ?: ""} mode ${f?.linkMode ?: "-"} cam ${core.welcome?.cam ?: "-"}\n")
            core.lastError?.let { sb.append("error  $it\n") }
            if (s == null) return sb.append("(no status yet)").toString()
            sb.append("app    ${s.appMode} clock ${s.clock}\n")
            sb.append("fps    ${num(s.fps)} / target ${num(s.fpsTarget)}  res ${s.resolution?.let { "${it.first}x${it.second}" } ?: "-"}\n")
            sb.append("stages p50/p95 ms\n")
            for (name in listOf("pipeline", "detect_full", "detect_roi", "send", "cap_to_sent")) {
                sb.append(String.format(Locale.ROOT, "  %-12s %s\n", name, ms(s.stagesMs[name])))
            }
            sb.append("cpu    app ${num(s.cpuAppPct)}%  core ${s.cpuMaxCurKhz?.let { "${it / 1000} MHz" } ?: "-"}\n")
            val t = s.thermal
            sb.append("therm  status ${t?.status ?: "-"} headroom ${num(t?.headroom, "%.2f")} level ${t?.level ?: "-"} ${t?.reason ?: ""}\n")
            sb.append("cam    exp ${s.exposureNs?.let { num(it / 1e6, "%.2f") } ?: "-"} ms iso ${s.iso ?: "-"} focus ${s.focus ?: "-"}" +
                " processing_on ${s.processingOn ?: "-"}\n")
            sb.append("frames markers ${s.markersSeen ?: "-"} skipped ${s.framesSkipped ?: "-"} send_dropped ${s.sendDropped ?: "-"}" +
                " lost_rescans ${s.lostRescans ?: "-"}\n")
            val p = s.perf
            sb.append("perf   fg ${p?.foregroundService} wake ${p?.wakeLock} wifi_ll ${p?.wifiLowLatency} sustained ${p?.sustainedMode}" +
                " hint ${p?.hintSession} battopt_exempt ${p?.batteryOptExempt}\n")
            sb.append("batt   ${s.batteryPct ?: "-"}% charging ${s.charging ?: "-"}\n")
            return sb.toString()
        }
    }
}
