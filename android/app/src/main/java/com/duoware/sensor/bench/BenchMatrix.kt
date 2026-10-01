package com.duoware.sensor.bench

import com.duoware.sensor.proto.YuvSize

/** One benchmark configuration (PROTOCOL.md §4.7). fpsTarget 0 = the sensor's maximum at that resolution. */
data class BenchConfig(
    val resolution: Pair<Int, Int>, val fpsTarget: Double, val pipeline: String, val fullScanEvery: Int,
    val aruco3: Boolean, val threads: Int,
)

/**
 * The benchmark run list (M2 plan step 11), built in three phases because B and C depend on what A found.
 *  A: pipelines at the current resolution, fps 0: java_full; native_full and native_roi each with aruco3 off and on.
 *  B: native_roi with the better aruco3 from A, every YUV size of 0.5-2.2 MP with max_fps >= 30, each at fps 0, 60
 *     and 30 where they differ from the maximum; at most 9 runs, largest first.
 *  C: at the current resolution: full_scan_every 5 and 20; threads 1, the fastest cluster, and all cores.
 */
object BenchMatrix {
    const val ROI = "native_roi"
    const val FULL = "native_full"
    const val JAVA = "java_full"
    const val ROI_FULL_SCAN = 10                 // the full-scan interval used in phases A and B (tuning default)
    private const val MIN_MP = 0.5
    private const val MAX_MP = 2.2
    private const val MIN_FPS = 30.0
    private const val MAX_B_RUNS = 9
    private val B_FPS = listOf(0.0, 60.0, 30.0)
    private val C_FULL_SCANS = listOf(5, 20)

    fun phaseA(resolution: Pair<Int, Int>, threads: Int): List<BenchConfig> = listOf(
        BenchConfig(resolution, 0.0, JAVA, 1, false, threads),
        BenchConfig(resolution, 0.0, FULL, 1, false, threads),
        BenchConfig(resolution, 0.0, FULL, 1, true, threads),
        BenchConfig(resolution, 0.0, ROI, ROI_FULL_SCAN, false, threads),
        BenchConfig(resolution, 0.0, ROI, ROI_FULL_SCAN, true, threads),
    )

    fun phaseB(sizes: List<YuvSize>, aruco3: Boolean, threads: Int): List<BenchConfig> {
        val eligible = sizes.filter {
            val mp = it.w.toDouble() * it.h / 1e6
            mp in MIN_MP..MAX_MP && it.maxFps >= MIN_FPS
        }.distinctBy { it.w to it.h }.sortedByDescending { it.w.toLong() * it.h }
        val runs = mutableListOf<BenchConfig>()
        for (s in eligible) {
            for (fps in B_FPS) {
                if (fps != 0.0 && fps >= s.maxFps) continue      // 0 already means the maximum; no faster than it
                runs += BenchConfig(s.w to s.h, fps, ROI, ROI_FULL_SCAN, aruco3, threads)
            }
        }
        return runs.take(MAX_B_RUNS)
    }

    fun phaseC(resolution: Pair<Int, Int>, aruco3: Boolean, fastCluster: Int, allCores: Int): List<BenchConfig> {
        val runs = C_FULL_SCANS.map { BenchConfig(resolution, 0.0, ROI, it, aruco3, 0) }.toMutableList()
        for (t in listOf(1, fastCluster, allCores).distinct()) {
            runs += BenchConfig(resolution, 0.0, ROI, ROI_FULL_SCAN, aruco3, t)
        }
        return runs
    }
}
