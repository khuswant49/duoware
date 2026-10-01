#include "detector.hpp"

#include <cmath>

namespace duo {

float Detection::side() const {
    float s = 0.f;
    for (int i = 0; i < 4; ++i) {
        const int j = (i + 1) % 4;
        s += std::hypot(c[2 * j] - c[2 * i], c[2 * j + 1] - c[2 * i + 1]);
    }
    return s * 0.25f;
}

namespace {

cv::aruco::DetectorParameters params(int cornerRefine, bool aruco3) {
    cv::aruco::DetectorParameters p;
    p.cornerRefinementMethod = cornerRefine == 1 ? cv::aruco::CORNER_REFINE_SUBPIX : cv::aruco::CORNER_REFINE_NONE;
    p.useAruco3Detection = aruco3;
    return p;
}

const cv::aruco::Dictionary& dictionary() {
    static const cv::aruco::Dictionary d = cv::aruco::getPredefinedDictionary(cv::aruco::DICT_4X4_50);
    return d;
}

}  // namespace

Detector::Detector()
    : full_(dictionary(), params(1, false)), roi_(dictionary(), params(1, false)) {
    corners_.reserve(64);
    ids_.reserve(64);
    rejected_.reserve(256);
}

void Detector::configure(int cornerRefine, bool aruco3) {
    full_.setDetectorParameters(params(cornerRefine, aruco3));
    roi_.setDetectorParameters(params(cornerRefine, false));       // windows are small: ArUco 3 brings nothing there
}

void Detector::detect(const cv::Mat& view, int offX, int offY, bool full, std::vector<Detection>& out) {
    corners_.clear();
    ids_.clear();
    rejected_.clear();
    (full ? full_ : roi_).detectMarkers(view, corners_, ids_, rejected_);
    for (size_t k = 0; k < ids_.size(); ++k) {
        Detection d{};
        d.id = ids_[k];
        for (int i = 0; i < 4; ++i) {
            d.c[2 * i] = corners_[k][i].x + static_cast<float>(offX);
            d.c[2 * i + 1] = corners_[k][i].y + static_cast<float>(offY);
        }
        out.push_back(d);
    }
}

}  // namespace duo
