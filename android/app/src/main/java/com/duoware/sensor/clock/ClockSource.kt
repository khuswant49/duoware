package com.duoware.sensor.clock

/** PROTOCOL.md §3.1: the phone's sync clock, the clock of the camera's sensor timestamps. */
enum class SyncClock(val wire: String) {
    BOOTTIME("boottime"),
    MONOTONIC("monotonic"),
    UNKNOWN("unknown"),
}

/**
 * Decides the sync clock once the camera runs and gives "now" on it. Pure apart from the two clock functions it is
 * given (the app passes `SystemClock::elapsedRealtimeNanos` and `System::nanoTime`).
 */
class ClockSource(private val boottimeNs: () -> Long, private val monotonicNs: () -> Long) {
    var clock: SyncClock = SyncClock.UNKNOWN
        private set

    /** Decides from the first frame (PROTOCOL.md §3.1); returns the decision. */
    fun decide(tsSourceRealtime: Boolean, firstCapNs: Long): SyncClock {
        clock = decide(tsSourceRealtime, firstCapNs, boottimeNs(), monotonicNs())
        return clock
    }

    /** Now on the sync clock; with UNKNOWN the boottime clock (the server keeps the camera unsynced anyway). */
    fun now(): Long = if (clock == SyncClock.MONOTONIC) monotonicNs() else boottimeNs()

    companion object {
        const val MAX_AGE_NS = 1_000_000_000L      // PROTOCOL.md §3.1: a capture time must be within 1 s of "now"

        /** PROTOCOL.md §3.1 rules 1 and 2, as a pure function of the readings taken at the first frame. */
        fun decide(tsSourceRealtime: Boolean, firstCapNs: Long, boottimeNow: Long, monotonicNow: Long): SyncClock {
            if (tsSourceRealtime) return SyncClock.BOOTTIME
            if (boottimeNow - firstCapNs in 0..MAX_AGE_NS) return SyncClock.BOOTTIME
            if (monotonicNow - firstCapNs in 0..MAX_AGE_NS) return SyncClock.MONOTONIC
            return SyncClock.UNKNOWN
        }
    }
}
