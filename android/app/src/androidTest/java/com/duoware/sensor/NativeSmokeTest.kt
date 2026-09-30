package com.duoware.sensor

import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.duoware.sensor.pipeline.NativeBridge
import com.duoware.sensor.pipeline.NativeTestHooks
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** M2 step 2 / H1: the native library loads on the phone and OpenCV's ArUco detector works there. */
@RunWith(AndroidJUnit4::class)
class NativeSmokeTest {
    /** Puts a line into the `am instrument -r` output (`INSTRUMENTATION_STATUS: <key>=<value>`) and into logcat. */
    private fun report(key: String, value: String) {
        Log.i(TAG, "$key: $value")
        val b = Bundle().apply { putString(key, value) }
        InstrumentationRegistry.getInstrumentation().sendStatus(0, b)
    }

    @Test
    fun openCvLoadsAndReportsItsVersionThreadsAndParallelFramework() {
        val info = NativeBridge.nativeVersion()
        report("device", "${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT}), " +
            "ABIs ${Build.SUPPORTED_ABIS.joinToString()}")
        report("opencv", info)
        assertTrue("unexpected version line: $info", info.startsWith("OpenCV 4."))
        assertTrue("missing the parallel framework line: $info", info.contains("Parallel framework:"))
    }

    @Test
    fun detectsOneGeneratedMarker() {
        NativeTestHooks.detectGeneratedMarker(markerId = 7, sidePx = 120)      // warm-up (first OpenCV calls are slow)
        val t0 = System.nanoTime()
        val ids = NativeTestHooks.detectGeneratedMarker(markerId = 7, sidePx = 120)
        val ms = (System.nanoTime() - t0) / 1e6
        report("generated_marker_7", "detected ids ${ids.toList()} in %.2f ms (generate + detect, 240 x 240 px frame)".format(ms))
        assertEquals(listOf(7), ids.toList())
    }

    private companion object {
        const val TAG = "DuoSmoke"
    }
}
