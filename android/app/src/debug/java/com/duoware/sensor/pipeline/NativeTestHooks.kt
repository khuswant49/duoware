package com.duoware.sensor.pipeline

/**
 * Test-only JNI functions (`cpp/test_hooks.cpp`, compiled in the debug build type only). The class lives in the
 * `debug` source set (not `androidTest`) because ART binds a native method only to libraries loaded by the class's own
 * class loader: the test APK's loader would not find `libduosensor.so`, the app's loader does. The release build has no
 * trace of it.
 */
object NativeTestHooks {
    init {
        System.loadLibrary("duosensor")
    }

    /** Draws marker [markerId] (DICT_4X4_50, [sidePx] px) on a grey frame, runs the detector, returns the IDs found. */
    external fun detectGeneratedMarker(markerId: Int, sidePx: Int): IntArray

    /**
     * Renders a synthetic frame into [out] (direct, [rowStride] bytes per row): per marker in [spec] (n x [id, cx, cy,
     * side px, angle deg]) a DICT_4X4_50 marker drawn at [superSample] x resolution and area-averaged down, then an
     * optional motion blur of [blurPx] px along [blurAngleDeg]. Returns the true corners (n x 8, ArUco order).
     */
    external fun renderScene(
        spec: FloatArray, superSample: Int, blurPx: Float, blurAngleDeg: Float, out: java.nio.ByteBuffer, w: Int,
        h: Int, rowStride: Int,
    ): FloatArray
}
