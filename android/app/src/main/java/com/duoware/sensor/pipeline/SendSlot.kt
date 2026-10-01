package com.duoware.sensor.pipeline

import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * The frame hand-off between the pipeline thread and the sender thread (PROTOCOL.md §2 "Phone rules", M2 plan
 * step 9): three preallocated buffers, one being filled, one ready, one being sent, and a depth-1 "ready" slot. A
 * ready frame replaced before it was sent is dropped and counted in [dropped]: old frames are never queued.
 *
 * Buffers are used by index so that nothing is boxed or allocated per frame.
 */
class SendSlot(buffers: Int = 3, size: Int = 8192) {
    private val bufs = Array(buffers) { ByteArray(size) }
    private val lens = IntArray(buffers)
    private val sentOffsets = IntArray(buffers)
    private val readyNs = LongArray(buffers)       // when the frame was ready (detection end), sync clock
    private val capNs = LongArray(buffers)
    private val free = IntArray(buffers) { it }
    private var nFree = buffers
    private var ready = -1
    private val lock = ReentrantLock()
    private val notReady = lock.newCondition()
    private var closed = false

    @Volatile var dropped = 0L
        private set

    private val wrappers = Array(buffers) { java.nio.ByteBuffer.wrap(bufs[it]) }     // reused by the sender: no allocation per write

    fun buffer(i: Int): ByteArray = bufs[i]
    fun wrapper(i: Int): java.nio.ByteBuffer = wrappers[i]
    fun length(i: Int): Int = lens[i]
    fun sentOffset(i: Int): Int = sentOffsets[i]
    fun readyNs(i: Int): Long = readyNs[i]
    fun capNs(i: Int): Long = capNs[i]

    /** A free buffer index to fill (always available: the sender holds at most one, the slot at most one). */
    fun acquire(): Int = lock.withLock { free[--nFree] }

    /** Marks buffer [i] ready; a frame still waiting there is recycled and counted as dropped. */
    fun publish(i: Int, length: Int, sentOffset: Int, readyNs: Long, capNs: Long) {
        lock.withLock {
            lens[i] = length; sentOffsets[i] = sentOffset; this.readyNs[i] = readyNs; this.capNs[i] = capNs
            if (ready >= 0) {
                free[nFree++] = ready
                dropped++
            }
            ready = i
            notReady.signal()
        }
    }

    /** Sender: blocks until a frame is ready (or [timeoutMs] passes: returns -1; also -1 once closed). */
    fun take(timeoutMs: Long): Int {
        lock.withLock {
            var left = timeoutMs * 1_000_000L
            while (ready < 0 && !closed && left > 0) left = notReady.awaitNanos(left)
            val i = ready
            ready = -1
            return i
        }
    }

    /** Sender: done with buffer [i]. */
    fun release(i: Int) = lock.withLock { free[nFree++] = i }

    fun close() = lock.withLock {
        closed = true
        notReady.signalAll()
    }
}
