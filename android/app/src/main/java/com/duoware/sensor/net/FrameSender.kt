package com.duoware.sensor.net

import android.os.Process
import android.util.Log
import com.duoware.sensor.pipeline.SendSlot
import com.duoware.sensor.proto.FrameWriter
import com.duoware.sensor.proto.FramingException
import com.duoware.sensor.proto.TcpDeframer
import com.duoware.sensor.proto.TcpFramer
import java.io.IOException
import java.net.InetSocketAddress
import java.net.StandardSocketOptions
import java.nio.ByteBuffer
import java.nio.channels.DatagramChannel
import java.nio.channels.SocketChannel

/**
 * The `sender` and `sync` threads (M2 plan "Threads and the hot path", step 9). The sender takes the newest ready frame
 * from the [SendSlot], stamps `sent_ns`, writes it, and reports it to [onSent]. The sync thread blocks on the read side
 * of the same channel and answers `sync` at once ([SyncResponder]); its writes share the channel with the frames.
 *
 * UDP (PROTOCOL.md §2): one connected `DatagramChannel`, one datagram per message. TCP (§2.1, `wired_adb`): a
 * `SocketChannel` to the loopback port, 4-byte length header + body written as one gathering write, reconnect after
 * 1 s while the session lives. While there is no connection the slot keeps replacing its frame (counted as dropped).
 */
