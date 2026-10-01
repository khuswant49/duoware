package com.duoware.sensor.camera

/**
 * The exposure, rolling-shutter skew and frame duration of recent captures, looked up by sensor timestamp
 * (M2 plan step 8). `onCaptureCompleted` writes it; the pipeline reads it. Preallocated ring, no allocation.
 *
 * A result can arrive after its image: [exposureNs] and [skewNs] then fall back to the latest entry (constant in manual
 * mode) and [misses] counts it (shown in the app's debug stats).
 */
class CaptureMeta(private val capacity: Int = CAPACITY) {
    private val ts = LongArray(capacity)
    private val exp = LongArray(capacity)
    private val skew = LongArray(capacity)
    private val dur = LongArray(capacity)
    private var head = 0                     // next write
    private var size = 0
    private val lock = Any()

    /** Times a lookup found no entry for the image's timestamp. */
    @Volatile var misses = 0L
        private set

    fun record(timestampNs: Long, exposureNs: Long, skewNs: Long, frameDurationNs: Long) = synchronized(lock) {
        ts[head] = timestampNs; exp[head] = exposureNs; skew[head] = skewNs; dur[head] = frameDurationNs
        head = (head + 1) % capacity
        if (size < capacity) size++
    }

    /** Index of the entry for [timestampNs], else -1. Caller holds the lock. */
    private fun find(timestampNs: Long): Int {
        for (k in 0 until size) {
            val i = (head - 1 - k + capacity) % capacity
            if (ts[i] == timestampNs) return i
        }
        return -1
    }

    private fun latest(): Int = if (size == 0) -1 else (head - 1 + capacity) % capacity

    /** Exposure of the capture with this timestamp; the latest known one (or [fallbackNs]) if its result is missing. */
    fun exposureNs(timestampNs: Long, fallbackNs: Long): Long = synchronized(lock) {
        val i = find(timestampNs)
        if (i >= 0) return exp[i]
        misses++
        val l = latest()
        if (l >= 0) exp[l] else fallbackNs
    }

    /** Rolling-shutter skew (first to last row start) of that capture; 0 if the device does not report it. */
    fun skewNs(timestampNs: Long): Long = synchronized(lock) {
        val i = find(timestampNs)
        if (i >= 0) return skew[i]
        val l = latest()
        if (l >= 0) skew[l] else 0L
    }

    fun frameDurationNs(timestampNs: Long): Long = synchronized(lock) {
        val i = find(timestampNs)
        if (i >= 0) dur[i] else latest().let { if (it >= 0) dur[it] else 0L }
    }

    companion object {
        const val CAPACITY = 16          // M2 plan step 8
    }
}
