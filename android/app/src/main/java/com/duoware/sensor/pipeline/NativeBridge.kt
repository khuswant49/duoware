package com.duoware.sensor.pipeline

/**
 * JNI entry points of `libduosensor.so` (C++, OpenCV; DECISIONS.md D31). Step 2 of the M2 plan has only the version
 * report; the per-frame functions arrive with the native pipeline (step 7).
 */
object NativeBridge {
    init {
        System.loadLibrary("duosensor")
    }

    /** OpenCV's version, `cv::getNumThreads()` and the "Parallel framework" line of `cv::getBuildInformation()`. */
    external fun nativeVersion(): String
}
