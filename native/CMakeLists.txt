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

if (MSVC)
    target_compile_options(gpu_host_runtime PRIVATE /W4 /permissive- /EHsc)
endif()

target_link_libraries(gpu_host_runtime PRIVATE
    d3d12
    d3dcompiler
    dxgi
)
set_target_properties(gpu_host_runtime PROPERTIES
    OUTPUT_NAME "gpu_host_runtime"
)

add_library(process_isolation_runtime SHARED
    process_isolation_runtime.cpp
    process_isolation_runtime.h
)

target_compile_features(process_isolation_runtime PRIVATE cxx_std_20)
target_compile_definitions(process_isolation_runtime PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    PROCESS_ISOLATION_RUNTIME_EXPORTS
)

if (MSVC)
    target_compile_options(process_isolation_runtime PRIVATE /W4 /permissive- /EHsc)
endif()

set_target_properties(process_isolation_runtime PROPERTIES
    OUTPUT_NAME "process_isolation_runtime"
)
