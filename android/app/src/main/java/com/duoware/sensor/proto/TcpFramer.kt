package com.duoware.sensor.proto

/**
 * PROTOCOL.md §2.1 TCP framing (mode `wired_adb`): a 4-byte unsigned big-endian length `N` (1…8192), then `N` bytes
 * of UTF-8 JSON. No allocation per message: the header goes into a caller-owned array and the deframer reuses one
 * internal buffer.
 */
object TcpFramer {
    const val HEADER_BYTES = 4
    const val MAX_MESSAGE = 8192            // PROTOCOL.md §2.1

    /** Writes the length header for a message of [length] bytes at [offset]. */
    fun writeHeader(dst: ByteArray, offset: Int, length: Int) {
        require(length in 1..MAX_MESSAGE) { "message length $length outside 1..$MAX_MESSAGE" }
        dst[offset] = (length ushr 24).toByte()
        dst[offset + 1] = (length ushr 16).toByte()
        dst[offset + 2] = (length ushr 8).toByte()
        dst[offset + 3] = length.toByte()
    }
}

/** The stream is broken (bad length): the connection must be closed, PROTOCOL.md §2.1. */
class FramingException(message: String) : Exception(message)

/** Splits a byte stream into messages. [onMessage] gets (buffer, offset, length) valid only during the call. */
class TcpDeframer {
    private val buf = ByteArray(TcpFramer.HEADER_BYTES + TcpFramer.MAX_MESSAGE)
    private var have = 0
    private var need = -1                    // body length once the header is complete

    @Throws(FramingException::class)
    fun feed(src: ByteArray, offset: Int, length: Int, onMessage: (ByteArray, Int, Int) -> Unit) {
        var i = offset
        val end = offset + length
        while (i < end) {
            if (need < 0) {
                val take = minOf(TcpFramer.HEADER_BYTES - have, end - i)
                System.arraycopy(src, i, buf, have, take)
                have += take
                i += take
                if (have == TcpFramer.HEADER_BYTES) {
                    val n = ((buf[0].toInt() and 0xff) shl 24) or ((buf[1].toInt() and 0xff) shl 16) or
                        ((buf[2].toInt() and 0xff) shl 8) or (buf[3].toInt() and 0xff)
                    if (n <= 0 || n > TcpFramer.MAX_MESSAGE) throw FramingException("bad message length $n")
                    need = n
                    have = 0
                }
            } else {
                val take = minOf(need - have, end - i)
                System.arraycopy(src, i, buf, TcpFramer.HEADER_BYTES + have, take)
                have += take
                i += take
                if (have == need) {
                    onMessage(buf, TcpFramer.HEADER_BYTES, need)
                    have = 0
                    need = -1
                }
            }
        }
    }
}
