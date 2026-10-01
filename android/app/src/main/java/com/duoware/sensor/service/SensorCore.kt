package com.duoware.sensor.service

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.Image
import android.os.BatteryManager
import android.os.SystemClock
import android.util.Log
import com.duoware.sensor.BuildConfig
import com.duoware.sensor.camera.CameraController
import com.duoware.sensor.camera.Capabilities
import com.duoware.sensor.camera.CaptureMeta
import com.duoware.sensor.camera.Sizes
import com.duoware.sensor.clock.ClockSource
import com.duoware.sensor.net.Discovery
import com.duoware.sensor.net.FrameSender
import com.duoware.sensor.net.Found
import com.duoware.sensor.net.NetworkBinder
import com.duoware.sensor.net.Session
import com.duoware.sensor.net.WifiLatencyLock
import com.duoware.sensor.perf.CpuStats
import com.duoware.sensor.perf.PerfManager
import com.duoware.sensor.perf.ThermalAdapter
import com.duoware.sensor.perf.ThermalGovernor
import com.duoware.sensor.pipeline.FramePipeline
import com.duoware.sensor.pipeline.ImageMailbox
import com.duoware.sensor.pipeline.PipelineRunner
import com.duoware.sensor.pipeline.SendSlot
import com.duoware.sensor.proto.CameraCaps
import com.duoware.sensor.proto.CameraSettings
import com.duoware.sensor.proto.CpuInfo
import com.duoware.sensor.proto.Messages
import com.duoware.sensor.proto.Pct
import com.duoware.sensor.proto.ServerError
import com.duoware.sensor.proto.Status
import com.duoware.sensor.proto.ThermalState
import com.duoware.sensor.proto.Welcome
import com.duoware.sensor.stats.StageStats
import com.duoware.sensor.store.Prefs
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * Everything the phone does while the sensor runs (M2 plan steps 8–10): discovery -> session -> camera -> pipeline ->
 * sender, the 1 Hz `status` and the thermal ladder. Owned by [SensorService]; the UI reads the `@Volatile` state.
 * A closed session stops the camera and the frames transport and starts discovery again after 1 s (a new session,
 * PROTOCOL.md §4.3 / DECISIONS.md D35).
 */
class SensorCore(private val ctx: Context) : Session.Listener, CameraController.Listener {
    val prefs = Prefs(ctx.getSharedPreferences("duoware", Context.MODE_PRIVATE))
    private val clock = ClockSource({ SystemClock.elapsedRealtimeNanos() }, { System.nanoTime() })
    private val meta = CaptureMeta()
    private val mailbox = ImageMailbox<Image>()
    private val slot = SendSlot()
    private val pipeline = FramePipeline({ clock.now() }, slot, meta)
    val perf = PerfManager(ctx)
    private val runner = PipelineRunner(mailbox, pipeline) { perf.reportWork(it) }
    private val controller = CameraController(ctx, clock, meta, mailbox, this)
    private val sender = FrameSender(slot, { clock.now() }, { s, r, c -> pipeline.onSent(s, r, c) })
    private val cpu = CpuStats({ System.nanoTime() })
    private val thermal = ThermalAdapter(ctx)
    private val wifiLock = WifiLatencyLock(ctx)
    private val executor: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor { Thread(it, "status") }
    private val discovery = Discovery(ctx, prefs, { step = it }, { onFound(it) }, { versionNote(it) })

    // ------------------------------------------------------------------------------------------- state for the UI
    @Volatile var running = false; private set
    @Volatile var step = "stopped"; private set
    @Volatile var linkState = "stopped"; private set
    @Volatile var lastError: String? = null; private set
    @Volatile var needsPairCode = false; private set
    @Volatile var found: Found? = null; private set
    @Volatile var welcome: Welcome? = null; private set
    @Volatile var snapshot: Status? = null; private set
    @Volatile var appMode = "tracking"
    @Volatile var pairCode: String? = null
    val sendDropped: Long get() = slot.dropped
    val framesSkipped: Long get() = mailbox.skipped
    val battery: Pair<Int?, Boolean?> get() = batteryNow()
    val cameraChoices get() = Capabilities.choices(ctx)

    // ------------------------------------------------------------------------------------------- session state
    private var session: Session? = null
    private var caps: CameraCaps? = null
    private var settings: CameraSettings? = null
    private var governor: ThermalGovernor? = null
    private var appliedLevel = -1
    private var holdForCode = false
    private var ticker: java.util.concurrent.ScheduledFuture<*>? = null
    private var lastFrames = 0L
    private var lastTickMs = 0L
    @Volatile private var fpsNow: Double? = null

