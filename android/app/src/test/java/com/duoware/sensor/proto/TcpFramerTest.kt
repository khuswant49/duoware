package com.duoware.sensor.proto

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class TcpFramerTest {
    private fun framed(vararg messages: String): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        for (m in messages) {
            val body = m.toByteArray()
            val head = ByteArray(4)
            TcpFramer.writeHeader(head, 0, body.size)
            out.write(head)
            out.write(body)
        }
        return out.toByteArray()
    }

    private fun collect(d: TcpDeframer, bytes: ByteArray, chunk: Int): List<String> {
        val got = mutableListOf<String>()
        var i = 0
        while (i < bytes.size) {
            val n = minOf(chunk, bytes.size - i)
            d.feed(bytes, i, n) { b, off, len -> got += String(b, off, len) }
            i += n
        }
        return got
    }

    @Test
    fun headerIsBigEndian() {
        val h = ByteArray(4)
        TcpFramer.writeHeader(h, 0, 0x0102)
        assertEquals(listOf<Byte>(0, 0, 1, 2), h.toList())
    }

    @Test
    fun messagesSurviveEverySplit() {
        val msgs = arrayOf("{\"a\":1}", "{\"t\":\"sync\",\"n\":2}", "x".repeat(8192))
        val bytes = framed(*msgs)
        for (chunk in listOf(1, 2, 3, 5, 7, 100, bytes.size)) {
            assertEquals("chunk $chunk", msgs.toList(), collect(TcpDeframer(), bytes, chunk))
        }
    }

    @Test
    fun zeroOrOversizedLengthsBreakTheStream() {
        assertThrows(FramingException::class.java) {
            TcpDeframer().feed(byteArrayOf(0, 0, 0, 0), 0, 4) { _, _, _ -> }
        }
        assertThrows(FramingException::class.java) {
            TcpDeframer().feed(byteArrayOf(0, 0, 0x20, 0x01), 0, 4) { _, _, _ -> }       // 8193
        }
        assertThrows(IllegalArgumentException::class.java) { TcpFramer.writeHeader(ByteArray(4), 0, 8193) }
    }
}
