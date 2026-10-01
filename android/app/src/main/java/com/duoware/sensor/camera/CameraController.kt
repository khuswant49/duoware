package com.duoware.sensor.camera

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Range
import android.view.Surface
import com.duoware.sensor.clock.ClockSource
import com.duoware.sensor.pipeline.ImageMailbox
import com.duoware.sensor.proto.YuvSize

/**
 * Camera2, driven directly (DECISIONS.md D30): one YUV `ImageReader` (maxImages 3), manual exposure / ISO / frame
 * duration, focus and white balance found once and then locked (D41), processing that changes the image turned off.
 * In `tracking` the session has only the reader surface; `setup` adds a small preview surface for aiming and [start]
 * is called again without it for `tracking` (a session reconfiguration).
 *
 * Threads: everything here runs on the `cam` HandlerThread. The image listener stamps `avail_ns`, closes nothing but a
 * replaced image (in the mailbox) and never detects or touches the network (M2 plan "Threads and the hot path").
 * Camera2 itself boxes some result values per frame: [CaptureResult.get] returns objects. That is the framework
 * allocation, kept to the keys the ring needs.
 */
class CameraController(
    private val ctx: Context,
    private val clock: ClockSource,
    private val meta: CaptureMeta,
    private val mailbox: ImageMailbox<Image>,
    private val listener: Listener,
) {
    interface Listener {
        /** The session streams and focus / white balance are settled (or gave up): [Active] is final until [start]. */
        fun onReady(active: Active)
        fun onError(message: String)
    }

    /** What the camera is doing now (for `status`, `hello` and the UI). */
    data class Active(
        val cameraId: String, val size: Pair<Int, Int>, val fps: Double, val frameDurationNs: Long,
        val exposureNs: Long, val iso: Int, val manualSensor: Boolean, val focus: String, val awbLocked: Boolean,
        val processingOn: List<String>, val clockName: String,
    )

    private val thread = HandlerThread("cam").also { it.start() }
    private val handler = Handler(thread.looper)
    private val manager = Capabilities.manager(ctx)

    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var reader: ImageReader? = null
    private var chars: CameraCharacteristics? = null
    private var preview: Surface? = null
    private var generation = 0                      // bumped by every start/stop so stale callbacks are ignored

    // requested / effective settings
    private var cameraId = ""
    private var size = 0 to 0
    private var fps = 0.0
    private var frameDurationNs = 0L
    private var exposureNs = 0L
    private var iso = 0
    private var manual = true
    private var offered: List<YuvSize> = emptyList()

    // focus / white balance
    private var focusMode = CaptureRequest.CONTROL_AF_MODE_AUTO
    private var focusDistance: Float? = null
    private var focusResult = "searching"           // searching | locked | failed (PROTOCOL.md section 4.5)
    private var afTriggered = false
    private var awbLock = false
    private var aeLock = false
    private var processingOn: List<String> = emptyList()

    @Volatile var active: Active? = null
        private set
    @Volatile var focus: String = "searching"
        private set

    // frame bookkeeping (cam thread)
    private var firstFrame = true
    private var lastTs = 0L
    private var logged = 0
    private var tsRealtime = false

    /** The actual exposure of the latest capture result. */
    @Volatile var lastExposureNs = 0L
        private set

    // -------------------------------------------------------------------------------------------- public API

    /** Opens [id] and streams at [resolution] (closest offered size), [fps] (0 = the sensor maximum), [exposureNs], [iso]. */
    @SuppressLint("MissingPermission")
    fun start(id: String, resolution: Pair<Int, Int>, fps: Double, exposureNs: Long, iso: Int, previewSurface: Surface?) {
        handler.post {
            val gen = ++generation
            closeCamera()
            try {
                val c = manager.getCameraCharacteristics(id)
                chars = c
                cameraId = id
                offered = Capabilities.yuvSizes(c)
                val picked = Sizes.pick(offered, resolution)
                if (picked == null) { listener.onError("camera $id offers no YUV_420_888 size"); return@post }
                size = picked
                val eff = Sizes.effectiveFps(offered, picked, fps)
                if (eff == null) { listener.onError("unknown frame rate for $picked"); return@post }
                this.fps = eff
                frameDurationNs = Sizes.frameDurationNs(eff)
                manual = Capabilities.hasManualSensor(c)
                this.exposureNs = exposureNs
                this.iso = iso
                preview = previewSurface
                tsRealtime = c.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE) ==
                    CameraMetadata.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME
                firstFrame = true; lastTs = 0; logged = 0
                afTriggered = false; awbLock = false; aeLock = false; focusDistance = null
                focusResult = "searching"; focus = focusResult
                processingOn = computeProcessingOn(c)
                mailbox.reopen()
                manager.openCamera(id, object : CameraDevice.StateCallback() {
                    override fun onOpened(camera: CameraDevice) {
                        if (gen != generation) { camera.close(); return }
                        device = camera
                        createSession(camera, gen)
                    }
                    override fun onDisconnected(camera: CameraDevice) {
                        camera.close()
                        if (gen == generation) listener.onError("camera disconnected")
                    }
                    override fun onError(camera: CameraDevice, error: Int) {
                        camera.close()
                        if (gen == generation) listener.onError("camera error $error")
                    }
                }, handler)
            } catch (e: SecurityException) {
                listener.onError("camera permission missing")
            } catch (e: Exception) {
                listener.onError("camera: ${e.message}")
            }
        }
    }

    /** New exposure / ISO / fps without reconfiguring the session (same size). */
    fun update(fps: Double, exposureNs: Long, iso: Int) {
        handler.post {
            this.fps = Sizes.effectiveFps(offered, size, fps) ?: this.fps
            frameDurationNs = Sizes.frameDurationNs(this.fps)
            this.exposureNs = exposureNs
            this.iso = iso
            applyRepeating()
            if (active != null) publish()
        }
    }

    /** Runs autofocus and white balance again (button, resolution change). */
    fun refocus() {
        handler.post { beginFocusAndWhiteBalance(generation) }
    }

    fun stop() {
        handler.post {
            generation++
            closeCamera()
            active = null
        }
    }

    fun release() {
        stop()
        handler.post { thread.quitSafely() }
    }

    // -------------------------------------------------------------------------------------------- session

    private fun createSession(camera: CameraDevice, gen: Int) {
        val r = ImageReader.newInstance(size.first, size.second, ImageFormat.YUV_420_888, MAX_IMAGES)
        reader = r
        r.setOnImageAvailableListener({ rd -> onImage(rd, gen) }, handler)
        val outputs = ArrayList<OutputConfiguration>(2)
        outputs.add(OutputConfiguration(r.surface))
        preview?.let { outputs.add(OutputConfiguration(it)) }
        val cfg = SessionConfiguration(SessionConfiguration.SESSION_REGULAR, outputs, { handler.post(it) },
            object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(s: CameraCaptureSession) {
                    if (gen != generation) { s.close(); return }
                    session = s
                    applyRepeating()
                    beginFocusAndWhiteBalance(gen)
                }
                override fun onConfigureFailed(s: CameraCaptureSession) {
                    if (gen == generation) listener.onError("camera session configuration failed")
                }
            })
        camera.createCaptureSession(cfg)
    }

    private fun closeCamera() {
        try { session?.stopRepeating() } catch (_: Exception) {}
        session?.close(); session = null
        device?.close(); device = null
        reader?.close(); reader = null
    }

    // -------------------------------------------------------------------------------------------- requests

    /** Image processing the device keeps on (D41): listed in `status.processing_on`. */
    private fun computeProcessingOn(c: CameraCharacteristics): List<String> {
        val on = ArrayList<String>(4)
        val ois = c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_OPTICAL_STABILIZATION)
        if (ois != null && ois.isNotEmpty() && ois.none { it == CameraMetadata.LENS_OPTICAL_STABILIZATION_MODE_OFF }) on.add("ois")
        val vs = c.get(CameraCharacteristics.CONTROL_AVAILABLE_VIDEO_STABILIZATION_MODES)
        if (vs != null && vs.isNotEmpty() && vs.none { it == CameraMetadata.CONTROL_VIDEO_STABILIZATION_MODE_OFF }) on.add("video_stabilization")
        val dc = c.get(CameraCharacteristics.DISTORTION_CORRECTION_AVAILABLE_MODES)
        if (dc != null && dc.isNotEmpty() && dc.none { it == CameraMetadata.DISTORTION_CORRECTION_MODE_OFF }) on.add("distortion_correction")
        return on
    }

    private fun request(): CaptureRequest.Builder? {
        val d = device ?: return null
        val r = reader ?: return null
        val c = chars ?: return null
        val b = d.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
        b.addTarget(r.surface)
        preview?.let { b.addTarget(it) }
        if (manual) {
            b.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_OFF)
            val er = c.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
            val ir = c.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
            var e = exposureNs.coerceAtMost(frameDurationNs)
            if (er != null) e = e.coerceIn(er.lower, er.upper)
            b.set(CaptureRequest.SENSOR_EXPOSURE_TIME, e)
            b.set(CaptureRequest.SENSOR_SENSITIVITY, if (ir != null) iso.coerceIn(ir.lower, ir.upper) else iso)
            b.set(CaptureRequest.SENSOR_FRAME_DURATION, frameDurationNs)
        } else {
            b.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)   // minimum compensation, locked after 1 s
            c.get(CameraCharacteristics.CONTROL_AE_COMPENSATION_RANGE)?.let { b.set(CaptureRequest.CONTROL_AE_EXPOSURE_COMPENSATION, it.lower) }
            b.set(CaptureRequest.CONTROL_AE_LOCK, aeLock)
            bestFpsRange(c)?.let { b.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, it) }
        }
        // D41: nothing that changes the image between the sensor and the detector
        if ("ois" !in processingOn) b.set(CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE, CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_OFF)
        if ("video_stabilization" !in processingOn) b.set(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_OFF)
        if ("distortion_correction" !in processingOn &&
            c.get(CameraCharacteristics.DISTORTION_CORRECTION_AVAILABLE_MODES)?.isNotEmpty() == true) {
            b.set(CaptureRequest.DISTORTION_CORRECTION_MODE, CaptureRequest.DISTORTION_CORRECTION_MODE_OFF)
        }
        if (c.get(CameraCharacteristics.NOISE_REDUCTION_AVAILABLE_NOISE_REDUCTION_MODES)?.contains(CaptureRequest.NOISE_REDUCTION_MODE_FAST) == true) {
            b.set(CaptureRequest.NOISE_REDUCTION_MODE, CaptureRequest.NOISE_REDUCTION_MODE_FAST)
        }
        if (c.get(CameraCharacteristics.EDGE_AVAILABLE_EDGE_MODES)?.contains(CaptureRequest.EDGE_MODE_FAST) == true) {
            b.set(CaptureRequest.EDGE_MODE, CaptureRequest.EDGE_MODE_FAST)
        }
        // focus and white balance (D41)
        b.set(CaptureRequest.CONTROL_AF_MODE, focusMode)
        focusDistance?.let { b.set(CaptureRequest.LENS_FOCUS_DISTANCE, it) }
        b.set(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO)
        if (c.get(CameraCharacteristics.CONTROL_AWB_LOCK_AVAILABLE) == true) b.set(CaptureRequest.CONTROL_AWB_LOCK, awbLock)
        return b
    }

    /** The AE target range that contains [fps] with the highest lower bound (only used without MANUAL_SENSOR). */
    private fun bestFpsRange(c: CameraCharacteristics): Range<Int>? =
        c.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
            ?.filter { it.upper >= fps.toInt() && it.lower <= fps.toInt() }?.maxByOrNull { it.lower }

    private fun applyRepeating() {
        val s = session ?: return
        val b = request() ?: return
        try {
            s.setRepeatingRequest(b.build(), captureCallback, handler)
        } catch (e: Exception) {
            listener.onError("camera request: ${e.message}")
        }
    }

    // -------------------------------------------------------------------------------------------- focus, white balance

    private fun beginFocusAndWhiteBalance(gen: Int) {
        val c = chars ?: return
        focusDistance = null
        awbLock = false
        aeLock = false
        val hasAf = (c.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE) ?: 0f) > 0f &&
            c.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES)?.contains(CaptureRequest.CONTROL_AF_MODE_AUTO) == true
        if (hasAf) {
            focusResult = "searching"; focus = focusResult
            focusMode = CaptureRequest.CONTROL_AF_MODE_AUTO
            applyRepeating()
            afTriggered = true
            try {
                val b = request() ?: return
                b.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_START)
                session?.capture(b.build(), captureCallback, handler)
            } catch (e: Exception) {
                finishFocus(gen, false)
                return
            }
            handler.postDelayed({ if (gen == generation && afTriggered) finishFocus(gen, false) }, AF_TIMEOUT_MS)
        } else {
            focusMode = CaptureRequest.CONTROL_AF_MODE_OFF         // fixed focus: nothing to find
            focusResult = "locked"; focus = focusResult
            applyRepeating()
            startWhiteBalance(gen)
        }
    }

    /** AF locked (or timed out): freeze the lens at the focus distance the camera found. */
    private fun finishFocus(gen: Int, ok: Boolean) {
        if (gen != generation || !afTriggered) return
        afTriggered = false
        focusMode = CaptureRequest.CONTROL_AF_MODE_OFF
        focusResult = if (ok) "locked" else "failed"; focus = focusResult
        if (!ok && "af" !in processingOn) processingOn = processingOn + "af"
        applyRepeating()
        startWhiteBalance(gen)
    }

    private fun startWhiteBalance(gen: Int) {
        awbLock = false
        aeLock = false
        applyRepeating()
        handler.postDelayed({
            if (gen != generation) return@postDelayed
            awbLock = true
            aeLock = !manual
            applyRepeating()
            publish()
            listener.onReady(active!!)
        }, AWB_SETTLE_MS)
    }

    private fun publish() {
        active = Active(cameraId, size, fps, frameDurationNs, exposureNs, iso, manual, focusResult, awbLock, processingOn,
            clock.clock.wire)
    }

    // -------------------------------------------------------------------------------------------- frames and results

    private fun onImage(rd: ImageReader, gen: Int) {
        val img = rd.acquireLatestImage() ?: return
        if (gen != generation) { img.close(); return }
        val ts = img.timestamp
        if (firstFrame) {
            firstFrame = false
            val decided = clock.decide(tsRealtime, ts)
            Log.i(TAG, "sync clock: ${decided.wire} (timestamp source realtime=$tsRealtime)")
            publish()
        }
        val availNs = clock.now()
        // the frame duration the HAL reports for this capture (it may keep 33 ms when 16.7 ms was asked for), else the requested one
        val dur = meta.frameDurationNs(ts).takeIf { it > 0 } ?: frameDurationNs
        if (lastTs != 0L && dur > 0 && ts - lastTs > dur * GAP_FACTOR) {
            mailbox.addSkipped(Math.round((ts - lastTs).toDouble() / dur) - 1)      // frames the sensor made that never reached us
        }
        lastTs = ts
        if (logged < DEBUG_FRAMES) {
            logged++
            Log.d(TAG, "frame $logged ts=$ts avail-ts=${(availNs - ts) / 1000} us exp=$lastExposureNs ns")
        }
        mailbox.put(img, availNs)
    }

    private val captureCallback = object : CameraCaptureSession.CaptureCallback() {
        override fun onCaptureCompleted(s: CameraCaptureSession, req: CaptureRequest, result: TotalCaptureResult) {
            val ts = result.get(CaptureResult.SENSOR_TIMESTAMP) ?: return
            val exp = result.get(CaptureResult.SENSOR_EXPOSURE_TIME) ?: exposureNs
            val skew = result.get(CaptureResult.SENSOR_ROLLING_SHUTTER_SKEW) ?: 0L
            val dur = result.get(CaptureResult.SENSOR_FRAME_DURATION) ?: frameDurationNs
            meta.record(ts, exp, skew, dur)
            lastExposureNs = exp
            if (afTriggered) {
                when (result.get(CaptureResult.CONTROL_AF_STATE)) {
                    CaptureResult.CONTROL_AF_STATE_FOCUSED_LOCKED -> lockLens(result, true)
                    CaptureResult.CONTROL_AF_STATE_NOT_FOCUSED_LOCKED -> lockLens(result, false)
                }
            }
        }
    }

    private fun lockLens(result: TotalCaptureResult, focused: Boolean) {
        focusDistance = result.get(CaptureResult.LENS_FOCUS_DISTANCE)
        finishFocus(generation, focused)
    }

    companion object {
        const val TAG = "DuoCamera"
        private const val MAX_IMAGES = 3                 // M2 plan step 8
        private const val AF_TIMEOUT_MS = 3000L          // M2 plan step 8: wait for a locked AF_STATE
        private const val AWB_SETTLE_MS = 1000L          // M2 plan step 8: AWB auto for 1 s, then lock
        private const val GAP_FACTOR = 1.5               // a timestamp gap above 1.5 frame durations means skipped frames
        private const val DEBUG_FRAMES = 100             // log the first frames after each start (H2 debug aid), never after
    }
}
