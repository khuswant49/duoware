package com.duoware.sensor.proto

import com.duoware.sensor.TestJson
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FrameWriterTest {
    private val sid = "9f2c4e1a7b3d5c60".toByteArray()

    private fun golden(w: FrameWriter, buf: ByteArray): Int {
        w.begin(buf, 1, sid, 48213, 81234567890123, 3000000, 18500000, 81234612890123, 1280, 720, false,
            intArrayOf(1, 5, 99), 2)
        assertTrue(w.marker(1, floatArrayOf(612.4f, 300.1f, 660.25f, 301.0f, 659.5f, 348.7f, 611.8f, 347.9f), 0))
        val c = floatArrayOf(-7f, -0.04f, -1.26f, 946.0f, 410.9f, 945.7f, 456.6f, 899.8f, 456.1f)
        assertTrue(w.marker(5, c, 1))
        return w.end()
    }

    @Test
    fun writesTheGoldenBytesExactly() {
        val w = FrameWriter()
        val buf = ByteArray(FrameWriter.MAX_BYTES)
        val n = golden(w, buf)
        FrameWriter.stampSent(buf, w.sentFieldOffset, 81234629890123)
        assertArrayEquals(TestJson.resourceBytes("frame_golden.json"), buf.copyOf(n))
    }

    @Test
    fun theSentFieldCanBeStampedAgain() {
        val w = FrameWriter()
        val buf = ByteArray(FrameWriter.MAX_BYTES)
        val n = golden(w, buf)
        FrameWriter.stampSent(buf, w.sentFieldOffset, Long.MAX_VALUE)
        FrameWriter.stampSent(buf, w.sentFieldOffset, 7)
        assertEquals(7L, JSONObject(String(buf, 0, n)).getLong("sent_ns"))
    }

    @Test
    fun fullFramesHaveAnEmptySearchedListAndEmptyFramesAreValid() {
        val w = FrameWriter()
        val buf = ByteArray(FrameWriter.MAX_BYTES)
        w.begin(buf, 2, sid, 0, 1, 2, 0, 3, 640, 480, true, intArrayOf(1, 5), 2)
        val n = w.end()
        FrameWriter.stampSent(buf, w.sentFieldOffset, 4)
        val o = JSONObject(String(buf, 0, n))
        assertEquals("full", o.getString("scan"))
        assertEquals(0, o.getJSONArray("searched").length())
        assertEquals(0, o.getJSONArray("m").length())
    }

    @Test
    fun stopsAddingMarkersAtTheCapacityAndStaysValidJson() {
        val w = FrameWriter(capacity = 600)
        val buf = ByteArray(600)
        w.begin(buf, 1, sid, 1, 1, 1, 1, 1, 1280, 720, true, IntArray(0), 0)
        val c = FloatArray(8) { -99999.9f }
        var added = 0
        while (w.marker(added, c, 0)) added++
        assertTrue(added in 1..9)
        val n = w.end()
        assertTrue(n <= 600)
        FrameWriter.stampSent(buf, w.sentFieldOffset, 1)
        assertEquals(added, JSONObject(String(buf, 0, n)).getJSONArray("m").length())
        assertFalse(w.marker(99, c, 0))
    }

    @Test
    fun writesLongsWithoutAllocation() {
        val buf = ByteArray(32)
        for (v in listOf(0L, 7L, -7L, 1234567890123L, Long.MAX_VALUE, Long.MIN_VALUE + 1)) {
            val n = FrameWriter.writeLong(buf, 0, v)
            assertEquals(v.toString(), String(buf, 0, n))
        }
    }
}
