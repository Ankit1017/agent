cmake_minimum_required(VERSION 3.24)

project(harness-audio2face-bridge LANGUAGES CXX)

if(NOT DEFINED AUDIO2FACE_SDK_ROOT)
    message(FATAL_ERROR "AUDIO2FACE_SDK_ROOT is required")
endif()

add_subdirectory("${AUDIO2FACE_SDK_ROOT}" audio2face-sdk-build)

add_executable(audio2face-bridge main.cpp)
target_compile_features(audio2face-bridge PRIVATE cxx_std_17)
target_include_directories(audio2face-bridge PRIVATE
    "${AUDIO2FACE_SDK_ROOT}/_deps/target-deps/audiofile/include"
)
target_link_libraries(audio2face-bridge PRIVATE audio2x)

set_target_properties(audio2face-bridge PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)
