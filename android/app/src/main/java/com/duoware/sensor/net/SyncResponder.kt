package com.duoware.sensor.net

import com.duoware.sensor.proto.FrameWriter
import org.json.JSONObject

/**
 * PROTOCOL.md §3.2: answers a `sync` with `sync_r`. `t2` is stamped by the caller immediately after the read returns;
 * `t3` is stamped here as late as possible, just before the bytes are written (the reply is built around it, so the
 * gap is a few microseconds). A `sync` for another session is ignored.
 */
class SyncResponder(private val cam: Int, private val sid: String, private val clock: () -> Long) {
    private val prefix = "{\"v\":1,\"t\":\"sync_r\",\"cam\":$cam,\"sid\":\"$sid\",\"n\":"

    /**
     * Builds the reply for the message in [buf] (offset, length) into [out] and returns its length, or -1 when it is
     * not a `sync` of this session. [t2] is the receive stamp.
     */
    fun reply(buf: ByteArray, offset: Int, length: Int, t2: Long, out: ByteArray): Int {
        val o = try {
            JSONObject(String(buf, offset, length, Charsets.UTF_8))
        } catch (e: Exception) {
            return -1
        }
        if (o.optInt("v", -1) != 1 || o.optString("t") != "sync" || o.optString("sid") != sid) return -1
        val n = o.optLong("n", Long.MIN_VALUE)
        val t1 = o.optLong("t1", Long.MIN_VALUE)
        if (n == Long.MIN_VALUE || t1 == Long.MIN_VALUE) return -1
        var p = 0
        for (c in prefix) out[p++] = c.code.toByte()
        p = FrameWriter.writeLong(out, p, n)
        p = putAscii(out, p, ",\"t1\":")
        p = FrameWriter.writeLong(out, p, t1)
        p = putAscii(out, p, ",\"t2\":")
        p = FrameWriter.writeLong(out, p, t2)
        p = putAscii(out, p, ",\"t3\":")
        p = FrameWriter.writeLong(out, p, clock())          // t3: as late as possible
        out[p++] = '}'.code.toByte()
        return p
    }

    private fun putAscii(dst: ByteArray, p: Int, s: String): Int {
        var q = p
        for (c in s) dst[q++] = c.code.toByte()
        return q
    }
}
