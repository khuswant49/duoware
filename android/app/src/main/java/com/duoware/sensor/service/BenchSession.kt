package com.duoware.sensor.service

import com.duoware.sensor.bench.BenchConfig
import com.duoware.sensor.bench.BenchHost
import com.duoware.sensor.bench.BenchMatrix
import com.duoware.sensor.bench.BenchSample
import com.duoware.sensor.camera.Sizes
import com.duoware.sensor.pipeline.JavaBaseline
import com.duoware.sensor.proto.BenchResult
import com.duoware.sensor.proto.Pct
import com.duoware.sensor.stats.StageStats
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** The [SensorCore] operations behind a benchmark run (step 11): reconfigure pipeline and camera, read the counters. */
class BenchSession(
    private val core: SensorCore,
    private val onProgress: (List<BenchResult>, String?) -> Unit,
) : BenchHost {
    private val java by lazy { JavaBaseline() }

    override fun applyConfig(c: BenchConfig): Boolean {
        val s = core.settings ?: return false
        val caps = core.caps ?: return false
        core.pipeline.javaBaseline = if (c.pipeline == BenchMatrix.JAVA) java else null
        core.pipeline.configure(s.tracking.copy(fullScanEvery = c.fullScanEvery, aruco3 = c.aruco3, threads = c.threads))
        val size = Sizes.pick(caps.yuvSizes, c.resolution) ?: return false
        val fps = Sizes.effectiveFps(caps.yuvSizes, size, c.fpsTarget) ?: return false
        val a = core.controller.active
        if (a == null || a.size != size || Math.abs(a.fps - fps) > FPS_TOLERANCE) {
            val latch = CountDownLatch(1)
            core.readyLatch = latch
            core.controller.start(caps.id, size, c.fpsTarget, s.exposureNs, s.iso, null)
            if (!latch.await(READY_TIMEOUT_S, TimeUnit.SECONDS)) return false
        }
        return true
    }

    override fun frameCount() = core.pipeline.frames
    override fun cpuAppPct() = core.cpu.appPct()
    override fun headroom(): Double? =
        core.thermal.headroom(core.settings?.thermal?.forecastS ?: DEFAULT_FORECAST_S)?.takeIf { !it.isNaN() }
    override fun markersSeen() = core.pipeline.markersSeen

    override fun sample(): BenchSample {
        val out = FloatArray(2)
        val now = core.nowSyncMs()
        fun get(st: StageStats.Stage): Pct? =
            if (core.pipeline.stats.get(st, now, out)) Pct(out[0].toDouble(), out[1].toDouble()) else null
        return BenchSample(get(StageStats.Stage.DETECT_FULL), get(StageStats.Stage.DETECT_ROI), get(StageStats.Stage.CAP_TO_SENT))
    }

    override fun progress(done: List<BenchResult>, running: String?) = onProgress(done, running)
    override fun sleepMs(ms: Long) = Thread.sleep(ms)

    companion object {
        private const val FPS_TOLERANCE = 0.5
        private const val READY_TIMEOUT_S = 15L
        private const val DEFAULT_FORECAST_S = 10.0
    }
}
