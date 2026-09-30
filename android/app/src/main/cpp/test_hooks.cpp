// Test-only JNI functions, compiled only when DUO_TEST_HOOKS is defined (the debug build type). They let the
// instrumented tests make synthetic images with cv::aruco::generateImageMarker and run the real detector on them.

#include <jni.h>

#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/objdetect/aruco_detector.hpp>

extern "C" JNIEXPORT jintArray JNICALL
Java_com_duoware_sensor_pipeline_NativeTestHooks_detectGeneratedMarker(JNIEnv* env, jobject /*self*/, jint markerId,
                                                                       jint sidePx) {
    const cv::aruco::Dictionary dict = cv::aruco::getPredefinedDictionary(cv::aruco::DICT_4X4_50);
    cv::Mat marker;
    cv::aruco::generateImageMarker(dict, markerId, sidePx, marker, 1);

    const int margin = sidePx / 2;                      // quiet zone around the marker
    cv::Mat frame(sidePx + 2 * margin, sidePx + 2 * margin, CV_8UC1, cv::Scalar(200));
    marker.copyTo(frame(cv::Rect(margin, margin, sidePx, sidePx)));

    cv::aruco::ArucoDetector detector(dict, cv::aruco::DetectorParameters());
    std::vector<std::vector<cv::Point2f>> corners;
    std::vector<int> ids;
    detector.detectMarkers(frame, corners, ids);

    jintArray out = env->NewIntArray(static_cast<jsize>(ids.size()));
    if (out != nullptr && !ids.empty()) env->SetIntArrayRegion(out, 0, static_cast<jsize>(ids.size()), ids.data());
    return out;
}
