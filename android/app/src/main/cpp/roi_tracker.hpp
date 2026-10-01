// Region-of-interest tracking (DECISIONS.md D32, PROTOCOL.md §4.4 `tracking`, M2 plan step 7). Pure logic: it decides
// whether a frame is a full scan or which windows to search, and digests what was detected. No allocation per frame
// (fixed arrays; the caller's vectors keep their capacity).
#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include <opencv2/core.hpp>

#include "detector.hpp"

namespace duo {

constexpr int DICT_SIZE = 50;                          // DICT_4X4_50
constexpr int MAX_WINDOWS = 16;                        // windows searched per roi frame
constexpr int64_t STALE_VELOCITY_NS = 500'000'000;     // two detections further apart than this: no velocity

struct TrackerConfig {
    std::vector<int> trackIds;     // the server's car tags (a hint list; the phone knows no roles)
    int fullScanEvery = 10;
    float roiMargin = 1.5f;        // window grows by this many tag side lengths on every side
    int roiMinPx = 64;
    int demoteAfterScans = 3;
};

struct Window {
    int id;                        // the tracked tag this window is for
    cv::Rect rect;                 // clipped to the image
    float cx, cy;                  // predicted centre of the tag
};

class RoiTracker {
public:
    void configure(const TrackerConfig& cfg);

    // Decides the next frame: returns true for a full scan; otherwise fills `windows` (cleared first).
    bool plan(int64_t capNs, int w, int h, bool forceFull, std::vector<Window>& windows);

    // Digests a frame's merged detections. Returns true when a tracked tag was not found in its window (`lost`), in
    // which case the next frame is a full scan.
    bool update(int64_t capNs, bool full, const std::vector<Window>& windows, const std::vector<Detection>& dets);

    bool isTracked(int id) const { return id >= 0 && id < DICT_SIZE && tags_[id].tracked; }
    int trackedCount() const;

private:
    struct Tag {
        bool seen = false;                 // has a last detection
        bool hasPrev = false;
        Detection last{}, prev{};
        int64_t lastNs = 0, prevNs = 0;
        bool tracked = false;
        bool promoted = false;             // tracked because it moved, not because it is in trackIds
        int stillScans = 0;
        bool hasFullPos = false;
        float fullCx = 0.f, fullCy = 0.f;  // centre at the previous full scan
    };

    void record(Tag& t, const Detection& d, int64_t capNs);
    bool inTrackIds(int id) const;
    cv::Rect window(const Tag& t, int64_t capNs, int w, int h, float& cx, float& cy) const;

    std::array<Tag, DICT_SIZE> tags_{};
    TrackerConfig cfg_{};
    int64_t frameIndex_ = 0;
    bool lostLast_ = false;
    bool changed_ = true;
};

}  // namespace duo
