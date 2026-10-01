package com.duoware.sensor.net

import android.content.Context
import android.net.wifi.WifiManager

/**
 * `WIFI_MODE_FULL_LOW_LATENCY` while tracking in `wireless` mode (PROTOCOL.md §4.1): keeps Wi-Fi power saving from
 * adding jitter. Android honours it only with the app in the foreground and the screen on (the demo screen guarantees it).
 */
class WifiLatencyLock(ctx: Context) {
    private val lock = (ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
        .createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "duoware-lowlatency").apply { setReferenceCounted(false) }

    val held: Boolean get() = lock.isHeld

    fun acquire() { if (!lock.isHeld) lock.acquire() }

    fun release() { if (lock.isHeld) lock.release() }
}
