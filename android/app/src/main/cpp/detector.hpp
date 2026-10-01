// One ArUco detector per worker (M2 step 7, DECISIONS.md D31). Detection runs on a cv::Mat view (no copy); the output
// vectors are members, cleared and reused (OpenCV's detectMarkers still allocates internally; the java_full vs native
// benchmark measures that).
#pragma once

#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/objdetect/aruco_detector.hpp>

namespace duo {

struct Detection {
    int id;
    float c[8];       // x0 y0 x1 y1 x2 y2 x3 y3 in full-image pixels (ArUco corner order, PROTOCOL.md §0)
    float cx() const { return (c[0] + c[2] + c[4] + c[6]) * 0.25f; }
    float cy() const { return (c[1] + c[3] + c[5] + c[7]) * 0.25f; }
    float side() const;   // mean edge length, px
};

class Detector {
public:
    Detector();
    // corner_refine: 0 none, 1 subpix. aruco3 applies to full scans only (useAruco3Detection).
    void configure(int cornerRefine, bool aruco3);
    // Detects in `view` (a sub-matrix of the frame); corners are shifted by (offX, offY) into full-image pixels and
    // appended to `out`. `full` selects the full-scan parameters.
    void detect(const cv::Mat& view, int offX, int offY, bool full, std::vector<Detection>& out);

private:
    cv::aruco::ArucoDetector full_;
    cv::aruco::ArucoDetector roi_;
    std::vector<std::vector<cv::Point2f>> corners_;
    std::vector<int> ids_;
    std::vector<std::vector<cv::Point2f>> rejected_;
};

}  // namespace duo
