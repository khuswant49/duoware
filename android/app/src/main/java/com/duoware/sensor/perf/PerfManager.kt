package com.duoware.sensor.perf

import android.content.Context
import android.os.Build
import android.os.PerformanceHintManager
import android.os.PowerManager
import com.duoware.sensor.proto.PerfState

/**
 * Android performance features (DECISIONS.md D34) and what is actually active, for `status.perf`: the ADPF hint session
 * over the pipeline thread and the native workers, sustained-performance mode (set by the activity on its window),
 * and the battery-optimisation exemption. `null` in [state] means "not measurable here", never "off".
 */
class PerfManager(private val ctx: Context) {
    private var hint: PerformanceHintManager.Session? = null
    @Volatile var sustainedMode: Boolean? = null
    @Volatile var foregroundService = false
    @Volatile var wakeLock = false
    @Volatile var wifiLowLatency: Boolean? = null

    /** (Re)creates the hint session for [tids] and the frame period; no-op below API 31 or when unsupported. */
    @Synchronized
    fun startHint(tids: IntArray, framePeriodNs: Long) {
        closeHint()
        val ids = tids.filter { it > 0 }.toIntArray()
        if (Build.VERSION.SDK_INT < 31 || ids.isEmpty() || framePeriodNs <= 0) return
        val m = ctx.getSystemService(PerformanceHintManager::class.java) ?: return
        hint = try { m.createHintSession(ids, framePeriodNs) } catch (e: Exception) { null }
    }

    /** Called every frame with the pipeline's work time: allocation free. */
    fun reportWork(ns: Long) {
        val h = hint ?: return
        if (ns > 0) h.reportActualWorkDuration(ns)
    }

    @Synchronized
    fun updateTarget(framePeriodNs: Long) {
        hint?.updateTargetWorkDuration(framePeriodNs)
    }

    @Synchronized
    fun closeHint() {
        hint?.close()
        hint = null
    }

    fun batteryOptExempt(): Boolean =
        (ctx.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(ctx.packageName)

    fun state() = PerfState(foregroundService, wakeLock, wifiLowLatency, sustainedMode, hint != null, batteryOptExempt())
}
