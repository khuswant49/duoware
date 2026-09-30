// JNI entry points of the sensor app's native library (DECISIONS.md D31). Step 2 of the M2 plan only has the
// version report; the per-frame functions arrive with the pipeline (step 7).

#include <jni.h>

#include <string>

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
