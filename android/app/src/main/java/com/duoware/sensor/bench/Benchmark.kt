package com.duoware.sensor.bench

import com.duoware.sensor.proto.BenchResult
import com.duoware.sensor.proto.Pct
import com.duoware.sensor.proto.YuvSize

/** What one benchmark run measures at its end (stage percentiles are over the last 10 s of the run). */
class BenchSample(val detectFull: Pct?, val detectRoi: Pct?, val capToSent: Pct?)

/** The phone-side operations a benchmark needs; implemented by the sensor core. */
interface BenchHost {
    /** Applies [c] (pipeline, tracking, resolution, fps) and returns when frames stream; false if the camera failed. */
    fun applyConfig(c: BenchConfig): Boolean
    fun frameCount(): Long
    fun cpuAppPct(): Double?
    fun headroom(): Double?
    fun markersSeen(): Int
    fun sample(): BenchSample
    /** Called after each finished run (for the on-screen table) and with the label of the run that starts. */
    fun progress(done: List<BenchResult>, running: String?)
    fun sleepMs(ms: Long)
}

/**
 * Benchmark mode (PROTOCOL.md §4.7, DECISIONS.md D36): runs the [BenchMatrix] phases A, B and C, each configuration
 * [warmS] s of warm-up (not measured) and [measureS] s measured. The defaults are the plan's 5 s and 30 s; shorter
 * values come only from the debug option of the activity.
 */
class Benchmark(private val host: BenchHost, private val warmS: Int = DEFAULT_WARM_S, private val measureS: Int = DEFAULT_MEASURE_S) {
    @Volatile var cancelled = false

    fun run(currentRes: Pair<Int, Int>, sizes: List<YuvSize>, fastCluster: Int, allCores: Int): List<BenchResult> {
        val results = ArrayList<BenchResult>()
        fun phase(configs: List<BenchConfig>) {
            for (c in configs) {
                if (cancelled) return
                host.progress(results, label(c))
                val r = runOne(c) ?: continue
                results += r
                host.progress(results, null)
            }
        }
        phase(BenchMatrix.phaseA(currentRes, 0))
        val aruco3 = BenchMatrix.betterAruco3(results)
        phase(BenchMatrix.phaseB(sizes, aruco3, 0))
        phase(BenchMatrix.phaseC(currentRes, aruco3, fastCluster, allCores))
        return results
    }

    private fun runOne(c: BenchConfig): BenchResult? {
        if (!host.applyConfig(c)) return null
        host.sleepMs(warmS * 1000L)
        if (cancelled) return null
        val headroomStart = host.headroom()
        val f0 = host.frameCount()
        var cpuSum = 0.0; var cpuN = 0; var markerSum = 0L; var markerN = 0
        val t0 = System.nanoTime()
        for (i in 0 until measureS) {
            host.sleepMs(1000)
            if (cancelled) return null
            host.cpuAppPct()?.let { cpuSum += it; cpuN++ }
            markerSum += host.markersSeen(); markerN++
        }
        val seconds = (System.nanoTime() - t0) / 1e9
        val s = host.sample()
        return BenchResult(
            c.resolution, c.fpsTarget, c.pipeline, c.fullScanEvery, c.aruco3, measureS.toDouble(),
            if (seconds > 0) (host.frameCount() - f0) / seconds else null, s.capToSent, s.detectFull, s.detectRoi,
            if (cpuN > 0) cpuSum / cpuN else null, headroomStart, host.headroom(),
            if (markerN > 0) markerSum.toDouble() / markerN else null,
        )
    }

    companion object {
        const val DEFAULT_WARM_S = 5          // M2 plan step 11
        const val DEFAULT_MEASURE_S = 30

        fun label(c: BenchConfig) = "${c.pipeline} ${c.resolution.first}x${c.resolution.second} fps " +
            "${if (c.fpsTarget == 0.0) "max" else c.fpsTarget.toInt()} scan/${c.fullScanEvery} aruco3 ${if (c.aruco3) "on" else "off"} " +
            "threads ${if (c.threads == 0) "auto" else c.threads}"
    }
}
