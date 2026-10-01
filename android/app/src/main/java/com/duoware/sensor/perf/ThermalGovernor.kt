package com.duoware.sensor.perf

import com.duoware.sensor.proto.ThermalPolicy
import java.util.Locale

/**
 * The thermal ladder (PROTOCOL.md §4.4 `thermal.*`, DECISIONS.md D34), pure: fed once a second with the thermal
 * headroom and status, it decides the level. Level k < len(fps_steps) runs at fps_steps[k] x the base fps; higher
 * levels step through resolution_steps at the last fps step.
 *
 * - One step **down** when headroom >= headroom_down or status >= status_down; after a step down, no further step
 *   down for forecast_s (the previous step needs time to show in the temperature).
 * - One step **up** after up_after_s continuously with headroom < headroom_up and status < status_down.
 * - headroom == null (API < 30): status only. A NaN reading keeps the last valid one for up to NAN_HOLD_MS.
 */
class ThermalGovernor(
    private val policy: ThermalPolicy,
    private val baseFps: Double,
    private val baseResolution: Pair<Int, Int>,
) {
    data class Level(val index: Int, val fps: Double, val resolution: Pair<Int, Int>, val reason: String?)

    val maxLevel: Int = policy.fpsSteps.size - 1 + policy.resolutionSteps.size

    var level: Level = levelAt(0, null)
        private set

    private var lastDownMs = Long.MIN_VALUE
    private var belowSinceMs: Long? = null
    private var lastValidHeadroom: Double? = null
    private var lastValidMs = Long.MIN_VALUE

    fun levelAt(index: Int, reason: String?): Level {
        val nFps = policy.fpsSteps.size
        return if (index < nFps) {
            Level(index, baseFps * policy.fpsSteps[index], baseResolution, reason)
        } else {
            Level(index, baseFps * policy.fpsSteps.last(), policy.resolutionSteps[index - nFps], reason)
        }
    }

    /**
     * Keeps the ladder position across a rebuild (new base fps / resolution or policy from a `settings` message): the
     * phone is as hot as it was, so it must not jump back to level 0. Clamped to the new ladder.
     */
    fun resume(index: Int, reason: String?) {
        level = levelAt(index.coerceIn(0, maxLevel), reason)
    }

    /** One reading. [headroom] is null when the device has no headroom API, NaN when it had no reading. */
    fun tick(nowMs: Long, headroom: Double?, status: Int): Level {
        val h = effectiveHeadroom(nowMs, headroom)
        val hot = (h != null && h >= policy.headroomDown) || status >= policy.statusDown
        if (hot) {
            belowSinceMs = null
            val waited = lastDownMs == Long.MIN_VALUE || nowMs - lastDownMs >= (policy.forecastS * 1000).toLong()
            if (level.index < maxLevel && waited) {
                val why = if (h != null && h >= policy.headroomDown) {
                    String.format(Locale.ROOT, "headroom %.2f >= %.2f", h, policy.headroomDown)
                } else {
                    "thermal status $status >= ${policy.statusDown}"
                }
                level = levelAt(level.index + 1, why)
                lastDownMs = nowMs
            }
            return level
        }
        val cool = (h == null || h < policy.headroomUp) && status < policy.statusDown
        if (!cool) {
            belowSinceMs = null
            return level
        }
        val since = belowSinceMs ?: nowMs.also { belowSinceMs = it }
        if (level.index > 0 && nowMs - since >= (policy.upAfterS * 1000).toLong()) {
            val next = level.index - 1
            level = levelAt(next, if (next == 0) null else level.reason)
            belowSinceMs = nowMs                                   // the next step up needs another full period
        }
        return level
    }

    private fun effectiveHeadroom(nowMs: Long, headroom: Double?): Double? {
        if (headroom == null) return null
        if (!headroom.isNaN()) {
            lastValidHeadroom = headroom
            lastValidMs = nowMs
            return headroom
        }
        val last = lastValidHeadroom ?: return null
        return if (nowMs - lastValidMs <= NAN_HOLD_MS) last else null
    }

    companion object {
        const val NAN_HOLD_MS = 10_000L     // M2 plan step 6: a NaN keeps the last valid reading for up to 10 s
    }
}
