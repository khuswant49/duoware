package com.duoware.sensor.store

import android.content.SharedPreferences
import java.util.UUID

/**
 * App-private settings: device ID, token, paired server, mode, camera choice, manual host. The token only authorises
 * sending poses on a local network (DECISIONS.md D6), so plain `SharedPreferences` is enough; `EncryptedSharedPreferences`
 * is deprecated and not used.
 */
class Prefs(private val sp: SharedPreferences, private val newId: () -> String = { UUID.randomUUID().toString() }) {
    /** A UUID created once (PROTOCOL.md §4.3 `device_id`). */
    val deviceId: String
        get() = sp.getString(DEVICE_ID, null) ?: newId().also { sp.edit().putString(DEVICE_ID, it).apply() }

    var token: String?
        get() = sp.getString(TOKEN, null)
        set(v) = put(TOKEN, v)

    var serverId: String?
        get() = sp.getString(SERVER_ID, null)
        set(v) = put(SERVER_ID, v)

    /** `wired` or `wireless` (the user's choice, PROTOCOL.md §4.1). */
    var mode: String
        get() = sp.getString(MODE, WIRED) ?: WIRED
        set(v) = put(MODE, if (v == WIRELESS) WIRELESS else WIRED)

    var cameraId: String?
        get() = sp.getString(CAMERA_ID, null)
        set(v) = put(CAMERA_ID, v)

    var manualHost: String?
        get() = sp.getString(MANUAL_HOST, null)
        set(v) = put(MANUAL_HOST, v?.trim()?.ifEmpty { null })

    private fun put(key: String, v: String?) {
        sp.edit().apply { if (v == null) remove(key) else putString(key, v) }.apply()
    }

    companion object {
        const val WIRED = "wired"
        const val WIRELESS = "wireless"
        private const val DEVICE_ID = "device_id"
        private const val TOKEN = "token"
        private const val SERVER_ID = "server_id"
        private const val MODE = "mode"
        private const val CAMERA_ID = "camera_id"
        private const val MANUAL_HOST = "manual_host"
    }
}
