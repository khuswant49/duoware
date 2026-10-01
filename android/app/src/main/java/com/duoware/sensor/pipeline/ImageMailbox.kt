package com.duoware.sensor.pipeline

import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * The one-slot hand-off between the camera thread and the pipeline thread (M2 plan step 8). The camera thread [put]s
 * the newest image; one still waiting there is closed and counted in [skipped] (newest wins: an old frame is worth
 * nothing). The pipeline thread [take]s, blocking while the slot is empty. No allocation per frame.
 */
class ImageMailbox<T : AutoCloseable> {
    private val lock = ReentrantLock()
    private val notEmpty = lock.newCondition()
    private var item: T? = null
    private var pendingAvailNs = 0L
    private var closed = false

    /** `avail_ns` (sync clock) of the image returned by the last [take]. Read by the single consumer only. */
    var availNs = 0L
        private set

    @Volatile var skipped = 0L
        private set

    /** Images the camera delivered to [put], processed or not (the sensor's real frame rate). */
    @Volatile var received = 0L
        private set

    /** Counts frames the sensor delivered but the app never saw (a timestamp gap), see [CameraController]. */
    fun addSkipped(n: Long) {
        skipped += n
    }

    fun put(image: T, availNs: Long) {
        var old: T? = null
        lock.withLock {
            received++
            if (closed) {
                old = image
            } else {
                old = item
                item = image
                pendingAvailNs = availNs
                if (old != null) skipped++
                notEmpty.signal()
            }
        }
        old?.close()
    }

    /** Blocks until an image is available; null once the mailbox is closed. */
    fun take(): T? {
        lock.withLock {
            while (item == null && !closed) notEmpty.await()
            val i = item
            item = null
            availNs = pendingAvailNs
            return i
        }
    }

    /** Wakes the consumer with null and closes an image still waiting. */
    fun close() {
        var old: T? = null
        lock.withLock {
            closed = true
            old = item
            item = null
            notEmpty.signalAll()
        }
        old?.close()
    }

    /** Reopens after [close] (a new camera session). */
    fun reopen() = lock.withLock { closed = false }
}
