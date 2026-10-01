package com.duoware.sensor.preview

import com.duoware.sensor.net.Session
import com.duoware.sensor.pipeline.NativeBridge
import com.duoware.sensor.pipeline.PreviewSource
import okio.ByteString.Companion.toByteString
import java.nio.ByteBuffer
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * The `preview` thread (PROTOCOL.md §4.6): encodes the greyscale copy the pipeline made into a JPEG and sends
 * `DWP1` + header + JPEG as a binary WebSocket message, at most `preview.fps`, off the tracking threads. Skipped while
 * OkHttp already holds more than [MAX_QUEUE_BYTES] unsent (the send path is busy). `fps = 0` turns it off.
 */
class PreviewEncoder(private val session: () -> Session?) : PreviewSource {
    @Volatile var fps = 0.0
    @Volatile override var width = DEFAULT_WIDTH
        private set
    @Volatile var quality = DEFAULT_QUALITY
    @Volatile var sent = 0L
        private set
    @Volatile var skipped = 0L
        private set

    private val gray = ByteBuffer.allocateDirect(MAX_GRAY_BYTES)
    private val jpeg = ByteBuffer.allocateDirect(MAX_JPEG_BYTES)
    private val message = ByteArray(HEADER_BYTES + MAX_JPEG_BYTES)
    private val lock = ReentrantLock()
    private val ready = lock.newCondition()
    private var busy = false                  // the buffer is acquired or waiting for the encoder
    private var pending = false               // published, not yet encoded
    private var pw = 0
    private var ph = 0
    private var pcap = 0L
    private var nextDueNs = 0L
    private var running = false
    private var thread: Thread? = null

    fun configure(fps: Double, width: Int, quality: Int) {
        this.fps = fps
        this.width = width.coerceIn(MIN_WIDTH, MAX_WIDTH)
        this.quality = quality.coerceIn(1, 100)
    }

    @Synchronized
    fun start() {
        if (running) return
        running = true
        thread = Thread({ loop() }, "preview").also { it.isDaemon = true; it.start() }
    }

    @Synchronized
    fun stop() {
        running = false
        lock.withLock { ready.signalAll() }
        thread?.join(JOIN_MS)
        thread = null
    }

    override fun acquire(nowNs: Long): ByteBuffer? {
        val f = fps
        if (f <= 0.0 || !running) return null
        lock.withLock {
            if (busy || nowNs < nextDueNs) return null
            busy = true
            nextDueNs = nowNs + (1e9 / f).toLong()
            gray.clear()
            return gray
        }
    }

    override fun publish(w: Int, h: Int, capNs: Long) {
        lock.withLock {
            pw = w; ph = h; pcap = capNs; pending = true
            ready.signal()
        }
    }

    override fun abort() {
        lock.withLock { busy = false }
    }

    private fun loop() {
        while (running) {
            var w: Int; var h: Int; var cap: Long
            lock.withLock {
                while (!pending && running) ready.await()
                if (!running) return
                w = pw; h = ph; cap = pcap
            }
            try {
                encodeAndSend(w, h, cap)
            } catch (e: Exception) {
                skipped++
            } finally {
                lock.withLock { pending = false; busy = false }
            }
        }
    }

    private fun encodeAndSend(w: Int, h: Int, capNs: Long) {
        val s = session() ?: return
        if (s.queueSize() > MAX_QUEUE_BYTES) { skipped++; return }
        gray.clear(); jpeg.clear()
        val len = NativeBridge.nativeEncodeJpeg(gray, w, h, quality, jpeg)
        if (len <= 0 || len > MAX_JPEG_BYTES) { skipped++; return }
        writeHeader(message, capNs, w, h)
        jpeg.position(0)
        jpeg.get(message, HEADER_BYTES, len)
        if (s.sendBinary(message.toByteString(0, HEADER_BYTES + len))) sent++ else skipped++
    }

    companion object {
        const val HEADER_BYTES = 16                    // PROTOCOL.md §4.6
        const val MAX_QUEUE_BYTES = 64 * 1024L         // M2 plan: skip when the WebSocket queue is over 64 KB
        const val MAX_JPEG_BYTES = 512 * 1024          // PROTOCOL.md §4.6: the server drops larger messages
        private const val MAX_GRAY_BYTES = 1280 * 720
        private const val MIN_WIDTH = 64
        private const val MAX_WIDTH = 1280
        private const val DEFAULT_WIDTH = 480
        private const val DEFAULT_QUALITY = 60
        private const val JOIN_MS = 2000L

        /** `DWP1`, `cap_ns` uint64 big-endian, width and height uint16 big-endian (PROTOCOL.md §4.6). */
        fun writeHeader(dst: ByteArray, capNs: Long, w: Int, h: Int) {
            dst[0] = 'D'.code.toByte(); dst[1] = 'W'.code.toByte(); dst[2] = 'P'.code.toByte(); dst[3] = '1'.code.toByte()
            for (i in 0 until 8) dst[4 + i] = (capNs ushr (56 - 8 * i)).toByte()
            dst[12] = (w ushr 8).toByte(); dst[13] = w.toByte()
            dst[14] = (h ushr 8).toByte(); dst[15] = h.toByte()
        }
    }
}
