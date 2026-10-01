package com.duoware.sensor

import android.Manifest
import android.media.Image
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.duoware.sensor.camera.CameraController
import com.duoware.sensor.camera.Capabilities
import com.duoware.sensor.camera.CaptureMeta
import com.duoware.sensor.clock.ClockSource
import com.duoware.sensor.pipeline.FramePipeline
import com.duoware.sensor.pipeline.ImageMailbox
import com.duoware.sensor.pipeline.PipelineRunner
import com.duoware.sensor.pipeline.SendSlot
import com.duoware.sensor.proto.Messages
import com.duoware.sensor.proto.Tracking
import com.duoware.sensor.stats.StageStats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * M2 step 8 / H2: the real camera of the phone. Needs the screen on and unlocked. Reports capabilities, the sync clock
 * decision, the achieved frame rate, exposure, skew and what `processing_on` says. Detects no markers (nothing in view
 * is required); it checks the stream, not the scene.
 */
@RunWith(AndroidJUnit4::class)
class CameraControllerTest {
    private val ctx get() = InstrumentationRegistry.getInstrumentation().targetContext

    private fun report(key: String, value: String) {
        Log.i(TAG, "$key: $value")
        InstrumentationRegistry.getInstrumentation().sendStatus(0, Bundle().apply { putString(key, value) })
    }

    @Test
    fun capabilitiesOfEveryBackCameraAreReadable() {
        val choices = Capabilities.choices(ctx)
        assertTrue("the phone lists a camera", choices.isNotEmpty())
        for (c in choices) {
            val caps = Capabilities.build(ctx, c.id)
            val json = Messages.hello(com.duoware.sensor.proto.Hello("d", null, null, "0", "m", "a", 0,
                com.duoware.sensor.proto.LinkInfo("wireless", "wlan0", "1.2.3.4", null), com.duoware.sensor.proto.CpuInfo(8, listOf(1L)), caps))
            report("camera_${c.id}", "${c.label}; level ${caps.hwLevel}; caps ${caps.capabilities}; ts ${caps.tsSource}; " +
                "exposure ${caps.exposureNsRange}; iso ${caps.isoRange}; fps ${caps.fpsRanges}; skew ${caps.rollingShutterSkew}; " +
                "focus ${caps.minFocusDiopters}; intrinsics ${caps.intrinsics}; distortion ${caps.distortion}; array ${caps.activeArray}")
            report("camera_${c.id}_yuv", caps.yuvSizes.joinToString { "${it.w}x${it.h}@${String.format(Locale.ROOT, "%.1f", it.maxFps)}" })
            assertTrue("hello is valid JSON of reasonable size", json.length in 200..6000)
            assertTrue("YUV sizes listed", caps.yuvSizes.isNotEmpty())
        }
        assertNotNull(Capabilities.defaultChoice(choices, null))
    }

    @Test
    fun streamsFramesAtTheMaximumRateWithAManualExposure() {
        // The realme refuses the runtime grant to the test runner (SecurityException from UiAutomation), so the CAMERA
        // permission has to be granted once by hand (the app's own permission screen, step 10).
        assumeTrue("CAMERA is not granted to com.duoware.sensor: grant it in the app's settings and rerun",
            ctx.checkSelfPermission(Manifest.permission.CAMERA) == android.content.pm.PackageManager.PERMISSION_GRANTED)
        val choice = Capabilities.defaultChoice(Capabilities.choices(ctx), null)!!
        val clock = ClockSource({ SystemClock.elapsedRealtimeNanos() }, { System.nanoTime() })
        val meta = CaptureMeta()
        val mailbox = ImageMailbox<Image>()
        val slot = SendSlot()
        val pipeline = FramePipeline({ clock.now() }, slot, meta)
        pipeline.startSession(1, "0123456789abcdef")
        pipeline.configure(Tracking(listOf(1, 5), 10, 1.5, 64, 0, 3, "subpix", false))
        val runner = PipelineRunner(mailbox, pipeline)
        val drain = java.util.concurrent.atomic.AtomicBoolean(true)
        val drainer = Thread {                                // a stand-in for the sender: takes and discards frames
            while (drain.get()) {
                val i = slot.take(50)
                if (i < 0) continue
                val sentNs = clock.now()
                pipeline.onSent(sentNs, slot.readyNs(i), slot.capNs(i))
                slot.release(i)
            }
        }.also { it.start() }
        val ready = CountDownLatch(1)
        var error: String? = null
        var active: CameraController.Active? = null
        val controller = CameraController(ctx, clock, meta, mailbox, object : CameraController.Listener {
            override fun onReady(active0: CameraController.Active) { active = active0; ready.countDown() }
            override fun onError(message: String) { error = message; ready.countDown() }
        })
        try {
            runner.start()
            controller.start(choice.id, 1280 to 720, 0.0, 3_000_000L, 800, null)
            assertTrue("camera ready within 15 s (error: $error)", ready.await(15, TimeUnit.SECONDS))
            assertEquals(null, error)
            val a = active!!
            pipeline.fallbackExposureNs = a.exposureNs
            val f0 = pipeline.frames
            val r0 = mailbox.received
            val t0 = System.nanoTime()
            Thread.sleep(MEASURE_MS)
            val frames = pipeline.frames - f0
            val seconds = (System.nanoTime() - t0) / 1e9
            val fps = frames / seconds
            val deliveredFps = (mailbox.received - r0) / seconds
            val out = FloatArray(2)
            val nowMs = clock.now() / 1_000_000
            val line = StringBuilder()
            for (s in StageStats.Stage.entries) {
                line.append(if (pipeline.stats.get(s, nowMs, out)) String.format(Locale.ROOT, "%s %.2f/%.2f; ", s.wire, out[0], out[1]) else "${s.wire} none; ")
            }
            report("camera", "${choice.label}; size ${a.size}; target ${"%.1f".format(Locale.ROOT, a.fps)} fps; achieved ${"%.1f".format(Locale.ROOT, fps)} fps (camera delivered ${"%.1f".format(Locale.ROOT, deliveredFps)} fps) " +
                "($frames frames in ${"%.1f".format(Locale.ROOT, seconds)} s); skipped ${mailbox.skipped}; meta misses ${meta.misses}")
            report("exposure", "requested 3.000 ms, result ${"%.3f".format(Locale.ROOT, controller.lastExposureNs / 1e6)} ms; iso ${a.iso}; manual ${a.manualSensor}")
            report("focus_awb", "focus ${a.focus}; awb locked ${a.awbLocked}; processing_on ${a.processingOn}")
            report("clock", "${a.clockName} (requested frame duration ${a.frameDurationNs} ns; the capture results report ${meta.frameDurationNs(0)} ns)")
            report("stages_ms_p50_p95", line.toString())
            assertTrue("sync clock decided (was ${a.clockName})", a.clockName != "unknown")
            assertTrue("frames flow: $fps fps", fps >= MIN_FPS)
            assertTrue("manual exposure applied: ${controller.lastExposureNs}", !a.manualSensor || Math.abs(controller.lastExposureNs - 3_000_000L) < 500_000L)
        } finally {
            controller.release()
            runner.stop()
            drain.set(false)
            drainer.join(1000)
            slot.close()
            pipeline.close()
        }
    }

    companion object {
        const val TAG = "DuoCameraTest"
        const val MEASURE_MS = 5000L
        const val MIN_FPS = 15.0              // a loose floor: the aim is to see that frames flow, H5 measures the rate
    }
}
