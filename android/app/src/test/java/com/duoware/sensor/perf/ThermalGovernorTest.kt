package com.duoware.sensor.perf

import com.duoware.sensor.proto.ThermalPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ThermalGovernorTest {
    // the PROTOCOL.md §4.4 example policy
    private val policy = ThermalPolicy(10.0, 0.85, 0.65, 60.0, 2, listOf(1.0, 0.75, 0.5), listOf(960 to 540))
    private fun gov() = ThermalGovernor(policy, 60.0, 1280 to 720)

    @Test
    fun levelsRunThroughFpsStepsThenResolutions() {
        val g = gov()
        assertEquals(3, g.maxLevel)
        assertEquals(listOf(60.0, 45.0, 30.0, 30.0), (0..3).map { g.levelAt(it, null).fps })
        assertEquals(listOf(1280 to 720, 1280 to 720, 1280 to 720, 960 to 540), (0..3).map { g.levelAt(it, null).resolution })
    }

    @Test
    fun risingHeadroomStepsDownOneLevelPerForecastPeriod() {
        val g = gov()
        assertEquals(0, g.tick(0, 0.80, 0).index)
        assertEquals(1, g.tick(1000, 0.86, 0).index)
        assertTrue(g.level.reason!!.contains("0.86"))
        assertEquals(1, g.tick(5000, 0.90, 0).index)              // waits forecast_s (10 s) before the next step
        assertEquals(2, g.tick(11_000, 0.90, 0).index)
        assertEquals(3, g.tick(21_000, 0.95, 0).index)
        assertEquals(3, g.tick(40_000, 0.99, 0).index)             // never beyond the last level
    }

    @Test
    fun stepsUpOnlyAfterUpAfterSContinuouslyCool() {
        val g = gov()
        g.tick(0, 0.9, 0)
        g.tick(10_000, 0.9, 0)
        assertEquals(2, g.level.index)
        g.tick(11_000, 0.60, 0)                                    // cool from here
        assertEquals(2, g.tick(70_000, 0.60, 0).index)             // 59 s
        assertEquals(1, g.tick(71_000, 0.60, 0).index)             // 60 s
        g.tick(80_000, 0.70, 0)                                    // between up and down: the timer restarts
        assertEquals(1, g.tick(135_000, 0.60, 0).index)
        assertEquals(0, g.tick(195_000, 0.60, 0).index)
        assertNull(g.level.reason)
        assertEquals(0, g.tick(400_000, 0.10, 0).index)            // never below level 0
    }

    @Test
    fun withoutAHeadroomApiTheStatusDecides() {
        val g = gov()
        assertEquals(0, g.tick(0, null, 1).index)
        assertEquals(1, g.tick(1000, null, 2).index)
        assertTrue(g.level.reason!!.contains("status 2"))
        assertEquals(1, g.tick(30_000, null, 0).index)
        assertEquals(0, g.tick(91_000, null, 0).index)             // 60 s after cooling started at 31 s? first cool tick 30 s
    }

    @Test
    fun nanKeepsTheLastReadingForTenSeconds() {
        val g = gov()
        g.tick(0, 0.90, 0)
        assertEquals(1, g.level.index)
        assertEquals(2, g.tick(10_000, Double.NaN, 0).index)        // last valid 0.90 is 10 s old: still used
        val g2 = gov()
        g2.tick(0, 0.90, 0)
        assertEquals(1, g2.tick(10_001, Double.NaN, 0).index)       // too old: no reading, status 0 -> not hot
    }
}
