package com.duoware.sensor

import com.duoware.sensor.bench.BenchMatrix
import com.duoware.sensor.clock.ClockSource
import com.duoware.sensor.clock.SyncClock
import com.duoware.sensor.net.DiscoveryRules
import com.duoware.sensor.proto.Beacon
import com.duoware.sensor.proto.YuvSize
import com.duoware.sensor.stats.Percentiles
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random

class ClockSourceTest {
    private val s = 1_000_000_000L

    @Test
    fun realtimeSourceIsBoottime() = assertEquals(SyncClock.BOOTTIME, ClockSource.decide(true, 0, 99 * s, 5 * s))

    @Test
    fun unknownSourceIsDecidedFromTheFirstFrame() {
        assertEquals(SyncClock.BOOTTIME, ClockSource.decide(false, 100 * s, 100 * s + 30_000_000, 7 * s))
        assertEquals(SyncClock.MONOTONIC, ClockSource.decide(false, 7 * s, 100 * s, 7 * s + 40_000_000))
        assertEquals(SyncClock.UNKNOWN, ClockSource.decide(false, 50 * s, 100 * s, 7 * s))
        assertEquals(SyncClock.UNKNOWN, ClockSource.decide(false, 101 * s, 100 * s, 7 * s))     // in the future
    }

    @Test
    fun nowFollowsTheDecision() {
        val c = ClockSource({ 1000L }, { 2000L })
        c.decide(false, 1500)
        assertEquals(SyncClock.MONOTONIC, c.clock)
        assertEquals(2000L, c.now())
        assertEquals("monotonic", c.clock.wire)
    }
}

class PercentilesTest {
    @Test
    fun nearestRankOnKnownData() {
        val p = Percentiles(100, 10_000)
        for (v in 1..20) p.add(0, v.toFloat())
        val out = FloatArray(2)
        assertTrue(p.p50p95(0, out))
        assertEquals(10f, out[0])
        assertEquals(19f, out[1])
    }

    @Test
    fun oldValuesLeaveTheWindowAndTheRingWraps() {
        val p = Percentiles(5, 1000)
        val out = FloatArray(2)
        assertFalse(p.p50p95(0, out))
        for (t in 0L until 10L) p.add(t * 100, t.toFloat())         // only the last 5 fit in the ring
        assertEquals(5, p.count(900))
        assertEquals(2, p.count(1800))                              // t = 800 and 900 are within 1000 ms of 1800
        assertTrue(p.p50p95(1800, out))
        assertEquals(8f, out[0])
    }

    @Test
    fun selectAgreesWithSorting() {
        val r = Random(3)
        repeat(200) {
            val n = 1 + r.nextInt(300)
            val a = FloatArray(n) { (r.nextInt(50) - 25).toFloat() }
            val sorted = a.sortedArray()
            val k = r.nextInt(n)
            assertEquals(sorted[k], Percentiles.select(a.copyOf(), n, k))
        }
    }
}

class BenchMatrixTest {
    private val sizes = listOf(
        YuvSize(4080, 3072, 10.0), YuvSize(1920, 1080, 30.0), YuvSize(1600, 1200, 30.0), YuvSize(1280, 720, 60.0),
        YuvSize(1280, 960, 60.0), YuvSize(1024, 768, 120.0), YuvSize(640, 480, 120.0), YuvSize(1280, 720, 60.0),
    )

    @Test
    fun phaseAComparesThePipelinesAndAruco3() {
        val a = BenchMatrix.phaseA(1280 to 720, 0)
        assertEquals(listOf("java_full", "native_full", "native_full", "native_roi", "native_roi"), a.map { it.pipeline })
        assertEquals(listOf(false, false, true, false, true), a.map { it.aruco3 })
        assertTrue(a.all { it.fpsTarget == 0.0 && it.resolution == 1280 to 720 })
    }

    @Test
    fun phaseBKeepsEligibleSizesLargestFirstAndAtMostNineRuns() {
        val b = BenchMatrix.phaseB(sizes, aruco3 = true, threads = 0)
        assertTrue(b.size <= 9)
        assertTrue(b.none { it.resolution == 4080 to 3072 || it.resolution == 640 to 480 })   // outside 0.5-2.2 MP
        assertEquals(1920 to 1080, b.first().resolution)
        assertTrue(b.all { it.pipeline == "native_roi" && it.aruco3 })
        // 1920x1080 max 30: only fps 0 (60 and 30 are not below the maximum)
        assertEquals(listOf(0.0), b.filter { it.resolution == 1920 to 1080 }.map { it.fpsTarget })
        // 1280x960 max 60: fps 0 and 30
        assertEquals(listOf(0.0, 30.0), b.filter { it.resolution == 1280 to 960 }.map { it.fpsTarget })
        assertEquals(b.size, b.distinct().size)
    }

    @Test
    fun phaseCVariesScanIntervalAndThreads() {
        val c = BenchMatrix.phaseC(1280 to 720, false, fastCluster = 2, allCores = 8)
        assertEquals(listOf(5, 20, 10, 10, 10), c.map { it.fullScanEvery })
        assertEquals(listOf(0, 0, 1, 2, 8), c.map { it.threads })
        assertEquals(4, BenchMatrix.phaseC(1280 to 720, false, fastCluster = 8, allCores = 8).size)
    }
}

class DiscoveryRulesTest {
    private fun b(id: String, host: String) = Beacon(id, "DUO-WARE", host, 8000, 47801, 47802)

    @Test
    fun interfaceClasses() {
        assertTrue(DiscoveryRules.isTether("rndis0") && DiscoveryRules.isTether("ncm0") && DiscoveryRules.isTether("usb0"))
        assertTrue(DiscoveryRules.isPhoneHotspot("swlan0") && DiscoveryRules.isPhoneHotspot("ap0"))
        assertTrue(DiscoveryRules.isWifiClient("wlan0") && !DiscoveryRules.isWifiClient("rmnet0"))
    }

    @Test
    fun subnetMatch() {
        assertTrue(DiscoveryRules.inSubnet("192.168.42.129", "192.168.42.17", 24))
        assertFalse(DiscoveryRules.inSubnet("192.168.43.1", "192.168.42.17", 24))
        assertTrue(DiscoveryRules.inSubnet("10.1.200.9", "10.1.0.5", 16))
        assertFalse(DiscoveryRules.inSubnet("10.2.0.1", "10.1.0.5", 16))
        assertFalse(DiscoveryRules.inSubnet("not-an-ip", "10.1.0.5", 16))
        assertNull(DiscoveryRules.ipv4("1.2.3.256"))
        assertNull(DiscoveryRules.ipv4("01.2.3.4"))
        assertTrue(DiscoveryRules.acceptable(b("a", "192.168.1.2"), "192.168.1.23", 24))
    }

    @Test
    fun preferTheStoredServerAndDeduplicate() {
        val heard = listOf(b("x", "192.168.1.2"), b("y", "192.168.1.3"), b("x", "192.168.1.2"))
        assertEquals("y", DiscoveryRules.choose(heard, "y")!!.serverId)
        assertEquals("x", DiscoveryRules.choose(heard, "zzz")!!.serverId)
        assertNull(DiscoveryRules.choose(emptyList(), "y"))
    }
}
