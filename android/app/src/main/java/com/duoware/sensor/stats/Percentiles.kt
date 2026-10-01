package com.duoware.sensor.stats

/**
 * p50 / p95 of the values added during the last [windowMs], without allocation: a ring of (time, value) and a
 * preallocated scratch array, with in-place selection (no sort that might allocate a work buffer).
 */
class Percentiles(private val capacity: Int, private val windowMs: Long) {
    private val times = LongArray(capacity)
    private val values = FloatArray(capacity)
    private val scratch = FloatArray(capacity)
    private var head = 0           // next write position
    private var size = 0

    fun add(nowMs: Long, value: Float) {
        times[head] = nowMs
        values[head] = value
        head = (head + 1) % capacity
        if (size < capacity) size++
    }

    /** Values inside the window, copied into the scratch array; returns how many. */
    private fun collect(nowMs: Long): Int {
        var n = 0
        for (k in 0 until size) {
            val i = (head - 1 - k + capacity) % capacity
            if (nowMs - times[i] > windowMs) break           // older entries follow
            scratch[n++] = values[i]
        }
        return n
    }

    fun count(nowMs: Long): Int = collect(nowMs)

    /** Fills [out] with p50 at [0] and p95 at [1]; returns false (out untouched) when the window is empty. */
    fun p50p95(nowMs: Long, out: FloatArray): Boolean {
        val n = collect(nowMs)
        if (n == 0) return false
        out[0] = select(scratch, n, rank(n, 0.50))
        out[1] = select(scratch, n, rank(n, 0.95))
        return true
    }

    companion object {
        /** Nearest-rank index (0-based) of quantile [q] among [n] values. */
        fun rank(n: Int, q: Double): Int = (Math.ceil(q * n).toInt() - 1).coerceIn(0, n - 1)

        /** The k-th smallest of a[0 until n] (Hoare quickselect, in place). */
        fun select(a: FloatArray, n: Int, k: Int): Float {
            var lo = 0
            var hi = n - 1
            while (lo < hi) {
                val pivot = a[(lo + hi) ushr 1]
                var i = lo
                var j = hi
                while (i <= j) {
                    while (a[i] < pivot) i++
                    while (a[j] > pivot) j--
                    if (i <= j) {
                        val t = a[i]; a[i] = a[j]; a[j] = t
                        i++; j--
                    }
                }
                if (k <= j) hi = j else if (k >= i) lo = i else return a[k]
            }
            return a[k]
        }
    }
}
