// The per-frame native pipeline (M2 step 7, DECISIONS.md D31, D32): one call per frame, on the pipeline thread.
// The Y plane is wrapped in a cv::Mat header (no copy); everything else is allocated once.
#pragma once

#include <cstdint>
#include <memory>
#include <mutex>
#include <set>
#include <vector>

#include "detector.hpp"
#include "roi_tracker.hpp"

namespace duo {

class Pipeline {
public:
    Pipeline(int maxW, int maxH);

    // threads: 0 = the cores of the fastest cluster. cornerRefine: 0 none, 1 subpix.
    void configure(const TrackerConfig& cfg, int threads, int cornerRefine, bool aruco3);

    // Detects in one frame and fills `result` (results.hpp layout). When previewW > 0 and `preview` is not null, also
    // writes an area-averaged downscale of the Y plane (previewW x round(h * previewW / w), at most previewCap bytes).
    // Returns the number of markers, or -1 on bad arguments.
    int process(const uint8_t* y, int rowStride, int w, int h, int64_t capNs, bool forceFull, int previewW,
                uint8_t* result, uint8_t* preview, int previewCap);

    // Thread IDs of OpenCV's worker threads (for the ADPF hint session). Called once, not per frame.
    std::vector<int> workerTids();

    int threads() const { return threads_; }

private:
    void merge(const std::vector<Window>& windows);

    int maxW_, maxH_;
    int threads_ = 1;
    RoiTracker tracker_;
    std::unique_ptr<Detector> fullDetector_;
    std::vector<std::unique_ptr<Detector>> windowDetectors_;   // one per window slot, so windows run in parallel
    std::vector<Window> windows_;
    std::vector<std::vector<Detection>> perWindow_;
    std::vector<Detection> merged_;
    std::vector<float> mergedDist_;
};

// Number of cores in the CPU cluster with the highest maximum frequency (sysfs); all cores if that cannot be read.
int fastestClusterCores();

// Greyscale JPEG of a w x h buffer (preview thread, not the hot path). Returns the length, or -1 if `outCap` is too
// small or encoding failed.
int encodeJpeg(const uint8_t* src, int w, int h, int quality, uint8_t* out, int outCap);

}  // namespace duo
