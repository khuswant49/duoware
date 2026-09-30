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
}
