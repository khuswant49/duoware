# Native part of the DUO-WARE sensor app (DECISIONS.md D31): OpenCV ArUco detection in C++.
#
# OpenCV comes from the Maven Central AAR through Prefab (app/build.gradle.kts: buildFeatures.prefab). If that
# package does not resolve, unpack the OpenCV Android SDK into android/third_party/opencv and configure with
#   -DOpenCV_DIR=<repo>/android/third_party/opencv/sdk/native/jni
# (README "Android native build" says how); the `OpenCV::opencv_java4` target then comes from that config or, when
# it is missing, from ${OpenCV_LIBS}.
cmake_minimum_required(VERSION 3.22)
project(duosensor LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# A debug-built native library is several times slower and would make every measurement meaningless, so -O2 is
# used in every build type. The flag is also set in the per-config flag variables, because CMake puts those before
# target options and Debug's own -O0 would otherwise come first on some toolchains.
set(CMAKE_CXX_FLAGS_DEBUG "-g -O2")

find_package(OpenCV REQUIRED CONFIG)
if(TARGET OpenCV::opencv_java4)
  set(DUO_OPENCV_LIBS OpenCV::opencv_java4)
else()
  set(DUO_OPENCV_LIBS ${OpenCV_LIBS})
endif()

set(DUO_SOURCES jni_bridge.cpp pipeline.cpp roi_tracker.cpp detector.cpp)

add_library(duosensor SHARED ${DUO_SOURCES})

# Test-only JNI functions (synthetic images for the instrumented tests). The debug build type sets DUO_TEST_HOOKS=ON
# from app/build.gradle.kts; the release build has none of this code.
if(DUO_TEST_HOOKS)
  target_sources(duosensor PRIVATE test_hooks.cpp)
  target_compile_definitions(duosensor PRIVATE DUO_TEST_HOOKS=1)
endif()

target_compile_options(duosensor PRIVATE -O2 -Wall -Wextra)
target_link_libraries(duosensor ${DUO_OPENCV_LIBS} log android)
