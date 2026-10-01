// Result buffer written by nativeProcess and read by pipeline/ResultBuffer.kt without allocation (M2 step 7).
// Little-endian, fixed offsets. Change RESULT_LAYOUT_VERSION whenever the layout changes (Kotlin checks it).
#pragma once

#include <cstdint>
#include <cstring>

namespace duo {

constexpr int32_t RESULT_LAYOUT_VERSION = 1;
constexpr int MAX_MARKERS = 64;        // markers reported per frame (DICT_4X4_50 has 50 IDs)
constexpr int MAX_SEARCHED = 64;       // tag IDs listed in a roi frame's `searched`

// Header (byte offsets)
constexpr int OFF_LAYOUT = 0;          // int32 RESULT_LAYOUT_VERSION
constexpr int OFF_N_MARKERS = 4;       // int32
constexpr int OFF_SCAN = 8;            // int32: 0 full, 1 roi
constexpr int OFF_N_SEARCHED = 12;     // int32
constexpr int OFF_SEARCHED = 16;       // int32[MAX_SEARCHED]
constexpr int OFF_N_WINDOWS = OFF_SEARCHED + 4 * MAX_SEARCHED;   // 272: int32 windows searched (roi frames)
constexpr int OFF_LOST = OFF_N_WINDOWS + 4;                       // 276: int32 1 = a tracked tag was not found
constexpr int OFF_PREVIEW_W = OFF_LOST + 4;                       // 280: int32 preview width written, 0 = none
constexpr int OFF_PREVIEW_H = OFF_PREVIEW_W + 4;                  // 284: int32
constexpr int OFF_DETECT_NS = OFF_PREVIEW_H + 4;                  // 288: int64 detection time (8-aligned)
constexpr int OFF_MARKERS = OFF_DETECT_NS + 8;                    // 296: markers
constexpr int MARKER_BYTES = 4 + 8 * 4;                           // int32 id, float32 x0 y0 x1 y1 x2 y2 x3 y3
constexpr int RESULT_BYTES = OFF_MARKERS + MAX_MARKERS * MARKER_BYTES;   // 2600

inline void putI32(uint8_t* b, int off, int32_t v) { std::memcpy(b + off, &v, 4); }
inline void putI64(uint8_t* b, int off, int64_t v) { std::memcpy(b + off, &v, 8); }
inline void putF32(uint8_t* b, int off, float v) { std::memcpy(b + off, &v, 4); }

}  // namespace duo
