package com.duoware.sensor

import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.duoware.sensor.pipeline.NativeBridge
import com.duoware.sensor.pipeline.NativeTestHooks
import com.duoware.sensor.pipeline.ResultBuffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.nio.ByteBuffer

/** M2 step 7 / H1: region-of-interest tracking (DECISIONS.md D32) through the real native pipeline. */
@RunWith(AndroidJUnit4::class)
class RoiTrackerTest {
    private var handle = 0L
    private val result = ResultBuffer()
    private val frame: ByteBuffer = ByteBuffer.allocateDirect(W * H)
    private var t = 0L

    @Before
    fun setUp() {
        handle = NativeBridge.nativeCreate(W, H)
    }

    @After
    fun tearDown() = NativeBridge.nativeDestroy(handle)

    private fun configure(track: IntArray, fullEvery: Int = 10, demote: Int = 3) =
        NativeBridge.nativeConfigure(handle, track, fullEvery, 1.5f, 64, 0, demote, 1, false)

    /** One frame at 60 fps with markers [spec] (n x [id, cx, cy, side, angle]); returns the IDs found. */
    private fun step(spec: FloatArray): List<Int> {
        NativeTestHooks.renderScene(spec, 1, 0f, 0f, frame, W, H, W)
        t += FRAME_NS
        val n = NativeBridge.nativeProcess(handle, frame, W, W, H, t, false, 0, result.buffer, null)
        return (0 until n).map { result.markerId(it) }
    }

    private fun searched(): List<Int> = (0 until result.searchedCount).map { result.searched(it) }

    @Test
    fun aTagMovingAt300PxPerSecondStaysInItsWindow() {
        configure(intArrayOf(1))
        var lostCount = 0
        var roiFrames = 0
        for (i in 0 until 300) {
            val x = 100f + pingPong(5 * i, 1000)                        // 300 px/s at 60 fps, back and forth in the image
            val ids = step(floatArrayOf(1f, x, 360f, 44f, 0f, 10f, 640f, 600f, 48f, 0f))
            assertTrue("frame $i: tag 1 found", 1 in ids)
            if (result.lost) lostCount++
            if (!result.isFull) {
                roiFrames++
                assertEquals(listOf(1), searched())
                assertFalse("floor tag 10 is outside the window", 10 in ids)
            }
        }
        Log.i(TAG, "moving tag: $roiFrames roi frames of 300, $lostCount lost")
        assertEquals(0, lostCount)
        assertTrue("most frames are roi frames ($roiFrames)", roiFrames > 200)
    }

    @Test
    fun aVanishedTagForcesAFullScan() {
        configure(intArrayOf(1))
        val with = floatArrayOf(1f, 400f, 300f, 44f, 0f)
        repeat(3) { step(with) }                                        // full scan, then roi frames
        assertFalse(result.isFull)
        step(floatArrayOf(10f, 900f, 300f, 48f, 0f))                    // tag 1 gone
        assertTrue("the roi frame that missed it reports lost", result.lost)
        step(floatArrayOf(10f, 900f, 300f, 48f, 0f))
        assertTrue("the next frame is a full scan", result.isFull)
    }

    @Test
    fun windowsAtTheImageEdgeAreClipped() {
        configure(intArrayOf(1))
        for (i in 0 until 30) {
            val ids = step(floatArrayOf(1f, 26f + (i % 3), 24f, 40f, 0f))  // the window reaches past the corner
            assertTrue(ids.all { it == 1 })
        }
    }

    @Test
    fun overlappingWindowsReportATagOnce() {
        configure(intArrayOf(1, 5))
        for (i in 0 until 40) {
            val ids = step(floatArrayOf(1f, 500f + i, 360f, 44f, 0f, 5f, 580f + i, 360f, 44f, 0f))
            assertEquals("frame $i: $ids", ids.toSet().size, ids.size)
            assertEquals(setOf(1, 5), ids.toSet())
        }
    }

    @Test
    fun anUntrackedTagThatMovesIsTrackedAndDemotedWhenStill() {
        configure(IntArray(0), fullEvery = 5, demote = 2)
        var x = 200f
        var tracked = false
        for (i in 0 until 30) {                                         // moves 60 px per frame between full scans
            step(floatArrayOf(7f, x, 360f, 44f, 0f))
            if (!result.isFull && 7 in searched()) tracked = true
            x += 12f
        }
        assertTrue("a moving untracked tag is promoted", tracked)
        var stillRoiWithTag = 0
        for (i in 0 until 40) {                                         // now still: demoted after 2 still full scans
            step(floatArrayOf(7f, x, 360f, 44f, 0f))
            if (i >= 20 && !result.isFull && 7 in searched()) stillRoiWithTag++
        }
        assertEquals("demoted after demote_after_scans still scans", 0, stillRoiWithTag)
    }

    /** Triangle wave: 0 .. `span` .. 0 ... as `d` grows (the tag turns round before it leaves the image). */
    private fun pingPong(d: Int, span: Int): Float {
        val m = d % (2 * span)
        return (if (m <= span) m else 2 * span - m).toFloat()
    }

    companion object {
        const val TAG = "DuoRoi"
        const val W = 1280
        const val H = 720
        const val FRAME_NS = 16_666_667L
    }
}
