package com.duoware.sensor

import android.os.Bundle
import android.os.Debug
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.duoware.sensor.camera.CaptureMeta
import com.duoware.sensor.pipeline.FramePipeline
import com.duoware.sensor.pipeline.NativeTestHooks
import com.duoware.sensor.pipeline.SendSlot
import com.duoware.sensor.proto.FrameWriter
import com.duoware.sensor.proto.Tracking
import com.duoware.sensor.stats.StageStats
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.nio.ByteBuffer
import java.nio.channels.DatagramChannel
import java.util.Locale

/**
 * M2 step 8 / H1: the frame pipeline (`processFrame`) on synthetic frames with a real sender writing to a local UDP
 * socket: the frames are valid PROTOCOL.md section 2 JSON, and the allocation check of the plan (at most 64 KB
 * allocated by our pipeline over 1000 frames).
 */
@RunWith(AndroidJUnit4::class)
class FramePipelineTest {
    private fun report(key: String, value: String) {
        Log.i(TAG, "$key: $value")
        InstrumentationRegistry.getInstrumentation().sendStatus(0, Bundle().apply { putString(key, value) })
    }

    private fun tracking() = Tracking(listOf(1, 5), 10, 1.5, 64, 0, 3, "subpix", false)

    private class Rig(val receiver: DatagramSocket) {
        val slot = SendSlot()
        val meta = CaptureMeta()
        val pipeline = FramePipeline({ System.nanoTime() }, slot, meta)
        @Volatile var running = true
        @Volatile var sent = 0L
        private val channel = DatagramChannel.open().also { it.connect(InetSocketAddress("127.0.0.1", receiver.localPort)) }
        val sender = Thread {
            while (running) {
                val i = slot.take(50)
                if (i < 0) continue
                val sentNs = System.nanoTime()
                FrameWriter.stampSent(slot.buffer(i), slot.sentOffset(i), sentNs)
                val bb = slot.wrapper(i)
                bb.clear(); bb.limit(slot.length(i))
                channel.write(bb)
                pipeline.onSent(sentNs, slot.readyNs(i), slot.capNs(i))
                slot.release(i)
                sent++
            }
        }.also { it.start() }

        fun close() {
            running = false
            sender.join(2000)
            channel.close()
            pipeline.close()
        }
    }

    @Test
    fun framesAreValidProtocolJsonAndAllocationStaysUnderTheLimit() {
        val frame = ByteBuffer.allocateDirect(W * H)
        NativeTestHooks.renderScene(SCENE, 1, 0f, 0f, frame, W, H, W)
        val receiver = DatagramSocket(0, java.net.InetAddress.getByName("127.0.0.1")).also { it.soTimeout = 2000 }
        val rig = Rig(receiver)
        try {
            val p = rig.pipeline
            p.startSession(1, "0123456789abcdef")
            p.configure(tracking())
            var cap = 1_000_000_000L
            val fullIntervalNs = 16_666_667L
            fun frameOnce() {
                cap += fullIntervalNs
                p.processFrame(frame, W, W, H, cap, cap + 20_000_000L)
            }
            // one frame on the wire, parsed as a PROTOCOL.md section 2 message
            frameOnce()
            val pkt = java.net.DatagramPacket(ByteArray(8192), 8192)
            receiver.receive(pkt)
            val json = JSONObject(String(pkt.data, 0, pkt.length, Charsets.UTF_8))
            assertEquals(1, json.getInt("v")); assertEquals("frame", json.getString("t")); assertEquals(1, json.getInt("cam"))
            assertEquals("0123456789abcdef", json.getString("sid")); assertEquals(0, json.getInt("seq"))
            assertEquals("full", json.getString("scan")); assertEquals(W, json.getInt("w")); assertEquals(H, json.getInt("h"))
            assertEquals(11, json.getJSONArray("m").length())
            assertTrue("sent_ns stamped", json.getLong("sent_ns") > 0)
            report("frame_bytes", "${pkt.length} (11 markers)")

            repeat(300) { frameOnce() }                                       // warm-up: JIT, class loading, thread-local buffers
            val stat = Debug.getRuntimeStat("art.gc.bytes-allocated")
            assumeTrue("art.gc.bytes-allocated is not available on this device", !stat.isNullOrEmpty())
            val before = stat!!.toLong()
            val framesBefore = p.frames
            repeat(1000) { frameOnce() }
            val allocated = Debug.getRuntimeStat("art.gc.bytes-allocated").toLong() - before
            report("allocated_bytes_1000_frames", "$allocated (limit 65536); frames ${p.frames - framesBefore}; slot dropped ${rig.slot.dropped}; sent ${rig.sent}")
            val out = FloatArray(2)
            val now = System.nanoTime() / 1_000_000
            val line = StringBuilder()
            for (s in StageStats.Stage.entries) {
                line.append(if (p.stats.get(s, now, out)) String.format(Locale.ROOT, "%s p50 %.2f p95 %.2f; ", s.wire, out[0], out[1]) else "${s.wire} none; ")
            }
            report("stage_timings_ms", line.toString())
            assertTrue("allocated $allocated bytes over 1000 frames", allocated <= ALLOC_LIMIT)
        } finally {
            rig.close()
            receiver.close()
        }
    }

    @Test
    fun seqCountsAnalysedFramesAndRoiFramesNameTheirWindows() {
        val frame = ByteBuffer.allocateDirect(W * H)
        NativeTestHooks.renderScene(SCENE, 1, 0f, 0f, frame, W, H, W)
        val receiver = DatagramSocket(0, java.net.InetAddress.getByName("127.0.0.1")).also { it.soTimeout = 2000 }
        val rig = Rig(receiver)
        try {
            rig.pipeline.startSession(2, "fedcba9876543210")
            rig.pipeline.configure(tracking())
            val scans = ArrayList<String>()
            val seqs = ArrayList<Long>()
            var cap = 5_000_000_000L
            for (i in 0 until 12) {
                cap += 16_666_667L
                rig.pipeline.processFrame(frame, W, W, H, cap, cap + 15_000_000L)
                val pkt = java.net.DatagramPacket(ByteArray(8192), 8192)
                receiver.receive(pkt)
                val j = JSONObject(String(pkt.data, 0, pkt.length, Charsets.UTF_8))
                scans += j.getString("scan"); seqs += j.getLong("seq")
                if (j.getString("scan") == "roi") {
                    val searched = j.getJSONArray("searched")
                    assertEquals("both car tags are searched", setOf(1, 5), (0 until searched.length()).map { searched.getInt(it) }.toSet())
                    assertEquals("a roi frame holds only the tracked tags", 2, j.getJSONArray("m").length())
                }
            }
            assertEquals((0L until 12L).toList(), seqs)
            assertEquals("full", scans[0]); assertEquals("roi", scans[1])
            assertEquals("full again at full_scan_every", "full", scans[10])
        } finally {
            rig.close()
            receiver.close()
        }
    }

    companion object {
        const val TAG = "DuoPipeline"
        const val W = 1280
        const val H = 720
        const val ALLOC_LIMIT = 64 * 1024L                   // M2 plan step 8
        val SCENE = NativeDetectorTest.SCENE
    }
}
