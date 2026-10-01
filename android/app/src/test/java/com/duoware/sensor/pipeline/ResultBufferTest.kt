package com.duoware.sensor.pipeline

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class ResultBufferTest {
    @Test
    fun offsetsMatchTheNativeLayout() {
        // the values results.hpp computes (comments there): 272, 276, 280, 284, 288, 296, 36, 2600
        assertEquals(272, ResultBuffer.OFF_N_WINDOWS)
        assertEquals(288, ResultBuffer.OFF_DETECT_NS)
        assertEquals(0, ResultBuffer.OFF_DETECT_NS % 8)
        assertEquals(296, ResultBuffer.OFF_MARKERS)
        assertEquals(2600, ResultBuffer.BYTES)
    }

    @Test
    fun readsAHandWrittenBuffer() {
        val b = ByteBuffer.allocateDirect(ResultBuffer.BYTES).order(ByteOrder.LITTLE_ENDIAN)
        b.putInt(ResultBuffer.OFF_LAYOUT, 1).putInt(ResultBuffer.OFF_N_MARKERS, 2).putInt(ResultBuffer.OFF_SCAN, 1)
            .putInt(ResultBuffer.OFF_N_SEARCHED, 2).putInt(ResultBuffer.OFF_SEARCHED, 1)
            .putInt(ResultBuffer.OFF_SEARCHED + 4, 5).putInt(ResultBuffer.OFF_N_WINDOWS, 2)
            .putInt(ResultBuffer.OFF_LOST, 1).putInt(ResultBuffer.OFF_PREVIEW_W, 480)
            .putInt(ResultBuffer.OFF_PREVIEW_H, 270).putLong(ResultBuffer.OFF_DETECT_NS, 1_234_567L)
        for (m in 0 until 2) {
            val base = ResultBuffer.OFF_MARKERS + m * ResultBuffer.MARKER_BYTES
            b.putInt(base, if (m == 0) 1 else 5)
            for (j in 0 until 8) b.putFloat(base + 4 + 4 * j, m * 100f + j + 0.5f)
        }
        val r = ResultBuffer(b)
        assertEquals(ResultBuffer.LAYOUT_VERSION, r.layout)
        assertEquals(2, r.markerCount)
        assertTrue(!r.isFull && r.lost)
        val ids = IntArray(ResultBuffer.MAX_SEARCHED)
        assertEquals(2, r.searchedInto(ids))
        assertEquals(listOf(1, 5), ids.take(2))
        assertEquals(480 to 270, r.previewW to r.previewH)
        assertEquals(1_234_567L, r.detectNs)
        assertEquals(5, r.markerId(1))
        val c = FloatArray(10)
        r.corners(1, c, 2)
        assertArrayEquals(FloatArray(8) { 100f + it + 0.5f }, c.copyOfRange(2, 10), 0f)
    }
}
