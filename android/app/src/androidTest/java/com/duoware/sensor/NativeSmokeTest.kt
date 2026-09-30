package com.duoware.sensor

import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.duoware.sensor.pipeline.NativeBridge
import com.duoware.sensor.pipeline.NativeTestHooks
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** M2 step 2 / H1: the native library loads on the phone and OpenCV's ArUco detector works there. */
@RunWith(AndroidJUnit4::class)
class NativeSmokeTest {
    @Test
    fun openCvLoadsAndReportsItsVersionThreadsAndParallelFramework() {
        val info = NativeBridge.nativeVersion()
        // The owner reports this line from logcat (tag DuoSmoke) after the instrumented run.
        Log.i(TAG, info)
        println("$TAG: $info")
        assertTrue("unexpected version line: $info", info.startsWith("OpenCV 4."))
        assertTrue("missing the parallel framework line: $info", info.contains("Parallel framework:"))
    }

    @Test
    fun detectsOneGeneratedMarker() {
        val ids = NativeTestHooks.detectGeneratedMarker(markerId = 7, sidePx = 120)
        Log.i(TAG, "generated marker 7, detected ids ${ids.toList()}")
        assertEquals(listOf(7), ids.toList())
    }

    private companion object {
        const val TAG = "DuoSmoke"
    }
}
