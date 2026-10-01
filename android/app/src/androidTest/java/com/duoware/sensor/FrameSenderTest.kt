package com.duoware.sensor

import android.os.Bundle
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.duoware.sensor.net.FrameSender
import com.duoware.sensor.net.NetworkBinder
import com.duoware.sensor.net.Route
import com.duoware.sensor.pipeline.SendSlot
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicLong

/**
 * M2 step 9: the real [FrameSender] on loopback sockets of the phone: a frame arrives (UDP datagram / TCP length
 * framing, PROTOCOL.md §2 and §2.1) with `sent_ns` stamped, and a `sync` from the "server" is answered with `sync_r`
 * on the same channel (§3.2). Loopback only: this is not a measurement of any Wi-Fi or USB link.
 */
@RunWith(AndroidJUnit4::class)
class FrameSenderTest {
    private val sid = "0123456789abcdef"

    private fun report(key: String, value: String) {
        Log.i("DuoSender", "$key: $value")
        InstrumentationRegistry.getInstrumentation().sendStatus(0, Bundle().apply { putString(key, value) })
    }

    /** A frame-shaped message whose `sent_ns` is the last field, a 20-char blank to be stamped (§2). */
    private fun publishFrame(slot: SendSlot, seq: Int) {
        val i = slot.acquire()
        val head = "{\"v\":1,\"t\":\"frame\",\"cam\":1,\"sid\":\"$sid\",\"seq\":$seq,\"sent_ns\":"
        val b = slot.buffer(i)
        var p = 0
        for (c in head) b[p++] = c.code.toByte()
        val sentOffset = p
        for (k in 0 until 20) b[p++] = ' '.code.toByte()
        b[p++] = '}'.code.toByte()
        slot.publish(i, p, sentOffset, System.nanoTime(), 1L)
    }

    @Test
    fun udpFrameArrivesStampedAndSyncIsAnswered() {
        val server = DatagramSocket(0, InetAddress.getByName("127.0.0.1")).also { it.soTimeout = 3000 }
        val slot = SendSlot()
        val sent = AtomicLong(0)
        val sender = FrameSender(slot, { System.nanoTime() }, { _, _, _ -> sent.incrementAndGet() })
        try {
            sender.start(FrameSender.Target("127.0.0.1", server.localPort, false, NetworkBinder(Route.Loopback), 1, sid))
            publishFrame(slot, 0)
            val pkt = DatagramPacket(ByteArray(8192), 8192)
            server.receive(pkt)
            val j = JSONObject(String(pkt.data, 0, pkt.length, Charsets.UTF_8))
            assertEquals("frame", j.getString("t")); assertTrue("sent_ns stamped", j.getLong("sent_ns") > 0)
            val req = "{\"v\":1,\"t\":\"sync\",\"sid\":\"$sid\",\"n\":7,\"t1\":42}".toByteArray()
            val t1 = System.nanoTime()
            server.send(DatagramPacket(req, req.size, pkt.socketAddress))
            val reply = DatagramPacket(ByteArray(8192), 8192)
            server.receive(reply)
            val r = JSONObject(String(reply.data, 0, reply.length, Charsets.UTF_8))
            val rtt = System.nanoTime() - t1
            assertEquals("sync_r", r.getString("t")); assertEquals(7L, r.getLong("n")); assertEquals(42L, r.getLong("t1"))
            assertTrue("t2 <= t3", r.getLong("t2") <= r.getLong("t3"))
            report("udp_loopback_sync_rtt_us", "${rtt / 1000}")
            assertEquals(1L, sent.get())
        } finally {
            sender.stop(); server.close(); slot.close()
        }
    }

    @Test
    fun tcpFramesAreLengthPrefixedSyncIsAnsweredAndTheSenderReconnects() {
        val listener = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).also { it.soTimeout = 5000 }
        val slot = SendSlot()
        val sender = FrameSender(slot, { System.nanoTime() }, { _, _, _ -> })
        try {
            sender.start(FrameSender.Target("127.0.0.1", listener.localPort, true, NetworkBinder(Route.Loopback), 1, sid))
            val first = listener.accept().also { it.soTimeout = 3000 }
            publishFrame(slot, 0)
            val inp = first.getInputStream()
            fun readMessage(): JSONObject {
                val h = ByteArray(4); inp.readFully(h)
                val n = ((h[0].toInt() and 0xff) shl 24) or ((h[1].toInt() and 0xff) shl 16) or ((h[2].toInt() and 0xff) shl 8) or (h[3].toInt() and 0xff)
                assertTrue("length 1..8192: $n", n in 1..8192)
                val body = ByteArray(n); inp.readFully(body)
                return JSONObject(String(body, Charsets.UTF_8))
            }
            val f = readMessage()
            assertEquals("frame", f.getString("t")); assertTrue(f.getLong("sent_ns") > 0)
            val req = "{\"v\":1,\"t\":\"sync\",\"sid\":\"$sid\",\"n\":9,\"t1\":77}".toByteArray()
            first.getOutputStream().write(byteArrayOf(0, 0, 0, req.size.toByte())); first.getOutputStream().write(req)
            val r = readMessage()
            assertEquals("sync_r", r.getString("t")); assertEquals(9L, r.getLong("n"))
            first.close()                                                   // the server drops the connection
            val before = System.currentTimeMillis()
            publishFrame(slot, 1)                                           // may be lost on the dead stream: newest wins
            val second = listener.accept()
            report("tcp_reconnect_ms", "${System.currentTimeMillis() - before}")
            second.close()
            assertTrue("reconnected", sender.reconnects >= 2)
        } finally {
            sender.stop(); listener.close(); slot.close()
        }
    }
}

private fun java.io.InputStream.readFully(b: ByteArray) {
    var o = 0
    while (o < b.size) {
        val n = read(b, o, b.size - o)
        if (n < 0) throw java.io.EOFException()
        o += n
    }
}
