// JNI entry points of the sensor app's native library (DECISIONS.md D31; M2 plan steps 2 and 7). Per frame there is
// one call, nativeProcess, with direct ByteBuffers only (GetDirectBufferAddress): nothing is copied or allocated.

#include <jni.h>

#include <string>
#include <vector>

#include "pipeline.hpp"
#include "results.hpp"

#include <opencv2/core.hpp>
#include <opencv2/core/utility.hpp>

namespace {

// The "Parallel framework:" line of cv::getBuildInformation(), whitespace collapsed ("Parallel framework: pthreads").
std::string parallelFrameworkLine() {
    const std::string info = cv::getBuildInformation();
    const std::string key = "Parallel framework:";
    const size_t start = info.find(key);
    if (start == std::string::npos) return "Parallel framework: unknown";
    size_t end = info.find('\n', start);
    if (end == std::string::npos) end = info.size();
    const std::string rest = info.substr(start + key.size(), end - start - key.size());
    std::string value;
    bool space = true;                                  // drops leading and repeated blanks
    for (char c : rest) {
        if (c == ' ' || c == '\t' || c == '\r') {
            space = true;
        } else {
            if (space && !value.empty()) value += ' ';
            value += c;
            space = false;
        }
    }
    return key + " " + (value.empty() ? "none" : value);
}

}  // namespace

extern "C" JNIEXPORT jstring JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeVersion(JNIEnv* env, jobject /*self*/) {
    const std::string text = std::string("OpenCV ") + CV_VERSION + " | threads " + std::to_string(cv::getNumThreads()) +
                             " | cpus " + std::to_string(cv::getNumberOfCPUs()) + " | " + parallelFrameworkLine();
    return env->NewStringUTF(text.c_str());
}

// ---------------------------------------------------------------------------------------------------- pipeline

namespace {

duo::Pipeline* pipe(jlong handle) { return reinterpret_cast<duo::Pipeline*>(handle); }

uint8_t* direct(JNIEnv* env, jobject buffer, jlong minBytes) {
    if (buffer == nullptr) return nullptr;
    if (env->GetDirectBufferCapacity(buffer) < minBytes) return nullptr;
    return static_cast<uint8_t*>(env->GetDirectBufferAddress(buffer));
}

}  // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeCreate(JNIEnv*, jobject, jint maxW, jint maxH) {
    return reinterpret_cast<jlong>(new duo::Pipeline(maxW, maxH));
}

extern "C" JNIEXPORT void JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeDestroy(JNIEnv*, jobject, jlong handle) {
    delete pipe(handle);
}

extern "C" JNIEXPORT void JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeConfigure(JNIEnv* env, jobject, jlong handle, jintArray trackIds,
                                                              jint fullScanEvery, jfloat roiMargin, jint roiMinPx,
                                                              jint threads, jint demoteAfterScans, jint cornerRefine,
                                                              jboolean aruco3) {
    duo::TrackerConfig cfg;
    const jsize n = trackIds == nullptr ? 0 : env->GetArrayLength(trackIds);
    cfg.trackIds.resize(static_cast<size_t>(n));
    if (n > 0) env->GetIntArrayRegion(trackIds, 0, n, cfg.trackIds.data());
    cfg.fullScanEvery = fullScanEvery;
    cfg.roiMargin = roiMargin;
    cfg.roiMinPx = roiMinPx;
    cfg.demoteAfterScans = demoteAfterScans;
    pipe(handle)->configure(cfg, threads, cornerRefine, aruco3 == JNI_TRUE);
}

extern "C" JNIEXPORT jint JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeProcess(JNIEnv* env, jobject, jlong handle, jobject y,
                                                            jint rowStride, jint w, jint h, jlong capNs,
                                                            jboolean forceFull, jint previewW, jobject result,
                                                            jobject preview) {
    uint8_t* yp = direct(env, y, static_cast<jlong>(rowStride) * (h - 1) + w);
    uint8_t* rp = direct(env, result, duo::RESULT_BYTES);
    uint8_t* pp = preview == nullptr ? nullptr : direct(env, preview, 1);
    const jlong pcap = preview == nullptr ? 0 : env->GetDirectBufferCapacity(preview);
    if (yp == nullptr || rp == nullptr) return -1;
    return pipe(handle)->process(yp, rowStride, w, h, capNs, forceFull == JNI_TRUE, previewW, rp, pp,
                                 static_cast<int>(pcap));
}

extern "C" JNIEXPORT jintArray JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeWorkerTids(JNIEnv* env, jobject, jlong handle) {
    const std::vector<int> tids = pipe(handle)->workerTids();
    jintArray out = env->NewIntArray(static_cast<jsize>(tids.size()));
    if (out != nullptr && !tids.empty()) env->SetIntArrayRegion(out, 0, static_cast<jsize>(tids.size()), tids.data());
    return out;
}

extern "C" JNIEXPORT jint JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeThreads(JNIEnv*, jobject, jlong handle) {
    return pipe(handle)->threads();
}

extern "C" JNIEXPORT jint JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeFastestClusterCores(JNIEnv*, jobject) {
    return duo::fastestClusterCores();
}

extern "C" JNIEXPORT jint JNICALL
Java_com_duoware_sensor_pipeline_NativeBridge_nativeEncodeJpeg(JNIEnv* env, jobject, jobject src, jint w, jint h,
                                                              jint quality, jobject out) {
    uint8_t* sp = direct(env, src, static_cast<jlong>(w) * h);
    uint8_t* op = direct(env, out, 1);
    if (sp == nullptr || op == nullptr) return -1;
    return duo::encodeJpeg(sp, w, h, quality, op, static_cast<int>(env->GetDirectBufferCapacity(out)));
}

