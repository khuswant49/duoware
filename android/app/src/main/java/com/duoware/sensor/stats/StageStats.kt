package com.duoware.sensor.stats

/** PROTOCOL.md §4.5 `stages_ms`: the five stages, p50/p95 over the last 10 s, at up to 240 fps. */
class StageStats {
    enum class Stage(val wire: String) {
        PIPELINE("pipeline"),          // avail_ns - cap_ns
        DETECT_FULL("detect_full"),
        DETECT_ROI("detect_roi"),
        SEND("send"),                  // detection end -> sent_ns
        CAP_TO_SENT("cap_to_sent"),    // sent_ns - cap_ns
    }

    private val rings = Array(Stage.entries.size) { Percentiles(CAPACITY, WINDOW_MS) }

    fun add(stage: Stage, nowMs: Long, ms: Float) = rings[stage.ordinal].add(nowMs, ms)

    /** p50/p95 of one stage into [out]; false when it has no samples in the window. */
    fun get(stage: Stage, nowMs: Long, out: FloatArray): Boolean = rings[stage.ordinal].p50p95(nowMs, out)

    companion object {
        const val WINDOW_MS = 10_000L          // PROTOCOL.md §4.5: over the last 10 s
        const val CAPACITY = 2400              // 10 s at 240 fps
    }
}
