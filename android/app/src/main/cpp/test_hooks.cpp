// Test-only JNI functions, compiled only when DUO_TEST_HOOKS is defined (the debug build type). They let the
// instrumented tests make synthetic images with cv::aruco::generateImageMarker and run the real detector on them.

#include <jni.h>

#include <cmath>
#include <cstring>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
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

namespace {

constexpr int CELL_PX = 40;            // source marker resolution: 6 cells (4 bits + border) of 40 px
constexpr int PAD_PX = CELL_PX;        // white quiet zone of one cell around the marker
constexpr uint8_t FLOOR_GREY = 150;    // background brightness of the synthetic floor

}  // namespace

// Renders a synthetic grey frame into `out` (direct, rowStride bytes per row): for every marker in `spec`
// (n x [id, cx, cy, side px, angle deg]) a DICT_4X4_50 marker with a white quiet zone, drawn at `superSample` times
// the resolution and area-averaged down (sub-pixel accurate edges), then an optional linear motion blur of `blurPx`
// px along `blurAngleDeg`. Returns the true corners (n x 8 floats, ArUco order, OpenCV pixel-centre convention).
extern "C" JNIEXPORT jfloatArray JNICALL
Java_com_duoware_sensor_pipeline_NativeTestHooks_renderScene(JNIEnv* env, jobject, jfloatArray specArr, jint superSample,
                                                            jfloat blurPx, jfloat blurAngleDeg, jobject out, jint w,
                                                            jint h, jint rowStride) {
    const jsize nf = env->GetArrayLength(specArr);
    std::vector<float> spec(static_cast<size_t>(nf));
    env->GetFloatArrayRegion(specArr, 0, nf, spec.data());
    const int n = nf / 5;
    const int ss = superSample < 1 ? 1 : superSample;
    const cv::aruco::Dictionary dict = cv::aruco::getPredefinedDictionary(cv::aruco::DICT_4X4_50);

    cv::Mat hi(h * ss, w * ss, CV_8UC1, cv::Scalar(FLOOR_GREY));
    std::vector<float> truth(static_cast<size_t>(n) * 8);
    const int markerPx = 6 * CELL_PX;
    const float srcCentre = PAD_PX + markerPx * 0.5f - 0.5f;
    for (int k = 0; k < n; ++k) {
        const int id = static_cast<int>(spec[5 * k]);
        const float cx = spec[5 * k + 1], cy = spec[5 * k + 2], side = spec[5 * k + 3];
        const float ang = spec[5 * k + 4] * static_cast<float>(CV_PI) / 180.f;
        cv::Mat marker;
        cv::aruco::generateImageMarker(dict, id, markerPx, marker, 1);
        cv::Mat src(markerPx + 2 * PAD_PX, markerPx + 2 * PAD_PX, CV_8UC1, cv::Scalar(255));
        marker.copyTo(src(cv::Rect(PAD_PX, PAD_PX, markerPx, markerPx)));
        // affine src -> hi-res frame: scale, rotate about the marker centre, move it to (cx, cy) in hi-res pixels
        const float s = side * ss / markerPx;
        const float hx = (cx + 0.5f) * ss - 0.5f, hy = (cy + 0.5f) * ss - 0.5f;   // pixel-centre convention
        const float a = s * std::cos(ang), b = s * std::sin(ang);
        cv::Matx23f M(a, -b, hx - (a * srcCentre - b * srcCentre),
                      b, a, hy - (b * srcCentre + a * srcCentre));
        cv::warpAffine(src, hi, M, hi.size(), cv::INTER_LINEAR, cv::BORDER_TRANSPARENT);
        const float e0 = PAD_PX - 0.5f, e1 = PAD_PX + markerPx - 0.5f;          // edges of the black square
        const float sx[4] = {e0, e1, e1, e0}, sy[4] = {e0, e0, e1, e1};
        for (int i = 0; i < 4; ++i) {
            const float X = M(0, 0) * sx[i] + M(0, 1) * sy[i] + M(0, 2);
            const float Y = M(1, 0) * sx[i] + M(1, 1) * sy[i] + M(1, 2);
            truth[8 * k + 2 * i] = (X + 0.5f) / ss - 0.5f;                     // back to output pixels
            truth[8 * k + 2 * i + 1] = (Y + 0.5f) / ss - 0.5f;
        }
    }
    cv::Mat img;
    if (ss > 1) cv::resize(hi, img, cv::Size(w, h), 0, 0, cv::INTER_AREA); else img = hi;
    if (blurPx > 1.f) {
        const int len = static_cast<int>(std::ceil(blurPx)) | 1;
        cv::Mat kernel = cv::Mat::zeros(len, len, CV_32F);
        const float ba = blurAngleDeg * static_cast<float>(CV_PI) / 180.f;
        const float c = (len - 1) * 0.5f;
        for (int t = 0; t < len * 4; ++t) {           // a line through the centre, sampled finely
            const float u = (t / (len * 4.f - 1.f) - 0.5f) * blurPx;
            const int x = static_cast<int>(std::lround(c + u * std::cos(ba)));
            const int y = static_cast<int>(std::lround(c + u * std::sin(ba)));
            if (x >= 0 && x < len && y >= 0 && y < len) kernel.at<float>(y, x) += 1.f;
        }
        kernel /= cv::sum(kernel)[0];
        cv::filter2D(img, img, -1, kernel);
    }
    auto* dst = static_cast<uint8_t*>(env->GetDirectBufferAddress(out));
    for (int r = 0; r < h; ++r) std::memcpy(dst + static_cast<size_t>(r) * rowStride, img.ptr(r), static_cast<size_t>(w));
    jfloatArray res = env->NewFloatArray(static_cast<jsize>(truth.size()));
    env->SetFloatArrayRegion(res, 0, static_cast<jsize>(truth.size()), truth.data());
    return res;
}

