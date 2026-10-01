package com.duoware.sensor

import com.duoware.sensor.bench.BenchMatrix
import com.duoware.sensor.bench.Benchmark
import com.duoware.sensor.bench.BenchConfig
import com.duoware.sensor.bench.BenchHost
import com.duoware.sensor.bench.BenchSample
import com.duoware.sensor.preview.PreviewEncoder
import com.duoware.sensor.proto.BenchResult
import com.duoware.sensor.proto.Pct
import com.duoware.sensor.proto.YuvSize
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** M2 step 11, pure parts (unit test only, nothing ran on the phone here): the DWP1 header, the benchmark run order. */
class Step11Test {
    @Test
    fun previewHeaderIsDwp1CapNsAndSizeBigEndian() {
        val b = ByteArray(16)
        PreviewEncoder.writeHeader(b, 0x0102030405060708L, 480, 270)
        assertArrayEquals(byteArrayOf(0x44, 0x57, 0x50, 0x31, 1, 2, 3, 4, 5, 6, 7, 8, 0x01, 0xE0.toByte(), 0x01, 0x0E), b)
    }

    private fun result(p: String, aruco3: Boolean, full: Double?) = BenchResult(
        1280 to 720, 0.0, p, 1, aruco3, 30.0, 30.0, null, full?.let { Pct(it, it + 1) }, null, null, null, null, null,
    )

    @Test
    fun aruco3IsChosenFromTheNativeFullRunsOnly() {
        assertTrue(BenchMatrix.betterAruco3(listOf(result("native_full", false, 12.0), result("native_full", true, 9.0))))
        assertFalse(BenchMatrix.betterAruco3(listOf(result("native_full", false, 9.0), result("native_full", true, 12.0))))
        assertFalse("a missing run: off", BenchMatrix.betterAruco3(listOf(result("native_full", false, 9.0))))
        assertFalse("roi runs do not count", BenchMatrix.betterAruco3(listOf(result("native_roi", false, 12.0), result("native_roi", true, 1.0))))
    }

    private class FakeHost(val failOn: Int = -1) : BenchHost {
        val applied = ArrayList<BenchConfig>()
        var frames = 0L
        var slept = 0L
        val labels = ArrayList<String?>()
        override fun applyConfig(c: BenchConfig): Boolean { applied += c; return applied.size - 1 != failOn }
        override fun frameCount(): Long { frames += 30; return frames }
        override fun cpuAppPct() = 10.0
        override fun headroom(): Double? = null
        override fun markersSeen() = 3
        override fun sample(): BenchSample {
            val faster = applied.lastOrNull()?.aruco3 == true
            return BenchSample(Pct(if (faster) 8.0 else 12.0, 14.0), null, Pct(50.0, 60.0))
        }
        override fun progress(done: List<BenchResult>, running: String?) { labels += running }
        override fun sleepMs(ms: Long) { slept += ms }
    }

    @Test
    fun runsPhaseAThenBThenCAndSkipsARunTheCameraRefused() {
        val sizes = listOf(YuvSize(1280, 720, 30.0), YuvSize(1920, 1080, 30.0))
        val host = FakeHost(failOn = 1)                                  // native_full aruco3=off fails to start
        val out = Benchmark(host, 5, 30).run(1280 to 720, sizes, 2, 8)
        val expected = BenchMatrix.phaseA(1280 to 720, 0).size + BenchMatrix.phaseB(sizes, false, 0).size +
            BenchMatrix.phaseC(1280 to 720, false, 2, 8).size
        assertEquals(expected, host.applied.size)
        assertEquals("one run was refused", expected - 1, out.size)
        assertEquals("java_full", out.first().pipeline)
        assertEquals("every run: 5 s warm-up and 30 one-second samples", (5 + 30) * 1000L * out.size, host.slept)
    }

    @Test
    fun cancelStopsBetweenRunsAndKeepsTheFinishedOnes() {
        val host = object : BenchHost by FakeHost() {}
        val b = Benchmark(host, 0, 1)
        b.cancelled = true
        assertTrue(b.run(1280 to 720, emptyList(), 2, 8).isEmpty())
    }
}
