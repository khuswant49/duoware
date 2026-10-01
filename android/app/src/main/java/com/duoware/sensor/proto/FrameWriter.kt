package com.duoware.sensor.proto

/**
 * Writes one PROTOCOL.md §2 `frame` message into a caller-owned ByteArray with **no allocation** (hot path, D31).
 *
 * Field order is that of PROTOCOL.md §2 except `sent_ns`, which comes last: it is reserved as 20 spaces when the frame
 * is built and stamped by the sender thread just before the write ([stampSent]), leaving trailing spaces (valid JSON).
 *
 * Usage: [begin], [marker] per detected marker, [end] (returns the length); later [stampSent] with [sentFieldOffset].
 */
class FrameWriter(private val capacity: Int = MAX_BYTES) {
    private var buf: ByteArray = EMPTY
    private var pos = 0
    private var markers = 0

    /** Offset of the 20 reserved characters after `"sent_ns":` in the last frame written. */
    var sentFieldOffset = -1
        private set

    /** Starts a frame. [sid] is the 16 ASCII hex characters of the session ID. */
    fun begin(
        dst: ByteArray, cam: Int, sid: ByteArray, seq: Long, capNs: Long, expNs: Long, skewNs: Long, availNs: Long,
        w: Int, h: Int, full: Boolean, searched: IntArray, nSearched: Int,
    ) {
        require(dst.size >= capacity) { "buffer smaller than the frame capacity" }
        buf = dst
        pos = 0
        markers = 0
        put(K_HEAD)           // {"v":1,"t":"frame","cam":
        putLong(cam.toLong())
        put(K_SID); put(sid); put(QUOTE)
        put(K_SEQ); putLong(seq)
        put(K_CAP); putLong(capNs)
        put(K_EXP); putLong(expNs)
        put(K_SKEW); putLong(skewNs)
        put(K_AVAIL); putLong(availNs)
        put(K_W); putLong(w.toLong())
        put(K_H); putLong(h.toLong())
        put(if (full) K_SCAN_FULL else K_SCAN_ROI)
        put(K_SEARCHED)
        if (!full) {
            for (i in 0 until nSearched) {
                if (i > 0) put(COMMA)
                putLong(searched[i].toLong())
            }
        }
        put(K_M)              // ],"m":[
    }

    /**
     * One marker: [corners] holds x0, y0 … x3, y3 starting at [offset]. Returns false (and writes nothing) when the
     * frame would exceed its capacity; the caller stops adding markers.
     */
    fun marker(id: Int, corners: FloatArray, offset: Int): Boolean {
        if (pos + MARKER_MAX_BYTES + TAIL_BYTES > capacity) return false
        if (markers > 0) put(COMMA)
        put(LBRACKET)
        putLong(id.toLong())
        for (i in 0 until 8) {
            put(COMMA)
            putTenths(corners[offset + i])
        }
        put(RBRACKET)
        markers++
        return true
    }

    /** Closes the frame and returns its length in bytes. */
    fun end(): Int {
        put(K_SENT)           // ],"sent_ns":
        sentFieldOffset = pos
        for (i in 0 until SENT_FIELD_CHARS) buf[pos++] = SPACE
        put(RBRACE)
        return pos
    }

    // ------------------------------------------------------------------------------------------------ writing

    private fun put(b: Byte) {
        buf[pos++] = b
    }

    private fun put(bytes: ByteArray) {
        System.arraycopy(bytes, 0, buf, pos, bytes.size)
        pos += bytes.size
    }

    private fun putLong(v: Long) {
        pos = writeLong(buf, pos, v)
    }

    /** A float rounded to 0.1 by integer arithmetic; `-0.0` (and anything that rounds to it) is written `0.0`. */
    private fun putTenths(f: Float) {
        val tenths = Math.round(f.toDouble() * 10.0)
        if (tenths < 0) put(MINUS)
        val a = Math.abs(tenths)
        putLong(a / 10)
        put(DOT)
        put((ZERO + (a % 10).toInt()).toByte())
    }

    companion object {
        const val MAX_BYTES = 8192                   // PROTOCOL.md §2: hard maximum message size
        const val SENT_FIELD_CHARS = 20              // room for any non-negative int64 in decimal (19 digits)
        private const val MARKER_MAX_BYTES = 2 + 11 + 8 * (1 + 12) + 1   // [id,8 corners] with the widest numbers
        private const val TAIL_BYTES = 13 + SENT_FIELD_CHARS + 1           // ],"sent_ns": + reserved field + }

        private val EMPTY = ByteArray(0)
        private val K_HEAD = "{\"v\":1,\"t\":\"frame\",\"cam\":".toByteArray()
        private val K_SID = ",\"sid\":\"".toByteArray()
        private val K_SEQ = ",\"seq\":".toByteArray()
        private val K_CAP = ",\"cap_ns\":".toByteArray()
        private val K_EXP = ",\"exp_ns\":".toByteArray()
        private val K_SKEW = ",\"skew_ns\":".toByteArray()
        private val K_AVAIL = ",\"avail_ns\":".toByteArray()
        private val K_W = ",\"w\":".toByteArray()
        private val K_H = ",\"h\":".toByteArray()
        private val K_SCAN_FULL = ",\"scan\":\"full\"".toByteArray()
        private val K_SCAN_ROI = ",\"scan\":\"roi\"".toByteArray()
        private val K_SEARCHED = ",\"searched\":[".toByteArray()
        private val K_M = "],\"m\":[".toByteArray()
        private val K_SENT = "],\"sent_ns\":".toByteArray()
        private const val QUOTE = '"'.code.toByte()
        private const val COMMA = ','.code.toByte()
        private const val LBRACKET = '['.code.toByte()
        private const val RBRACKET = ']'.code.toByte()
        private const val RBRACE = '}'.code.toByte()
        private const val MINUS = '-'.code.toByte()
        private const val DOT = '.'.code.toByte()
        private const val SPACE = ' '.code.toByte()
        private const val ZERO = '0'.code

        /** Writes the decimal digits of [v] at [p]; returns the new position. No allocation. */
        fun writeLong(dst: ByteArray, p: Int, v: Long): Int {
            var pos = p
            if (v == 0L) {
                dst[pos++] = ZERO.toByte()
                return pos
            }
            var x = v
            if (x < 0) {
                dst[pos++] = MINUS
            }
            val start = pos
            while (x != 0L) {
                val d = (x % 10).toInt()
                dst[pos++] = (ZERO + Math.abs(d)).toByte()
                x /= 10
            }
            var i = start
            var j = pos - 1
            while (i < j) {
                val t = dst[i]; dst[i] = dst[j]; dst[j] = t
                i++; j--
            }
            return pos
        }

        /** Writes [sentNs] into the reserved field at [fieldOffset]; the unused characters stay spaces. */
        fun stampSent(dst: ByteArray, fieldOffset: Int, sentNs: Long) {
            require(sentNs >= 0) { "sent_ns must be non-negative" }
            for (i in 0 until SENT_FIELD_CHARS) dst[fieldOffset + i] = SPACE
            writeLong(dst, fieldOffset, sentNs)
        }
    }
}
