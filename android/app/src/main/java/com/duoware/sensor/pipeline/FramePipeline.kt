package com.duoware.sensor.pipeline

import com.duoware.sensor.camera.CaptureMeta
import com.duoware.sensor.proto.FrameWriter
import com.duoware.sensor.proto.Tracking
import com.duoware.sensor.stats.StageStats
import java.nio.ByteBuffer

/**
 * Per frame, on the pipeline thread (M2 plan step 8 "FramePipeline"): one JNI call detects, the result is written as a
 * PROTOCOL.md §2 frame into a send buffer and handed to the sender. Everything after the camera image is in
 * [processFrame], so tests can drive it without a camera.
 *
 * Hot path (D31): no allocation here. Scratch arrays and buffers are created once; [FrameWriter] writes bytes directly.
 * Stage timings: `pipeline` = avail_ns - cap_ns, `detect_full`/`detect_roi` = the native detection time; `send` and
 * `cap_to_sent` are measured by the sender through [onSent] (it stamps `sent_ns`).
 */
class FramePipeline(
    private val clock: () -> Long,                   // the sync clock (PROTOCOL.md §3.1), ns
    private val slot: SendSlot,
    private val meta: CaptureMeta,
    val stats: StageStats = StageStats(),
    maxW: Int = MAX_W,
    maxH: Int = MAX_H,
) {
    private val handle = NativeBridge.nativeCreate(maxW, maxH)
    private val result = ResultBuffer()
    private val writer = FrameWriter()
    private val corners = FloatArray(8)
    private val searched = IntArray(ResultBuffer.MAX_SEARCHED)
    private val sid = ByteArray(SID_CHARS)
    private var cam = 0
    private var seq = 0L
    private var forceFull = false

    /** The dashboard preview (PROTOCOL.md �4.6): asked for a downscaled copy only when one is due; null = off. */
    @Volatile var previewSource: PreviewSource? = null

    /** Benchmark only (`java_full`, DECISIONS.md D31): detect with the OpenCV Java API instead of the native pipeline. */
    @Volatile var javaBaseline: JavaBaseline? = null

    /** Exposure used when no capture result matches an image (the configured one). */
    @Volatile var fallbackExposureNs = 0L

    @Volatile var markersSeen = 0
        private set
    @Volatile var lostRescans = 0L
        private set
    @Volatile var roiWindows = 0                      // windows of the last roi frame
        private set
    @Volatile var frames = 0L
        private set
    @Volatile var lastDetectNs = 0L
        private set
    @Volatile var lastWasFull = true
        private set

    /** A new session: its `cam` and `sid` (16 ASCII hex characters); `seq` starts at 0 again (PROTOCOL.md §2). */
    @Synchronized
    fun startSession(camId: Int, sidText: String) {
        require(sidText.length == SID_CHARS) { "sid must be $SID_CHARS characters" }
        cam = camId
        for (i in 0 until SID_CHARS) sid[i] = sidText[i].code.toByte()
        seq = 0
        forceFull = true
    }

    /** PROTOCOL.md §4.4 `tracking`; the next frame is a full scan. */
    @Synchronized
    fun configure(t: Tracking, trackIds: IntArray = IntArray(t.trackIds.size) { t.trackIds[it] }) {
        NativeBridge.nativeConfigure(handle, trackIds, t.fullScanEvery, t.roiMargin.toFloat(), t.roiMinPx, t.threads,
            t.demoteAfterScans, if (t.cornerRefine == "none") 0 else 1, t.aruco3)
    }

    fun workerTids(): IntArray = NativeBridge.nativeWorkerTids(handle)
    fun threads(): Int = NativeBridge.nativeThreads(handle)

    /**
     * Detects in one frame and hands the §2 message to the sender. [y] is the Y plane (direct), [availNs] the sync-clock
     * time the image reached the app. Returns the number of markers (-1 on bad arguments).
     */
    @Synchronized
    fun processFrame(y: ByteBuffer, rowStride: Int, w: Int, h: Int, capNs: Long, availNs: Long): Int {
        val java = javaBaseline
        val ps = if (java == null) previewSource else null
        val pbuf = ps?.acquire(availNs)
        val n: Int
        val full: Boolean
        val nSearched: Int
        val detectNs: Long
        if (java != null) {
            val t0 = System.nanoTime()
            n = java.detect(y, rowStride, w, h)
            detectNs = System.nanoTime() - t0
            full = true; nSearched = 0
        } else {
            n = NativeBridge.nativeProcess(handle, y, rowStride, w, h, capNs, forceFull, if (pbuf != null) ps!!.width else 0,
                result.buffer, pbuf)
            if (n < 0) { ps?.abort(); return n }
            forceFull = false
            full = result.isFull
            nSearched = if (full) 0 else result.searchedInto(searched)
            detectNs = result.detectNs
            if (result.lost) lostRescans++
            if (pbuf != null) {
                if (result.previewW > 0) ps!!.publish(result.previewW, result.previewH, capNs) else ps!!.abort()
            }
        }
        val detEndNs = clock()
        lastWasFull = full
        lastDetectNs = detectNs
        if (full) markersSeen = n else if (java == null) roiWindows = result.windows

        val i = slot.acquire()
        writer.begin(slot.buffer(i), cam, sid, seq++, capNs, meta.exposureNs(capNs, fallbackExposureNs),
            meta.skewNs(capNs), availNs, w, h, full, searched, nSearched)
        for (k in 0 until (if (java != null) 0 else minOf(n, ResultBuffer.MAX_MARKERS))) {      // java_full sends no corners
            result.corners(k, corners, 0)
            if (!writer.marker(result.markerId(k), corners, 0)) break
        }
        val len = writer.end()
        slot.publish(i, len, writer.sentFieldOffset, detEndNs, capNs)

        val nowMs = detEndNs / NS_PER_MS
        stats.add(StageStats.Stage.PIPELINE, nowMs, (availNs - capNs) / NS_PER_MS_F)
        stats.add(if (full) StageStats.Stage.DETECT_FULL else StageStats.Stage.DETECT_ROI, nowMs, detectNs / NS_PER_MS_F)
        frames++
        return n
    }

    /** Called by the sender thread after it wrote a frame: [sentNs] is the `sent_ns` it stamped. */
    fun onSent(sentNs: Long, readyNs: Long, capNs: Long) {
        val nowMs = sentNs / NS_PER_MS
        stats.add(StageStats.Stage.SEND, nowMs, (sentNs - readyNs) / NS_PER_MS_F)
        stats.add(StageStats.Stage.CAP_TO_SENT, nowMs, (sentNs - capNs) / NS_PER_MS_F)
    }

    fun close() = NativeBridge.nativeDestroy(handle)

    companion object {
        const val SID_CHARS = 16                    // PROTOCOL.md §1: sid is 16 lowercase hex characters
        const val MAX_W = 4096                      // largest frame the native side accepts (checked per call)
        const val MAX_H = 3072
        private const val NS_PER_MS = 1_000_000L
        private const val NS_PER_MS_F = 1_000_000f
    }
}
