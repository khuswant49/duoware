if(NOT TARGET OpenCV::opencv_java4)
add_library(OpenCV::opencv_java4 SHARED IMPORTED)
set_target_properties(OpenCV::opencv_java4 PROPERTIES
    IMPORTED_LOCATION "C:/Users/khusw/.gradle/caches/8.13/transforms/c7afcffa028c31b0e968dbec74f8630c/transformed/opencv-4.14.0/prefab/modules/opencv_java4/libs/android.arm64-v8a/libopencv_java4.so"
    INTERFACE_INCLUDE_DIRECTORIES "C:/Users/khusw/.gradle/caches/8.13/transforms/c7afcffa028c31b0e968dbec74f8630c/transformed/opencv-4.14.0/prefab/modules/opencv_java4/include"
    INTERFACE_LINK_LIBRARIES ""
)
endif()

