#include "roi_tracker.hpp"

#include <algorithm>
#include <cmath>

namespace duo {

void RoiTracker::configure(const TrackerConfig& cfg) {
    cfg_ = cfg;
    if (cfg_.fullScanEvery < 1) cfg_.fullScanEvery = 1;
    for (int id = 0; id < DICT_SIZE; ++id) {          // a tag dropped from track_ids is no longer followed
        if (tags_[id].tracked && !tags_[id].promoted && !inTrackIds(id)) tags_[id].tracked = false;
    }
    changed_ = true;                                   // the next frame is a full scan
}

bool RoiTracker::inTrackIds(int id) const {
    return std::find(cfg_.trackIds.begin(), cfg_.trackIds.end(), id) != cfg_.trackIds.end();
}

int RoiTracker::trackedCount() const {
    int n = 0;
    for (const auto& t : tags_) n += t.tracked ? 1 : 0;
    return n;
}

void RoiTracker::record(Tag& t, const Detection& d, int64_t capNs) {
    if (t.seen) {
        t.prev = t.last;
        t.prevNs = t.lastNs;
        t.hasPrev = true;
    }
    t.last = d;
    t.lastNs = capNs;
    t.seen = true;
}

cv::Rect RoiTracker::window(const Tag& t, int64_t capNs, int w, int h, float& cx, float& cy) const {
    float dx = 0.f, dy = 0.f;
    const int64_t span = t.lastNs - t.prevNs;
    if (t.hasPrev && span > 0 && span <= STALE_VELOCITY_NS) {
        const float k = static_cast<float>(capNs - t.lastNs) / static_cast<float>(span);
        dx = (t.last.cx() - t.prev.cx()) * k;
        dy = (t.last.cy() - t.prev.cy()) * k;
    }
    float x0 = t.last.c[0], x1 = t.last.c[0], y0 = t.last.c[1], y1 = t.last.c[1];
    for (int i = 1; i < 4; ++i) {
        x0 = std::min(x0, t.last.c[2 * i]);
        x1 = std::max(x1, t.last.c[2 * i]);
        y0 = std::min(y0, t.last.c[2 * i + 1]);
        y1 = std::max(y1, t.last.c[2 * i + 1]);
    }
    const float grow = cfg_.roiMargin * t.last.side();
    x0 += dx - grow; x1 += dx + grow; y0 += dy - grow; y1 += dy + grow;
    cx = (x0 + x1) * 0.5f;
    cy = (y0 + y1) * 0.5f;
    const float half = static_cast<float>(cfg_.roiMinPx) * 0.5f;     // at least roi_min_px square
    if (x1 - x0 < 2 * half) { x0 = cx - half; x1 = cx + half; }
    if (y1 - y0 < 2 * half) { y0 = cy - half; y1 = cy + half; }
    const int ix0 = std::max(0, static_cast<int>(std::floor(x0)));
    const int iy0 = std::max(0, static_cast<int>(std::floor(y0)));
    const int ix1 = std::min(w, static_cast<int>(std::ceil(x1)));
    const int iy1 = std::min(h, static_cast<int>(std::ceil(y1)));
    if (ix1 <= ix0 || iy1 <= iy0) return {};
    return {ix0, iy0, ix1 - ix0, iy1 - iy0};
}

bool RoiTracker::plan(int64_t capNs, int w, int h, bool forceFull, std::vector<Window>& windows) {
    windows.clear();
    const bool due = frameIndex_ % cfg_.fullScanEvery == 0;
    ++frameIndex_;
    if (forceFull || changed_ || lostLast_ || due || trackedCount() == 0) return true;
    for (int id = 0; id < DICT_SIZE && static_cast<int>(windows.size()) < MAX_WINDOWS; ++id) {
        const Tag& t = tags_[id];
        if (!t.tracked || !t.seen) continue;
        float cx = 0.f, cy = 0.f;
        const cv::Rect r = window(t, capNs, w, h, cx, cy);
        if (r.area() > 0) windows.push_back({id, r, cx, cy});
    }
    return windows.empty();                            // nothing to search (all windows outside): scan everything
}

bool RoiTracker::update(int64_t capNs, bool full, const std::vector<Window>& windows,
                        const std::vector<Detection>& dets) {
    changed_ = false;
    bool lost = false;
    std::array<bool, DICT_SIZE> found{};
    for (const auto& d : dets) {
        if (d.id < 0 || d.id >= DICT_SIZE) continue;
        found[d.id] = true;
        record(tags_[d.id], d, capNs);
    }
    if (full) {
        for (int id = 0; id < DICT_SIZE; ++id) {
            Tag& t = tags_[id];
            if (!found[id]) {                          // not in view: stop searching for it until a full scan sees it
                t.tracked = false;
                t.promoted = false;
                t.stillScans = 0;
                t.hasFullPos = false;
                continue;
            }
            const float cx = t.last.cx(), cy = t.last.cy();
            const bool moved = t.hasFullPos && std::hypot(cx - t.fullCx, cy - t.fullCy) > t.last.side();
            if (inTrackIds(id)) {
                t.tracked = true;
                t.promoted = false;
            } else if (moved) {                        // an untracked tag moved more than its own size: follow it
                t.tracked = true;
                t.promoted = true;
                t.stillScans = 0;
            } else if (t.promoted) {                   // still: demoted after demote_after_scans still full scans
                if (++t.stillScans >= cfg_.demoteAfterScans) {
                    t.tracked = false;
                    t.promoted = false;
                    t.stillScans = 0;
                }
            } else {
                t.tracked = false;
            }
            t.fullCx = cx;
            t.fullCy = cy;
            t.hasFullPos = true;
        }
    } else {
        for (const auto& wdw : windows) {
            if (!found[wdw.id]) lost = true;          // PROTOCOL.md §4.4: the next frame is a full scan
        }
    }
    lostLast_ = lost;
    return lost;
}

}  // namespace duo