    // ------------------------------------------------------------------------------------------- lifecycle

    @Synchronized
    fun start() {
        if (running) return
        running = true
        perf.foregroundService = true
        mailbox.reopen()
        runner.start()
        thermal.listen(executor) { thermalTick() }
        lastFrames = pipeline.frames; lastTickMs = SystemClock.elapsedRealtime()
        ticker = executor.scheduleAtFixedRate({ safely { tick() } }, 1, 1, TimeUnit.SECONDS)
        holdForCode = false
        discovery.start(0)
    }

    @Synchronized
    fun stop() {
        if (!running) return
        running = false
        ticker?.cancel(false); ticker = null
        discovery.stop()
        endSession()
        runner.stop()
        thermal.stop()
        perf.foregroundService = false
        step = "stopped"; linkState = "stopped"
    }

    /** Releases the native detector; the core cannot be started again. */
    fun destroy() {
        stop()
        executor.shutdownNow()
        controller.release()
        slot.close()
        pipeline.close()
    }

    private fun endSession() {
        session?.close(); session = null
        sender.stop()
        controller.stop()
        perf.closeHint()
        wifiLock.release(); perf.wifiLowLatency = null
        governor = null; appliedLevel = -1
        welcome = null; found = null; settings = null
    }

    /** The user changed mode, camera, manual host or pair code: a new session in the new setting. */
    @Synchronized
    fun restart(pairCodeEntered: String? = null) {
        if (!running) return
        if (pairCodeEntered != null) { pairCode = pairCodeEntered; holdForCode = false }
        discovery.stop()
        endSession()
        linkState = "searching"
        discovery.start(0)
    }

    fun refocus() = controller.refocus()

    // ------------------------------------------------------------------------------------------- discovery -> session

    private fun onFound(f: Found) {
        try {
            val choice = Capabilities.defaultChoice(Capabilities.choices(ctx), prefs.cameraId)
                ?: return fail("no camera on this phone")
            val c = Capabilities.build(ctx, choice.id)
            caps = c
            found = f
            linkState = "connecting"; step = "Connecting to ${f.beacon.name} at ${f.host} (${f.linkMode})"
            val hello = Session.helloFor(ctx, prefs, f, c, CpuInfo(cpu.cores, cpu.maxKhzReadable()), BuildConfig.VERSION_NAME, pairCode)
            session = Session(prefs, this).also { it.open(f, hello) }
        } catch (e: Exception) {
            Log.w(TAG, "connect failed", e)
            fail("connect failed: ${e.message}")
            discovery.start(RETRY_MS)
        }
    }

    private fun fail(msg: String) { lastError = msg; linkState = "error" }

    private fun versionNote(v: Int) { lastError = "server speaks protocol v$v" }

    override fun onWelcome(w: Welcome) {
        val f = found ?: return
        val c = caps ?: return
        welcome = w
        prefs.serverId = w.serverId
        lastError = null; needsPairCode = false; holdForCode = false
        pipeline.startSession(w.cam, w.sid)
        pipeline.configure(w.settings.tracking)
        settings = w.settings
        val tcp = f.linkMode == "wired_adb"
        sender.start(FrameSender.Target(f.host, if (tcp) w.framesTcpPort else w.framesPort, tcp, NetworkBinder(f.route), w.cam, w.sid))
        if (f.linkMode == "wireless") { wifiLock.acquire(); perf.wifiLowLatency = wifiLock.held } else perf.wifiLowLatency = false
        val base = Sizes.pick(c.yuvSizes, w.settings.resolution) ?: w.settings.resolution
        val baseFps = Sizes.effectiveFps(c.yuvSizes, base, w.settings.fps) ?: DEFAULT_FPS
        governor = ThermalGovernor(w.settings.thermal, baseFps, base)
        startCamera()
        linkState = "connected"; step = "Connected to ${f.beacon.name}"
    }

    private fun startCamera() {
        val s = settings ?: return
        val c = caps ?: return
        val lv = governor?.level ?: return
        appliedLevel = lv.index
        controller.start(c.id, lv.resolution, lv.fps, s.exposureNs, s.iso, null)
    }

    override fun onSettings(s: CameraSettings) {
        val old = settings ?: return
        val c = caps ?: return
        settings = s
        if (s.tracking != old.tracking) pipeline.configure(s.tracking)
        if (s.resolution != old.resolution || s.fps != old.fps || s.thermal != old.thermal) {
            val base = Sizes.pick(c.yuvSizes, s.resolution) ?: s.resolution
            governor = ThermalGovernor(s.thermal, Sizes.effectiveFps(c.yuvSizes, base, s.fps) ?: DEFAULT_FPS, base)
            startCamera()
        } else if (s.exposureNs != old.exposureNs || s.iso != old.iso) {
            controller.update(governor?.level?.fps ?: 0.0, s.exposureNs, s.iso)
        }
    }

