package com.duoware.sensor.camera

import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureResult
import com.duoware.sensor.proto.CameraCaps
import com.duoware.sensor.proto.YuvSize

/** One entry of the camera list in the UI. */
data class CameraChoice(val id: String, val focalMm: Float?, val manualSensor: Boolean, val back: Boolean) {
    val label: String get() = "$id: ${focalMm?.let { "%.1f mm".format(it) } ?: "? mm"}, MANUAL_SENSOR ${if (manualSensor) "yes" else "no"}"
}

/** Pure size / frame-rate rules (JVM-tested). */
object Sizes {
    /** [requested] if it is an offered size, else the closest offered size with the same aspect ratio, else the closest. */
    fun pick(offered: List<YuvSize>, requested: Pair<Int, Int>): Pair<Int, Int>? {
        if (offered.isEmpty()) return null
        offered.firstOrNull { it.w == requested.first && it.h == requested.second }?.let { return it.w to it.h }
        val ratio = requested.first.toDouble() / requested.second
        val same = offered.filter { Math.abs(it.w.toDouble() / it.h - ratio) < RATIO_TOLERANCE }
        val pool = same.ifEmpty { offered }
        val target = requested.first.toLong() * requested.second
        return pool.minByOrNull { Math.abs(it.w.toLong() * it.h - target) }!!.let { it.w to it.h }
    }

    /** The frame rate to run: [fps] (0 = the maximum for the size), never above the size's maximum, else null if unknown. */
    fun effectiveFps(offered: List<YuvSize>, size: Pair<Int, Int>, fps: Double): Double? {
        val max = offered.firstOrNull { it.w == size.first && it.h == size.second }?.maxFps ?: return null
        return if (fps <= 0.0 || fps > max) max else fps
    }

    fun frameDurationNs(fps: Double): Long = Math.round(1e9 / fps)

    private const val RATIO_TOLERANCE = 0.01
}

/** PROTOCOL.md §4.3 `hello.camera`, read from the device (every field; null where the device does not report it). */
object Capabilities {
    fun manager(ctx: Context): CameraManager = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager

    fun hardwareLevel(c: CameraCharacteristics): String = when (c.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL)) {
        CameraMetadata.INFO_SUPPORTED_HARDWARE_LEVEL_LEGACY -> "LEGACY"
        CameraMetadata.INFO_SUPPORTED_HARDWARE_LEVEL_LIMITED -> "LIMITED"
        CameraMetadata.INFO_SUPPORTED_HARDWARE_LEVEL_FULL -> "FULL"
        CameraMetadata.INFO_SUPPORTED_HARDWARE_LEVEL_3 -> "LEVEL_3"
        CameraMetadata.INFO_SUPPORTED_HARDWARE_LEVEL_EXTERNAL -> "EXTERNAL"
        else -> "LIMITED"
    }

    fun capabilityNames(c: CameraCharacteristics): List<String> =
        (c.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: IntArray(0)).toList().mapNotNull {
            when (it) {
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_BACKWARD_COMPATIBLE -> "BACKWARD_COMPATIBLE"
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR -> "MANUAL_SENSOR"
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_POST_PROCESSING -> "MANUAL_POST_PROCESSING"
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_RAW -> "RAW"
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_READ_SENSOR_SETTINGS -> "READ_SENSOR_SETTINGS"
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_BURST_CAPTURE -> "BURST_CAPTURE"
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_DEPTH_OUTPUT -> "DEPTH_OUTPUT"
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_LOGICAL_MULTI_CAMERA -> "LOGICAL_MULTI_CAMERA"
                CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_MONOCHROME -> "MONOCHROME"
                else -> null
            }
        }

    fun hasManualSensor(c: CameraCharacteristics): Boolean = "MANUAL_SENSOR" in capabilityNames(c)

    fun isBack(c: CameraCharacteristics): Boolean = c.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK

    fun yuvSizes(c: CameraCharacteristics): List<YuvSize> {
        val map = c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: return emptyList()
        return (map.getOutputSizes(ImageFormat.YUV_420_888) ?: emptyArray()).map { s ->
            val dur = map.getOutputMinFrameDuration(ImageFormat.YUV_420_888, s)
            YuvSize(s.width, s.height, if (dur > 0) 1e9 / dur else 0.0)
        }
    }

    /** Back cameras first, the one with MANUAL_SENSOR preferred (the default choice of the UI). */
    fun choices(ctx: Context): List<CameraChoice> {
        val m = manager(ctx)
        return m.cameraIdList.mapNotNull { id ->
            val c = try { m.getCameraCharacteristics(id) } catch (e: Exception) { return@mapNotNull null }
            if ("BACKWARD_COMPATIBLE" !in capabilityNames(c)) return@mapNotNull null
            CameraChoice(id, c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)?.firstOrNull(), hasManualSensor(c), isBack(c))
        }.sortedWith(compareByDescending<CameraChoice> { it.back }.thenByDescending { it.manualSensor })
    }

    fun defaultChoice(choices: List<CameraChoice>, stored: String?): CameraChoice? =
        choices.firstOrNull { it.id == stored } ?: choices.firstOrNull { it.back && it.manualSensor } ?: choices.firstOrNull { it.back }
        ?: choices.firstOrNull()

    fun build(ctx: Context, id: String): CameraCaps = build(manager(ctx).getCameraCharacteristics(id), id)

    fun build(c: CameraCharacteristics, id: String): CameraCaps {
        val manual = hasManualSensor(c)
        val exp = c.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
        val iso = c.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
        val fps = c.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES) ?: emptyArray()
        val keys = c.availableCaptureResultKeys
        val intr = c.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION)
        val dist = c.get(CameraCharacteristics.LENS_DISTORTION)
        val active = c.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE)
        return CameraCaps(
            id = id,
            hwLevel = hardwareLevel(c),
            capabilities = capabilityNames(c),
            tsSource = if (c.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE) ==
                CameraMetadata.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME) "REALTIME" else "UNKNOWN",
            exposureNsRange = if (manual && exp != null) exp.lower to exp.upper else null,
            isoRange = if (manual && iso != null) iso.lower to iso.upper else null,
            fpsRanges = fps.map { it.lower to it.upper },
            yuvSizes = yuvSizes(c),
            rollingShutterSkew = keys.contains(CaptureResult.SENSOR_ROLLING_SHUTTER_SKEW),
            minFocusDiopters = c.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE)?.toDouble(),
            intrinsics = intr?.map { it.toDouble() },
            distortion = dist?.map { it.toDouble() },
            activeArray = active?.let { it.width() to it.height() },
        )
    }
}
