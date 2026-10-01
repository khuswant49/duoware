package com.duoware.sensor.proto

import org.json.JSONArray
import org.json.JSONObject

/**
 * PROTOCOL.md §3.2 and §4.3–4.7 session messages (org.json; never on the per-frame path, which uses [FrameWriter]).
 * Encoders for what the phone sends (`hello`, `status`, `bench`, `sync_r`), parsers for what it receives (`welcome`,
 * `settings`, `error`, `sync`). Values the phone cannot measure are `null`, never invented (CLAUDE.md).
 */
const val PROTOCOL_VERSION = 1

class ProtocolException(message: String) : Exception(message)

// ------------------------------------------------------------------------------------------------- hello

data class WifiInfo(val bandGhz: Double?, val rssiDbm: Int?, val linkMbps: Int?)

data class LinkInfo(val mode: String, val iface: String, val localIp: String, val wifi: WifiInfo?)

data class CpuInfo(val cores: Int, val maxKhz: List<Long>)

data class CameraCaps(
    val id: String,
    val hwLevel: String,
    val capabilities: List<String>,
    val tsSource: String,
    val exposureNsRange: Pair<Long, Long>?,
    val isoRange: Pair<Int, Int>?,
    val fpsRanges: List<Pair<Int, Int>>,
    val yuvSizes: List<YuvSize>,
    val rollingShutterSkew: Boolean,
    val minFocusDiopters: Double?,
    val intrinsics: List<Double>?,
    val distortion: List<Double>?,
    val activeArray: Pair<Int, Int>?,
)

data class YuvSize(val w: Int, val h: Int, val maxFps: Double)

data class Hello(
    val deviceId: String,
    val token: String?,
    val pairCode: String?,
    val appVersion: String,
    val model: String,
    val android: String,
    val sdk: Int,
    val link: LinkInfo,
    val cpu: CpuInfo,
    val camera: CameraCaps,
)

// ------------------------------------------------------------------------------------------------- status

data class Pct(val p50: Double, val p95: Double)

data class ThermalState(val status: Int?, val headroom: Double?, val level: Int?, val reason: String?)

data class PerfState(
    val foregroundService: Boolean?, val wakeLock: Boolean?, val wifiLowLatency: Boolean?,
    val sustainedMode: Boolean?, val hintSession: Boolean?, val batteryOptExempt: Boolean?,
)

data class Status(
    val cam: Int,
    val appMode: String,
    val clock: String,
    val link: LinkInfo?,
    val fps: Double?,
    val fpsTarget: Double?,
    val resolution: Pair<Int, Int>?,
    val stagesMs: Map<String, Pct?>,              // keys: pipeline, detect_full, detect_roi, send, cap_to_sent
    val scanFullEvery: Int?,
    val roisPerFrame: Double?,
    val lostRescans: Long?,
    val framesSkipped: Long?,
    val sendDropped: Long?,
    val exposureNs: Long?,
    val iso: Int?,
    val focus: String?,
    val cpuAppPct: Double?,
    val cpuMaxCurKhz: Long?,
    val thermal: ThermalState?,
    val perf: PerfState?,
    val batteryPct: Int?,
    val charging: Boolean?,
    val markersSeen: Int?,
    val processingOn: List<String>?,               // null: not reported (PROTOCOL.md §4.5, from M2)
)

// ------------------------------------------------------------------------------------------------- bench

data class BenchResult(
    val resolution: Pair<Int, Int>, val fpsTarget: Double, val pipeline: String, val fullScanEvery: Int,
    val aruco3: Boolean, val durationS: Double, val fps: Double?, val capToSentMs: Pct?, val detectFullMs: Pct?,
    val detectRoiMs: Pct?, val cpuAppPct: Double?, val headroomStart: Double?, val headroomEnd: Double?,
    val markersSeen: Double?,
)

data class Bench(val cam: Int, val runId: String, val results: List<BenchResult>)

// ------------------------------------------------------------------------------------------------- settings

data class Tracking(
    val trackIds: List<Int>, val fullScanEvery: Int, val roiMargin: Double, val roiMinPx: Int, val threads: Int,
    val demoteAfterScans: Int, val cornerRefine: String, val aruco3: Boolean,
)

data class ThermalPolicy(
    val forecastS: Double, val headroomDown: Double, val headroomUp: Double, val upAfterS: Double,
    val statusDown: Int, val fpsSteps: List<Double>, val resolutionSteps: List<Pair<Int, Int>>,
)

data class PreviewSettings(val fps: Double, val width: Int, val quality: Int)

data class CameraSettings(
    val resolution: Pair<Int, Int>, val fps: Double, val exposureNs: Long, val iso: Int, val focus: String,
    val awb: String, val tracking: Tracking, val thermal: ThermalPolicy, val preview: PreviewSettings,
)

