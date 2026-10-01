package com.duoware.sensor.pipeline

import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.objdetect.ArucoDetector
import org.opencv.objdetect.DetectorParameters
import org.opencv.objdetect.Dictionary
import org.opencv.objdetect.Objdetect
import java.nio.ByteBuffer

/**
 * `java_full` (benchmark only, DECISIONS.md D31): the natural OpenCV Java API path on full frames, so the JNI and
 * allocation overhead of the native pipeline is measured against something honest. It copies the Y plane into a
 * `Mat` with `put` and uses Java lists for the results; it allocates per frame on purpose.
 */
class JavaBaseline {
    private val dictionary: Dictionary = Objdetect.getPredefinedDictionary(Objdetect.DICT_4X4_50)
    private val detector = ArucoDetector(dictionary, DetectorParameters())

    /** Detects markers in the Y plane; returns how many. */
    fun detect(y: ByteBuffer, rowStride: Int, w: Int, h: Int): Int {
        val bytes = ByteArray(w * h)
        for (r in 0 until h) {
            y.position(r * rowStride)
            y.get(bytes, r * w, w)
        }
        val mat = Mat(h, w, CvType.CV_8UC1)
        mat.put(0, 0, bytes)
        val corners = ArrayList<Mat>()
        val ids = Mat()
        detector.detectMarkers(mat, corners, ids)
        val n = ids.rows()
        for (c in corners) c.release()
        ids.release()
        mat.release()
        return n
    }

    companion object {
        init {
            System.loadLibrary("opencv_java4")      // the Java classes bind their natives through this library
        }
    }
}
