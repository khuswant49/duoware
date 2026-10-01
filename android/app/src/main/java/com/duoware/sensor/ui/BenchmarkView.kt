package com.duoware.sensor.ui

import android.content.Context
import android.graphics.Typeface
import android.widget.TextView
import com.duoware.sensor.proto.BenchResult
import com.duoware.sensor.service.SensorCore
import java.util.Locale

/** The benchmark results table (M2 plan step 11): one row per finished run, updated as runs finish. */
class BenchmarkView(ctx: Context) : TextView(ctx) {
    init {
        typeface = Typeface.MONOSPACE
        textSize = 11f
    }

    fun update(core: SensorCore) {
        text = format(core.benchStatus, core.benchResults)
    }

    companion object {
        fun format(status: String?, results: List<BenchResult>): String {
            if (status == null && results.isEmpty()) return ""
            val sb = StringBuilder("benchmark: ${status ?: "-"}\n")
            if (results.isNotEmpty()) {
                sb.append("pipeline     res        fps  scan a3 fps    det_full  det_roi   cap_sent  cpu%\n")
            }
            for (r in results) {
                sb.append(String.format(Locale.ROOT, "%-12s %4dx%-4d %4s %3d %-3s %5s  %9s %9s %9s %5s\n",
                    r.pipeline, r.resolution.first, r.resolution.second, if (r.fpsTarget == 0.0) "max" else r.fpsTarget.toInt().toString(),
                    r.fullScanEvery, if (r.aruco3) "on" else "off", StatsView.num(r.fps),
                    StatsView.ms(r.detectFullMs), StatsView.ms(r.detectRoiMs), StatsView.ms(r.capToSentMs), StatsView.num(r.cpuAppPct, "%.0f")))
            }
            return sb.toString()
        }
    }
}
