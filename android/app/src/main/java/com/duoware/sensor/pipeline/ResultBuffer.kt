package com.duoware.sensor.pipeline

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Reads the native result buffer (`cpp/results.hpp`, little-endian, fixed offsets) without allocation. The buffer is
 * direct, allocated once, and reused for every frame.
 */
class ResultBuffer(val buffer: ByteBuffer = ByteBuffer.allocateDirect(BYTES).order(ByteOrder.LITTLE_ENDIAN)) {
    init {
        require(buffer.isDirect && buffer.capacity() >= BYTES) { "the result buffer must be direct and >= $BYTES bytes" }
        buffer.order(ByteOrder.LITTLE_ENDIAN)
    }

    val layout: Int get() = buffer.getInt(OFF_LAYOUT)
    val markerCount: Int get() = buffer.getInt(OFF_N_MARKERS)
    val isFull: Boolean get() = buffer.getInt(OFF_SCAN) == 0
    val searchedCount: Int get() = buffer.getInt(OFF_N_SEARCHED)
    fun searched(i: Int): Int = buffer.getInt(OFF_SEARCHED + 4 * i)
    val windows: Int get() = buffer.getInt(OFF_N_WINDOWS)
    val lost: Boolean get() = buffer.getInt(OFF_LOST) != 0
    val previewW: Int get() = buffer.getInt(OFF_PREVIEW_W)
    val previewH: Int get() = buffer.getInt(OFF_PREVIEW_H)
    val detectNs: Long get() = buffer.getLong(OFF_DETECT_NS)

    fun markerId(i: Int): Int = buffer.getInt(OFF_MARKERS + i * MARKER_BYTES)

    /** Copies marker [i]'s 8 corner coordinates into [dst] at [offset]. */
    fun corners(i: Int, dst: FloatArray, offset: Int) {
        val base = OFF_MARKERS + i * MARKER_BYTES + 4
        for (j in 0 until 8) dst[offset + j] = buffer.getFloat(base + 4 * j)
    }

    /** Copies the searched IDs into [dst] (at least [MAX_SEARCHED] long); returns how many. */
    fun searchedInto(dst: IntArray): Int {
        val n = searchedCount
        for (i in 0 until n) dst[i] = searched(i)
        return n
    }

    companion object {
        // Mirror of cpp/results.hpp; ResultBufferTest checks the arithmetic, the layout field the version.
        const val LAYOUT_VERSION = 1
        const val MAX_MARKERS = 64
        const val MAX_SEARCHED = 64
        const val OFF_LAYOUT = 0
        const val OFF_N_MARKERS = 4
        const val OFF_SCAN = 8
        const val OFF_N_SEARCHED = 12
        const val OFF_SEARCHED = 16
        const val OFF_N_WINDOWS = OFF_SEARCHED + 4 * MAX_SEARCHED
        const val OFF_LOST = OFF_N_WINDOWS + 4
        const val OFF_PREVIEW_W = OFF_LOST + 4
        const val OFF_PREVIEW_H = OFF_PREVIEW_W + 4
        const val OFF_DETECT_NS = OFF_PREVIEW_H + 4
        const val OFF_MARKERS = OFF_DETECT_NS + 8
        const val MARKER_BYTES = 4 + 8 * 4
        const val BYTES = OFF_MARKERS + MAX_MARKERS * MARKER_BYTES
    }
}