data class Welcome(
    val cam: Int, val sid: String, val token: String?, val framesPort: Int, val framesTcpPort: Int,
    val serverId: String, val settings: CameraSettings,
)

data class ServerError(val code: String, val message: String)

data class SyncRequest(val sid: String, val n: Long, val t1: Long)

object Messages {
    // ---------------------------------------------------------------------------------------------- encoders

    fun hello(h: Hello): String = JSONObject().apply {
        put("v", PROTOCOL_VERSION); put("t", "hello")
        put("device_id", h.deviceId); put("token", h.token.js()); put("pair_code", h.pairCode.js())
        put("app_version", h.appVersion); put("model", h.model); put("android", h.android); put("sdk", h.sdk)
        put("link", link(h.link))
        put("cpu", JSONObject().put("cores", h.cpu.cores).put("max_khz", JSONArray(h.cpu.maxKhz)))
        put("camera", camera(h.camera))
    }.toString()

    fun status(s: Status): String = JSONObject().apply {
        put("v", PROTOCOL_VERSION); put("t", "status"); put("cam", s.cam)
        put("app_mode", s.appMode); put("clock", s.clock)
        put("link", s.link?.let { link(it) }.js())
        put("fps", s.fps.js()); put("fps_target", s.fpsTarget.js())
        put("resolution", s.resolution?.let { JSONArray(listOf(it.first, it.second)) }.js())
        put("stages_ms", JSONObject().also { st ->
            for (name in STAGES) st.put(name, s.stagesMs[name]?.let { pct(it) }.js())
        })
        put("scan", JSONObject().put("full_every", s.scanFullEvery.js())
            .put("rois_per_frame", s.roisPerFrame.js()).put("lost_rescans", s.lostRescans.js()))
        put("frames_skipped", s.framesSkipped.js()); put("send_dropped", s.sendDropped.js())
        put("exposure_ns", s.exposureNs.js()); put("iso", s.iso.js()); put("focus", s.focus.js())
        put("cpu", JSONObject().put("app_pct", s.cpuAppPct.js()).put("max_cur_khz", s.cpuMaxCurKhz.js()))
        put("thermal", s.thermal?.let {
            JSONObject().put("status", it.status.js()).put("headroom", it.headroom.js())
                .put("level", it.level.js()).put("reason", it.reason.js())
        }.js())
        put("perf", s.perf?.let {
            JSONObject().put("foreground_service", it.foregroundService.js()).put("wake_lock", it.wakeLock.js())
                .put("wifi_low_latency", it.wifiLowLatency.js()).put("sustained_mode", it.sustainedMode.js())
                .put("hint_session", it.hintSession.js()).put("battery_opt_exempt", it.batteryOptExempt.js())
        }.js())
        put("battery_pct", s.batteryPct.js()); put("charging", s.charging.js())
        put("markers_seen", s.markersSeen.js())
        if (s.processingOn != null) put("processing_on", JSONArray(s.processingOn))
    }.toString()

    fun bench(b: Bench): String = JSONObject().apply {
        put("v", PROTOCOL_VERSION); put("t", "bench"); put("cam", b.cam); put("run_id", b.runId)
        put("results", JSONArray().also { arr ->
            for (r in b.results) arr.put(JSONObject().apply {
                put("resolution", JSONArray(listOf(r.resolution.first, r.resolution.second)))
                put("fps_target", r.fpsTarget); put("pipeline", r.pipeline); put("full_scan_every", r.fullScanEvery)
                put("aruco3", r.aruco3); put("duration_s", r.durationS); put("fps", r.fps.js())
                put("cap_to_sent_ms", r.capToSentMs?.let { pct(it) }.js())
                put("detect_full_ms", r.detectFullMs?.let { pct(it) }.js())
                put("detect_roi_ms", r.detectRoiMs?.let { pct(it) }.js())
                put("cpu_app_pct", r.cpuAppPct.js()); put("headroom_start", r.headroomStart.js())
                put("headroom_end", r.headroomEnd.js()); put("markers_seen", r.markersSeen.js())
            })
        })
    }.toString()

    fun syncReply(cam: Int, sid: String, n: Long, t1: Long, t2: Long, t3: Long): String =
        "{\"v\":1,\"t\":\"sync_r\",\"cam\":$cam,\"sid\":\"$sid\",\"n\":$n,\"t1\":$t1,\"t2\":$t2,\"t3\":$t3}"

    private fun link(l: LinkInfo) = JSONObject().put("mode", l.mode).put("interface", l.iface).put("local_ip", l.localIp)
        .put("wifi", l.wifi?.let {
            JSONObject().put("band_ghz", it.bandGhz.js()).put("rssi_dbm", it.rssiDbm.js())
                .put("link_mbps", it.linkMbps.js())
        }.js())

