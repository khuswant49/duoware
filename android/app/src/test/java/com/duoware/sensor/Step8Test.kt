package com.duoware.sensor

import com.duoware.sensor.camera.CaptureMeta
import com.duoware.sensor.camera.Sizes
import com.duoware.sensor.pipeline.ImageMailbox
import com.duoware.sensor.pipeline.SendSlot
import com.duoware.sensor.proto.YuvSize
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** M2 step 8, pure parts: capture metadata ring, image mailbox, send slot, size and frame-rate choice. */
class Step8Test {
    // ------------------------------------------------------------------------------------------ CaptureMeta

    @Test
    fun metaFindsByTimestampAndFallsBackToTheLatestEntry() {
        val m = CaptureMeta(4)
        assertEquals("nothing recorded: the fallback", 3_000_000L, m.exposureNs(1, 3_000_000L))
        m.record(100, 3_000_000, 18_000_000, 16_666_667)
        m.record(200, 3_100_000, 18_500_000, 16_666_667)
        assertEquals(3_000_000L, m.exposureNs(100, 0))
        assertEquals(18_500_000L, m.skewNs(200))
        val before = m.misses
        assertEquals("unknown timestamp: the latest exposure", 3_100_000L, m.exposureNs(300, 0))
        assertEquals(before + 1, m.misses)
        assertEquals("unknown timestamp: the latest skew", 18_500_000L, m.skewNs(300))
        assertEquals(16_666_667L, m.frameDurationNs(100))
    }

    @Test
    fun metaRingForgetsTheOldestEntries() {
        val m = CaptureMeta(3)
        for (i in 1..5) m.record(i * 10L, i * 1000L, i.toLong(), 1)
        assertEquals(5000L, m.exposureNs(50, 0))
        assertEquals(3000L, m.exposureNs(30, 0))
        val misses = m.misses
        m.exposureNs(10, 0)                                  // dropped out of the ring
        assertEquals(misses + 1, m.misses)
    }

    // ------------------------------------------------------------------------------------------ ImageMailbox

    private class Img(val id: Int) : AutoCloseable {
        var closed = false
        override fun close() { closed = true }
    }

    @Test
    fun mailboxKeepsTheNewestImageAndClosesAndCountsTheOlderOne() {
        val box = ImageMailbox<Img>()
        val a = Img(1); val b = Img(2); val c = Img(3)
        box.put(a, 10); box.put(b, 20)
        assertTrue(a.closed); assertFalse(b.closed)
        assertEquals(1L, box.skipped)
        box.put(c, 30)
        assertTrue(b.closed)
        assertEquals(2L, box.skipped)
        val got = box.take()
        assertEquals(3, got?.id)
        assertEquals(30L, box.availNs)
        box.addSkipped(5)
        assertEquals(7L, box.skipped)
    }

    @Test
    fun mailboxTakeBlocksUntilAnImageArrivesAndCloseWakesIt() {
        val box = ImageMailbox<Img>()
        val got = arrayOfNulls<Img>(1)
        val done = CountDownLatch(1)
        val t = Thread { got[0] = box.take(); done.countDown() }
        t.start()
        Thread.sleep(50)
        assertEquals("still waiting", 1L, done.count)
        box.put(Img(9), 1)
        assertTrue(done.await(2, TimeUnit.SECONDS))
        assertEquals(9, got[0]?.id)
        val t2 = Thread { got[0] = box.take(); done.countDown() }
        val done2 = CountDownLatch(1)
        val t3 = Thread { assertNull(box.take()); done2.countDown() }
        t3.start()
        Thread.sleep(50)
        box.close()
        assertTrue(done2.await(2, TimeUnit.SECONDS))
        val late = Img(5)
        box.put(late, 2)
        assertTrue("a closed mailbox closes what it is given", late.closed)
        box.reopen()
        val ok = Img(6)
        box.put(ok, 3)
        assertFalse(ok.closed)
        t2.interrupt()
    }

    // ------------------------------------------------------------------------------------------ SendSlot

    @Test
    fun slotDropsAReadyFrameThatIsReplacedBeforeItWasSent() {
        val s = SendSlot(3, 64)
        val a = s.acquire(); s.buffer(a)[0] = 1
        s.publish(a, 10, 2, 100, 50)
        val b = s.acquire(); s.buffer(b)[0] = 2
        s.publish(b, 11, 3, 200, 150)                       // replaces a: a is dropped
        assertEquals(1L, s.dropped)
        val got = s.take(100)
        assertEquals(b, got)
        assertEquals(11, s.length(got)); assertEquals(3, s.sentOffset(got))
        assertEquals(200L, s.readyNs(got)); assertEquals(150L, s.capNs(got))
        assertEquals("nothing else is ready", -1, s.take(10))
        s.release(got)
        // the pool never runs out: one being filled, one ready, one being sent
        val x = s.acquire(); val y = s.acquire(); val z = s.acquire()
        assertEquals(setOf(0, 1, 2), setOf(x, y, z))
    }

    @Test
    fun slotSenderWaitsForAFrame() {
        val s = SendSlot(3, 64)
        val taken = IntArray(1) { -2 }
        val done = CountDownLatch(1)
        Thread { taken[0] = s.take(2000); done.countDown() }.start()
        Thread.sleep(50)
        val i = s.acquire()
        s.publish(i, 5, 0, 0, 0)
        assertTrue(done.await(2, TimeUnit.SECONDS))
        assertEquals(i, taken[0])
        s.close()
        assertEquals(-1, s.take(1000))
    }

    // ------------------------------------------------------------------------------------------ Sizes

    private val offered = listOf(
        YuvSize(1920, 1080, 30.0), YuvSize(1280, 720, 60.0), YuvSize(960, 540, 60.0), YuvSize(640, 480, 120.0),
        YuvSize(1600, 1200, 30.0),
    )

    @Test
    fun sizePickKeepsAnOfferedSizeAndOtherwiseTheClosestOfTheSameAspectRatio() {
        assertEquals(1280 to 720, Sizes.pick(offered, 1280 to 720))
        assertEquals("16:9 request, no 1000x562: 960x540 is closest in pixels among 16:9", 960 to 540, Sizes.pick(offered, 1000 to 562))
        assertEquals(1920 to 1080, Sizes.pick(offered, 2560 to 1440))
        assertEquals("4:3 stays 4:3", 640 to 480, Sizes.pick(offered, 800 to 600))
        assertEquals("no 21:9 offered: the closest size overall", 960 to 540, Sizes.pick(offered, 1280 to 548))
        assertNull(Sizes.pick(emptyList(), 1280 to 720))
    }

    @Test
    fun effectiveFpsIsTheSizeMaximumForZeroAndClampedAbove() {
        assertEquals(60.0, Sizes.effectiveFps(offered, 1280 to 720, 0.0)!!, 0.0)
        assertEquals(30.0, Sizes.effectiveFps(offered, 1280 to 720, 30.0)!!, 0.0)
        assertEquals(60.0, Sizes.effectiveFps(offered, 1280 to 720, 90.0)!!, 0.0)
        assertNull(Sizes.effectiveFps(offered, 111 to 222, 30.0))
        assertEquals(16_666_667L, Sizes.frameDurationNs(60.0))
        assertEquals(33_333_333L, Sizes.frameDurationNs(30.0))
    }
}
