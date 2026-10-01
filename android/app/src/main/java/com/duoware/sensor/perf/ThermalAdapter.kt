package com.duoware.sensor.perf

import android.content.Context
import android.os.Build
import android.os.PowerManager
import java.util.concurrent.Executor

/**
 * Android's thermal readings for the [ThermalGovernor] (PROTOCOL.md §4.5 `thermal`, DECISIONS.md D34): the current
 * thermal status, the headroom forecast (API 30+; `null` below, NaN when the device has no reading) and a status
 * listener for an immediate reaction to jumps.
 */
class ThermalAdapter(ctx: Context) {
    private val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
    private var listener: PowerManager.OnThermalStatusChangedListener? = null

    fun status(): Int = pm.currentThermalStatus

    /** `getThermalHeadroom(forecastS)`: `null` before API 30, NaN without a reading. */
    fun headroom(forecastS: Double): Double? =
        if (Build.VERSION.SDK_INT >= 30) pm.getThermalHeadroom(forecastS.toInt().coerceIn(0, 60)).toDouble() else null

    /** [onJump] runs on [executor] whenever the thermal status changes. */
    fun listen(executor: Executor, onJump: (Int) -> Unit) {
        stop()
        val l = PowerManager.OnThermalStatusChangedListener { onJump(it) }
        pm.addThermalStatusListener(executor, l)
        listener = l
    }

    fun stop() {
        listener?.let { pm.removeThermalStatusListener(it) }
        listener = null
    }
}
