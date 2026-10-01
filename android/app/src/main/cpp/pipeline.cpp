#include "pipeline.hpp"

#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <map>
#include <thread>

#include <opencv2/core/utility.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include "results.hpp"

namespace duo {

namespace {

int64_t nowNs() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count();
}

constexpr int WARMUP_TASKS = 64;            // parallel_for_ tasks used once to find OpenCV's worker threads
constexpr int WARMUP_SPIN_US = 500;         // busy time per task, so the work spreads over every worker

}  // namespace

Pipeline::Pipeline(int maxW, int maxH) : maxW_(maxW), maxH_(maxH), fullDetector_(new Detector()) {
    for (int i = 0; i < MAX_WINDOWS; ++i) windowDetectors_.emplace_back(new Detector());
    windows_.reserve(MAX_WINDOWS);
    perWindow_.resize(MAX_WINDOWS);
    for (auto& v : perWindow_) v.reserve(8);
    merged_.reserve(MAX_MARKERS * 2);
    mergedDist_.reserve(MAX_MARKERS * 2);
    configure(TrackerConfig{}, 0, 1, false);
}

void Pipeline::configure(const TrackerConfig& cfg, int threads, int cornerRefine, bool aruco3) {
    tracker_.configure(cfg);
    threads_ = threads > 0 ? threads : fastestClusterCores();
    cv::setNumThreads(threads_);
    fullDetector_->configure(cornerRefine, aruco3);
    for (auto& d : windowDetectors_) d->configure(cornerRefine, aruco3);
}

void Pipeline::merge(const std::vector<Window>& windows) {
    // A tag seen by two overlapping windows is reported once: from the window whose centre is nearest to the marker
    // (duplicate IDs would make the server drop the tag, PROTOCOL.md §2).
    merged_.clear();
    mergedDist_.clear();
    for (size_t k = 0; k < windows.size(); ++k) {
        const cv::Rect& r = windows[k].rect;
        const float wx = r.x + r.width * 0.5f, wy = r.y + r.height * 0.5f;
        for (const auto& d : perWindow_[k]) {
            const float dist = std::hypot(d.cx() - wx, d.cy() - wy);
            bool replaced = false;
            for (size_t m = 0; m < merged_.size(); ++m) {
                if (merged_[m].id != d.id) continue;
                if (dist < mergedDist_[m]) {
                    merged_[m] = d;
                    mergedDist_[m] = dist;
                }
                replaced = true;
                break;
            }
            if (!replaced) {
                merged_.push_back(d);
                mergedDist_.push_back(dist);
            }
        }
    }
}

int Pipeline::process(const uint8_t* y, int rowStride, int w, int h, int64_t capNs, bool forceFull, int previewW,
                      uint8_t* result, uint8_t* preview, int previewCap) {
    if (y == nullptr || result == nullptr || w <= 0 || h <= 0 || w > maxW_ || h > maxH_ || rowStride < w) return -1;
    const cv::Mat frame(h, w, CV_8UC1, const_cast<uint8_t*>(y), static_cast<size_t>(rowStride));   // no copy
    const int64_t t0 = nowNs();
    const bool full = tracker_.plan(capNs, w, h, forceFull, windows_);
    if (full) {
        merged_.clear();
        fullDetector_->detect(frame, 0, 0, true, merged_);
    } else {
        const int n = static_cast<int>(windows_.size());
        for (int k = 0; k < n; ++k) perWindow_[k].clear();
        cv::parallel_for_(cv::Range(0, n), [&](const cv::Range& range) {
            for (int k = range.start; k < range.end; ++k) {
                const cv::Rect& r = windows_[k].rect;
                windowDetectors_[k]->detect(frame(r), r.x, r.y, false, perWindow_[k]);
            }
        });
        merge(windows_);
    }
    const bool lost = tracker_.update(capNs, full, windows_, merged_);
    const int64_t detectNs = nowNs() - t0;

    const int nMarkers = std::min(static_cast<int>(merged_.size()), MAX_MARKERS);
    putI32(result, OFF_LAYOUT, RESULT_LAYOUT_VERSION);
    putI32(result, OFF_N_MARKERS, nMarkers);
    putI32(result, OFF_SCAN, full ? 0 : 1);
    const int nSearched = full ? 0 : std::min(static_cast<int>(windows_.size()), MAX_SEARCHED);
    putI32(result, OFF_N_SEARCHED, nSearched);
    for (int i = 0; i < nSearched; ++i) putI32(result, OFF_SEARCHED + 4 * i, windows_[i].id);
    putI32(result, OFF_N_WINDOWS, full ? 0 : static_cast<int>(windows_.size()));
    putI32(result, OFF_LOST, lost ? 1 : 0);
    putI64(result, OFF_DETECT_NS, detectNs);
    for (int i = 0; i < nMarkers; ++i) {
        const int off = OFF_MARKERS + i * MARKER_BYTES;
        putI32(result, off, merged_[i].id);
        for (int j = 0; j < 8; ++j) putF32(result, off + 4 + 4 * j, merged_[i].c[j]);
    }

    int pw = 0, ph = 0;
    if (previewW > 0 && preview != nullptr) {
        pw = std::min(previewW, w);
        ph = static_cast<int>(std::lround(static_cast<double>(h) * pw / w));
        if (ph > 0 && pw * ph <= previewCap) {
            cv::Mat dst(ph, pw, CV_8UC1, preview);
            cv::resize(frame, dst, dst.size(), 0, 0, cv::INTER_AREA);   // writes into `preview`: dst has its size
        } else {
            pw = ph = 0;
        }
    }
    putI32(result, OFF_PREVIEW_W, pw);
    putI32(result, OFF_PREVIEW_H, ph);
    return nMarkers;
}

std::vector<int> Pipeline::workerTids() {
    std::mutex m;
    std::set<int> tids;
    cv::parallel_for_(cv::Range(0, WARMUP_TASKS), [&](const cv::Range& range) {
        for (int i = range.start; i < range.end; ++i) {
            const int64_t until = nowNs() + WARMUP_SPIN_US * 1000LL;
            while (nowNs() < until) {
            }
            std::lock_guard<std::mutex> lock(m);
            tids.insert(static_cast<int>(gettid()));
        }
    });
    return {tids.begin(), tids.end()};
}

int fastestClusterCores() {
    const long n = sysconf(_SC_NPROCESSORS_CONF);
    std::map<long, int> byFreq;
    for (long cpu = 0; cpu < n; ++cpu) {
        char path[96];
        std::snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%ld/cpufreq/cpuinfo_max_freq", cpu);
        FILE* f = std::fopen(path, "r");
        if (f == nullptr) continue;
        long khz = 0;
        if (std::fscanf(f, "%ld", &khz) == 1 && khz > 0) byFreq[khz]++;
        std::fclose(f);
    }
    if (byFreq.empty()) return std::max(1, cv::getNumberOfCPUs());
    return byFreq.rbegin()->second;
}

int encodeJpeg(const uint8_t* src, int w, int h, int quality, uint8_t* out, int outCap) {
    if (src == nullptr || out == nullptr || w <= 0 || h <= 0) return -1;
    const cv::Mat img(h, w, CV_8UC1, const_cast<uint8_t*>(src));
    std::vector<uchar> buf;
    if (!cv::imencode(".jpg", img, buf, {cv::IMWRITE_JPEG_QUALITY, quality})) return -1;
    if (static_cast<int>(buf.size()) > outCap) return -1;
    std::copy(buf.begin(), buf.end(), out);
    return static_cast<int>(buf.size());
}

}  // namespace duo
