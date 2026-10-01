package com.duoware.sensor.pipeline

import java.nio.ByteBuffer

/**
 * JNI entry points of `libduosensor.so` (C++, OpenCV; DECISIONS.md D31, D32). Per frame there is exactly one call,
 * [nativeProcess], with **direct** buffers only: the Y plane is wrapped without copying and results are written into a
 * preallocated [ResultBuffer].
 */
object NativeBridge {
    init {
        System.loadLibrary("duosensor")
    }

    /** OpenCV's version, `cv::getNumThreads()` and the "Parallel framework" line of `cv::getBuildInformation()`. */
    external fun nativeVersion(): String

    /** A pipeline for frames up to [maxW] x [maxH]; free it with [nativeDestroy]. */
    external fun nativeCreate(maxW: Int, maxH: Int): Long

    external fun nativeDestroy(handle: Long)

    /** PROTOCOL.md §4.4 `tracking`. [cornerRefine]: 0 none, 1 subpix. [threads] 0 = the fastest CPU cluster. */
    external fun nativeConfigure(
        handle: Long, trackIds: IntArray, fullScanEvery: Int, roiMargin: Float, roiMinPx: Int, threads: Int,
        demoteAfterScans: Int, cornerRefine: Int, aruco3: Boolean,
    )

    /**
     * Detects in one frame. [y] is the Y plane (direct), [rowStride] its row stride. Fills [result] (layout of
     * `results.hpp`, at least [ResultBuffer.BYTES]); with [previewW] > 0 also writes a greyscale downscale into
     * [preview]. Returns the number of markers, or -1 on bad arguments.
     */
    external fun nativeProcess(
        handle: Long, y: ByteBuffer, rowStride: Int, w: Int, h: Int, capNs: Long, forceFull: Boolean, previewW: Int,
        result: ByteBuffer, preview: ByteBuffer?,
    ): Int

    /** OpenCV worker thread IDs, for the ADPF hint session. Call once, not per frame. */
    external fun nativeWorkerTids(handle: Long): IntArray

    /** Worker threads in use after [nativeConfigure]. */
    external fun nativeThreads(handle: Long): Int

    /** Cores in the CPU cluster with the highest maximum frequency. */
    external fun nativeFastestClusterCores(): Int

    /** Greyscale JPEG of a [w] x [h] buffer into [out]; returns the length or -1. Preview thread only. */
    external fun nativeEncodeJpeg(src: ByteBuffer, w: Int, h: Int, quality: Int, out: ByteBuffer): Int
}
