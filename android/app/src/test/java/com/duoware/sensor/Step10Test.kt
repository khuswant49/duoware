package com.duoware.sensor

import com.duoware.sensor.perf.CpuStats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** M2 step 10, pure parts: `/proc/self/stat` parsing and the CPU percentage. Unit test only: nothing ran on the phone here. */
class Step10Test {
    @Test
    fun ticksAreUtimePlusStimeEvenWithSpacesAndParenthesesInTheCommandName() {
        val line = "1234 (com.duoware (x) y) S 1 1234 1234 0 -1 4194560 100 0 0 0 120 30 0 0 20 0 15 0 5000 1 2 3"
        assertEquals(150L, CpuStats.parseTicks(line))
        assertNull(CpuStats.parseTicks("garbage"))
        assertNull(CpuStats.parseTicks("1 (a) S 1 2"))
    }

    @Test
    fun percentIsCpuSecondsOverWallOverCores() {
        // 100 ticks/s, 8 cores: 200 ticks in 2 s of wall time = 1 core busy = 12.5 %
        assertEquals(12.5, CpuStats.percent(200, 2_000_000_000L, 8, 100)!!, 1e-9)
        assertNull(CpuStats.percent(-1, 1_000_000_000L, 8, 100))
        assertNull(CpuStats.percent(10, 0, 8, 100))
    }
}
