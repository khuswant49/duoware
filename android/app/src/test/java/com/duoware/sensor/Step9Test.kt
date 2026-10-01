package com.duoware.sensor

import android.content.SharedPreferences
import com.duoware.sensor.net.SyncResponder
import com.duoware.sensor.proto.Messages
import com.duoware.sensor.store.Prefs
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** M2 step 9, pure parts: the `sync_r` reply (PROTOCOL.md §3.2) and the preferences. Unit test only: no phone, no network. */
class Step9Test {
    private val sid = "9f2c4e1a7b3d5c60"

    @Test
    fun syncReplyEqualsTheEncoderOutputAndStampsT3Last() {
        val r = SyncResponder(1, sid) { 81_234_000_040_000L }
        val req = "{\"v\":1,\"t\":\"sync\",\"sid\":\"$sid\",\"n\":123,\"t1\":5512345678901}".toByteArray()
        val out = ByteArray(256)
        val len = r.reply(req, 0, req.size, 81_234_000_000_000L, out)
        val text = String(out, 0, len, Charsets.UTF_8)
        assertEquals(Messages.syncReply(1, sid, 123, 5_512_345_678_901L, 81_234_000_000_000L, 81_234_000_040_000L), text)
        val o = JSONObject(text)
        assertEquals("sync_r", o.getString("t")); assertEquals(123L, o.getLong("n"))
    }

    @Test
    fun syncOfAnotherSessionOrBadMessagesAreIgnored() {
        val r = SyncResponder(1, sid) { 5L }
        val out = ByteArray(256)
        fun reply(s: String): Int = s.toByteArray().let { r.reply(it, 0, it.size, 1L, out) }
        assertEquals(-1, reply("{\"v\":1,\"t\":\"sync\",\"sid\":\"other\",\"n\":1,\"t1\":2}"))
        assertEquals(-1, reply("{\"v\":2,\"t\":\"sync\",\"sid\":\"$sid\",\"n\":1,\"t1\":2}"))
        assertEquals(-1, reply("{\"v\":1,\"t\":\"frame\",\"sid\":\"$sid\"}"))
        assertEquals(-1, reply("{\"v\":1,\"t\":\"sync\",\"sid\":\"$sid\",\"n\":1}"))
        assertEquals(-1, reply("not json"))
    }

    private class FakePrefs : SharedPreferences {
        val map = HashMap<String, String?>()
        override fun getString(key: String, def: String?): String? = map[key] ?: def
        override fun edit(): SharedPreferences.Editor = object : SharedPreferences.Editor {
            private val pending = HashMap<String, String?>()
            override fun putString(key: String, value: String?) = apply { pending[key] = value }
            override fun remove(key: String) = apply { pending[key] = null }
            override fun apply() { for ((k, v) in pending) if (v == null) map.remove(k) else map[k] = v }
            override fun commit(): Boolean { apply(); return true }
            override fun putStringSet(k: String, v: MutableSet<String>?) = this
            override fun putInt(k: String, v: Int) = this
            override fun putLong(k: String, v: Long) = this
            override fun putFloat(k: String, v: Float) = this
            override fun putBoolean(k: String, v: Boolean) = this
            override fun clear() = this
        }
        override fun getAll(): MutableMap<String, *> = map
        override fun getStringSet(k: String, d: MutableSet<String>?) = d
        override fun getInt(k: String, d: Int) = d
        override fun getLong(k: String, d: Long) = d
        override fun getFloat(k: String, d: Float) = d
        override fun getBoolean(k: String, d: Boolean) = d
        override fun contains(k: String) = map.containsKey(k)
        override fun registerOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
        override fun unregisterOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
    }

    @Test
    fun prefsCreateTheDeviceIdOnceAndKeepTokenModeAndHost() {
        val sp = FakePrefs()
        var n = 0
        val p = Prefs(sp) { "id-${++n}" }
        val id = p.deviceId
        assertEquals(id, p.deviceId); assertEquals(id, Prefs(sp) { "other" }.deviceId)
        assertNull(p.token); assertEquals(Prefs.WIRED, p.mode)
        p.token = "abc"; p.mode = Prefs.WIRELESS; p.manualHost = "  192.168.1.5 "
        assertEquals("abc", p.token); assertEquals(Prefs.WIRELESS, p.mode); assertEquals("192.168.1.5", p.manualHost)
        p.token = null; p.manualHost = "  "
        assertNull(p.token); assertNull(p.manualHost)
        p.mode = "nonsense"
        assertEquals(Prefs.WIRED, p.mode)
        assertNotEquals("", id)
    }
}