class FrameSender(
    private val slot: SendSlot,
    private val clock: () -> Long,
    private val onSent: (sentNs: Long, readyNs: Long, capNs: Long) -> Unit,
) {
    class Target(
        val host: String, val port: Int, val tcp: Boolean, val binder: NetworkBinder, val cam: Int, val sid: String,
    )

    @Volatile private var running = false
    private var sender: Thread? = null

    @Volatile var writeErrors = 0L
        private set
    @Volatile var reconnects = 0L
        private set
    @Volatile var connected = false
        private set

    /** One live connection: the channel plus the buffers of its two threads (preallocated, reused). */
    private class Conn(val udp: DatagramChannel?, val tcp: SocketChannel?) {
        val header = ByteBuffer.allocateDirect(TcpFramer.HEADER_BYTES)
        val headerBytes = ByteArray(TcpFramer.HEADER_BYTES)
        val gather = arrayOfNulls<ByteBuffer>(2)
        val writeLock = Any()
        @Volatile var dead = false

        fun close() {
            dead = true
            try { udp?.close() } catch (_: IOException) {}
            try { tcp?.close() } catch (_: IOException) {}
        }
    }

    @Synchronized
    fun start(t: Target) {
        if (running) return
        running = true
        sender = Thread({
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY)
            loop(t)
        }, "sender").also { it.start() }
    }

    @Synchronized
    fun stop() {
        running = false
        sender?.join(JOIN_MS)
        sender = null
        connected = false
    }

    private fun loop(t: Target) {
        var conn: Conn? = null
        try {
            while (running) {
                if (conn == null || conn.dead) {
                    conn?.close()
                    connected = false
                    conn = connect(t)
                    if (conn == null) {
                        sleepWhileRunning(RECONNECT_MS)
                        continue
                    }
                    connected = true
                }
                val i = slot.take(TAKE_MS)
                if (i < 0) continue
                try {
                    val sentNs = clock()
                    FrameWriter.stampSent(slot.buffer(i), slot.sentOffset(i), sentNs)
                    val bb = slot.wrapper(i)
                    bb.clear(); bb.limit(slot.length(i))
                    write(conn, bb, t.tcp)
                    onSent(sentNs, slot.readyNs(i), slot.capNs(i))
                } catch (e: IOException) {
                    writeErrors++
                    if (t.tcp) conn.dead = true            // a byte stream cannot be resynchronised: reconnect
                } finally {
                    slot.release(i)
                }
            }
        } finally {
            conn?.close()
            connected = false
        }
    }

    private fun write(c: Conn, body: ByteBuffer, tcp: Boolean) {
        if (!tcp) {
            c.udp!!.write(body)
            return
        }
        synchronized(c.writeLock) {
            TcpFramer.writeHeader(c.headerBytes, 0, body.remaining())
            c.header.clear(); c.header.put(c.headerBytes).flip()
            c.gather[0] = c.header; c.gather[1] = body
            val ch = c.tcp!!
            while (c.gather[1]!!.hasRemaining()) ch.write(c.gather)
        }
    }

    private fun connect(t: Target): Conn? = try {
        val addr = InetSocketAddress(t.host, t.port)
        val conn: Conn
        if (t.tcp) {
            val ch = SocketChannel.open()
            try {
                t.binder.bind(ch.socket())
                ch.setOption(StandardSocketOptions.TCP_NODELAY, true)
                ch.setOption(StandardSocketOptions.SO_SNDBUF, TCP_SNDBUF)
                ch.socket().connect(addr, CONNECT_MS)
            } catch (e: IOException) {
                ch.close(); throw e
            }
            conn = Conn(null, ch)
        } else {
            val ch = DatagramChannel.open()
            try {
                t.binder.bind(ch.socket())
                ch.connect(addr)
            } catch (e: IOException) {
                ch.close(); throw e
            }
            conn = Conn(ch, null)
        }
        reconnects++
        val responder = SyncResponder(t.cam, t.sid, clock)
        Thread({ syncLoop(conn, responder, t.tcp) }, "sync").also { it.isDaemon = true; it.start() }
        conn
    } catch (e: IOException) {
        Log.w(TAG, "connect ${t.host}:${t.port} failed: ${e.message}")
        null
    }

    private fun syncLoop(c: Conn, responder: SyncResponder, tcp: Boolean) {
        val inBuf = ByteArray(TcpFramer.MAX_MESSAGE)
        val inWrap = ByteBuffer.wrap(inBuf)
        val out = ByteArray(OUT_BYTES)
        val outWrap = ByteBuffer.wrap(out)
        val deframer = TcpDeframer()
        try {
            while (!c.dead && running) {
                inWrap.clear()
                val n = if (tcp) c.tcp!!.read(inWrap) else c.udp!!.read(inWrap)
                val t2 = clock()                                     // as early as possible after the read
                if (n < 0) break
                if (tcp) {
                    deframer.feed(inBuf, 0, n) { b, o, l -> answer(c, responder, b, o, l, t2, out, outWrap, true) }
                } else {
                    answer(c, responder, inBuf, 0, n, t2, out, outWrap, false)
                }
            }
        } catch (e: IOException) {
            // closed or reset: the sender notices `dead` and reconnects
        } catch (e: FramingException) {
            Log.w(TAG, "bad framing from the server: ${e.message}")
        } finally {
            c.dead = true
        }
    }

    private fun answer(
        c: Conn, r: SyncResponder, b: ByteArray, o: Int, l: Int, t2: Long, out: ByteArray, outWrap: ByteBuffer, tcp: Boolean,
    ) {
        if (tcp) {
            synchronized(c.writeLock) {                              // t3 is stamped after taking the write lock
                val len = r.reply(b, o, l, t2, out)
                if (len < 0) return
                outWrap.clear(); outWrap.limit(len)
                TcpFramer.writeHeader(c.headerBytes, 0, len)
                c.header.clear(); c.header.put(c.headerBytes).flip()
                c.gather[0] = c.header; c.gather[1] = outWrap
                while (outWrap.hasRemaining()) c.tcp!!.write(c.gather)
            }
        } else {
            val len = r.reply(b, o, l, t2, out)
            if (len < 0) return
            outWrap.clear(); outWrap.limit(len)
            c.udp!!.write(outWrap)
        }
    }

    private fun sleepWhileRunning(ms: Long) {
        var left = ms
        while (running && left > 0) {
            Thread.sleep(minOf(left, 100L)); left -= 100L
        }
    }

    companion object {
        private const val TAG = "FrameSender"
        private const val TAKE_MS = 50L
        private const val RECONNECT_MS = 1000L             // PROTOCOL.md §2.1
        private const val CONNECT_MS = 2000
        private const val TCP_SNDBUF = 16 * 1024
        private const val OUT_BYTES = 256
        private const val JOIN_MS = 2000L
    }
}
