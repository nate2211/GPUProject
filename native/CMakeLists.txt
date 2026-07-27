cmake_minimum_required(VERSION 3.20)
project(gpu_host_runtime LANGUAGES CXX)

add_library(gpu_host_runtime SHARED
    gpu_host_runtime.cpp
    gpu_host_runtime.h
)

target_compile_features(gpu_host_runtime PRIVATE cxx_std_20)
target_compile_definitions(gpu_host_runtime PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    GPU_HOST_RUNTIME_EXPORTS
)

target_link_libraries(gpu_host_runtime PRIVATE dxgi)
set_target_properties(gpu_host_runtime PROPERTIES
    OUTPUT_NAME "gpu_host_runtime"
)