    private fun pct(p: Pct) = JSONObject().put("p50", p.p50).put("p95", p.p95)

    private fun camera(c: CameraCaps) = JSONObject().apply {
        put("id", c.id); put("hw_level", c.hwLevel); put("capabilities", JSONArray(c.capabilities))
        put("ts_source", c.tsSource)
        put("exposure_ns_range", c.exposureNsRange?.let { JSONArray(listOf(it.first, it.second)) }.js())
        put("iso_range", c.isoRange?.let { JSONArray(listOf(it.first, it.second)) }.js())
        put("fps_ranges", JSONArray(c.fpsRanges.map { JSONArray(listOf(it.first, it.second)) }))
        put("yuv_sizes", JSONArray(c.yuvSizes.map { JSONArray(listOf(it.w, it.h, it.maxFps)) }))
        put("rolling_shutter_skew", c.rollingShutterSkew)
        put("min_focus_diopters", c.minFocusDiopters.js())
        put("intrinsics", c.intrinsics?.let { JSONArray(it) }.js())
        put("distortion", c.distortion?.let { JSONArray(it) }.js())
        put("active_array", c.activeArray?.let { JSONArray(listOf(it.first, it.second)) }.js())
    }

    private val STAGES = listOf("pipeline", "detect_full", "detect_roi", "send", "cap_to_sent")

    // ---------------------------------------------------------------------------------------------- parsers

    /** The `t` of a server message after checking `v` (throws on another version or non-JSON). */
    fun type(o: JSONObject): String {
        val v = o.optInt("v", -1)
        if (v != PROTOCOL_VERSION) throw ProtocolException("server speaks protocol v$v")
        return o.optString("t")
    }

    @Throws(ProtocolException::class)
    fun parseObject(text: String): JSONObject = try {
        JSONObject(text)
    } catch (e: Exception) {
        throw ProtocolException("not JSON: ${e.message}")
    }

    @Throws(ProtocolException::class)
    fun welcome(o: JSONObject): Welcome = guard {
        Welcome(o.getInt("cam"), o.getString("sid"), o.optStringOrNull("token"), o.getInt("frames_port"),
            o.getInt("frames_tcp_port"), o.getString("server_id"), settings(o.getJSONObject("settings")))
    }

    @Throws(ProtocolException::class)
    fun settings(o: JSONObject): CameraSettings = guard {
        val t = o.getJSONObject("tracking")
        val th = o.getJSONObject("thermal")
        val pv = o.getJSONObject("preview")
        CameraSettings(
            pair(o.getJSONArray("resolution")), o.getDouble("fps"), o.getLong("exposure_ns"), o.getInt("iso"),
            o.getString("focus"), o.getString("awb"),
            Tracking(ints(t.getJSONArray("track_ids")), t.getInt("full_scan_every"), t.getDouble("roi_margin"),
                t.getInt("roi_min_px"), t.getInt("threads"), t.getInt("demote_after_scans"),
                t.getString("corner_refine"), t.getBoolean("aruco3")),
            ThermalPolicy(th.getDouble("forecast_s"), th.getDouble("headroom_down"), th.getDouble("headroom_up"),
                th.getDouble("up_after_s"), th.getInt("status_down"), doubles(th.getJSONArray("fps_steps")),
                th.getJSONArray("resolution_steps").let { a -> (0 until a.length()).map { pair(a.getJSONArray(it)) } }),
            PreviewSettings(pv.getDouble("fps"), pv.getInt("width"), pv.getInt("quality")),
        )
    }

    @Throws(ProtocolException::class)
    fun error(o: JSONObject): ServerError = guard { ServerError(o.getString("code"), o.optString("message")) }

    @Throws(ProtocolException::class)
    fun sync(o: JSONObject): SyncRequest = guard { SyncRequest(o.getString("sid"), o.getLong("n"), o.getLong("t1")) }

    private inline fun <T> guard(block: () -> T): T = try {
        block()
    } catch (e: ProtocolException) {
        throw e
    } catch (e: Exception) {
        throw ProtocolException("bad message: ${e.message}")
    }

    private fun pair(a: JSONArray): Pair<Int, Int> {
        if (a.length() != 2) throw ProtocolException("expected two numbers")
        return a.getInt(0) to a.getInt(1)
    }

    private fun ints(a: JSONArray) = (0 until a.length()).map { a.getInt(it) }
    private fun doubles(a: JSONArray) = (0 until a.length()).map { a.getDouble(it) }
}

/** org.json writes Kotlin null as a missing key; the protocol wants an explicit `null`. */
fun Any?.js(): Any = this ?: JSONObject.NULL

fun JSONObject.optStringOrNull(key: String): String? = if (isNull(key)) null else optString(key)