    override fun onServerError(e: ServerError) {
        lastError = "${e.code}: ${e.message}"
        if (e.code == "bad_token" || e.code == "bad_pair_code" || e.code == "locked") {
            needsPairCode = true; holdForCode = true             // do not hammer the server: wait for a new code
        }
    }

    override fun onVersionMismatch(serverVersion: Int) = versionNote(serverVersion)

    override fun onClosed(reason: String) {
        if (!running) return
        Log.i(TAG, "session ended: $reason")
        endSession()
        if (lastError == null) lastError = reason
        linkState = if (holdForCode) "needs pair code" else "searching"
        if (!holdForCode) discovery.start(RETRY_MS)
    }

    // ------------------------------------------------------------------------------------------- camera

    override fun onReady(active: CameraController.Active) {
        pipeline.fallbackExposureNs = active.exposureNs
        perf.startHint(intArrayOf(runner.tid) + pipeline.workerTids(), active.frameDurationNs)
    }

    override fun onError(message: String) {
        lastError = message
        if (message.contains("permission")) {                    // retrying cannot help: the user must grant it
            linkState = "needs camera permission"; discovery.stop(); endSession()
            return
        }
        restart()
    }

    // ------------------------------------------------------------------------------------------- 1 Hz

    private fun thermalTick() {
        val g = governor ?: return
        val f = settings?.thermal?.forecastS ?: DEFAULT_FORECAST_S
        val lv = g.tick(SystemClock.elapsedRealtime(), thermal.headroom(f), thermal.status())
        if (lv.index == appliedLevel) return
        val s = settings ?: return
        val active = controller.active
        appliedLevel = lv.index
        if (active == null || lv.resolution != active.size) startCamera()
        else controller.update(lv.fps, s.exposureNs, s.iso)
        perf.updateTarget(Sizes.frameDurationNs(lv.fps))
    }

    private fun tick() {
        val nowMs = SystemClock.elapsedRealtime()
        val frames = pipeline.frames
        val dt = (nowMs - lastTickMs) / 1000.0
        fpsNow = if (dt > 0 && running) (frames - lastFrames) / dt else null
        lastFrames = frames; lastTickMs = nowMs
        thermalTick()
        val st = buildStatus(nowMs)
        snapshot = st
        if (welcome != null) session?.send(Messages.status(st))
    }

    private fun buildStatus(nowMs: Long): Status {
        val w = welcome
        val active = controller.active
        val out = FloatArray(2)
        val nowClockMs = clock.now() / 1_000_000
        val stages = HashMap<String, Pct?>()
        for (s in StageStats.Stage.entries) {
            stages[s.wire] = if (pipeline.stats.get(s, nowClockMs, out)) Pct(out[0].toDouble(), out[1].toDouble()) else null
        }
        val g = governor
        val th = ThermalState(thermal.status(), thermal.headroom(settings?.thermal?.forecastS ?: DEFAULT_FORECAST_S)
            ?.takeIf { !it.isNaN() }, g?.level?.index, g?.level?.reason)
        val (pct, charging) = batteryNow()
        val exp = controller.lastExposureNs.takeIf { it > 0 }
        return Status(
            w?.cam ?: 0, appMode, clock.clock.wire, found?.let { Session.linkInfo(ctx, it) },
            fpsNow, active?.fps, active?.size, stages, settings?.tracking?.fullScanEvery,
            if (active != null) pipeline.roiWindows.toDouble() else null, pipeline.lostRescans, mailbox.skipped, slot.dropped,
            exp, active?.iso, active?.focus ?: controller.focus.takeIf { active != null },
            cpu.appPct(), cpu.maxCurKhz(), th, perf.state(), pct, charging, pipeline.markersSeen, active?.processingOn,
        )
    }

    private fun batteryNow(): Pair<Int?, Boolean?> {
        val i: Intent = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return null to null
        val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        val plugged = i.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1)
        return (if (level >= 0 && scale > 0) level * 100 / scale else null) to (if (plugged < 0) null else plugged != 0)
    }

    private inline fun safely(block: () -> Unit) {
        try { block() } catch (e: Exception) { Log.w(TAG, "status tick failed", e) }
    }

    companion object {
        private const val TAG = "SensorCore"
        private const val RETRY_MS = 1000L                       // PROTOCOL.md §4.3: discovery again after 1 s
        private const val DEFAULT_FPS = 30.0
        private const val DEFAULT_FORECAST_S = 10.0
    }
}
