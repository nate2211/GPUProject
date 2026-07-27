#pragma once

#include <cstdint>

#ifdef _WIN32
    #ifdef GPU_HOST_RUNTIME_EXPORTS
        #define GHR_API __declspec(dllexport)
    #else
        #define GHR_API __declspec(dllimport)
    #endif
#else
    #define GHR_API
#endif

extern "C" {

struct GhrAdapterInfo {
    wchar_t name[128];
    std::uint64_t dedicated_video_memory;
    std::uint32_t vendor_id;
    std::uint32_t device_id;
    std::uint32_t subsystem_id;
    std::uint32_t revision;
};

// Returns the number of DXGI adapters, or a negative error code.
GHR_API int ghr_get_adapter_count();

// Returns 0 on success. The caller owns the output structure.
GHR_API int ghr_get_adapter_info(int index, GhrAdapterInfo* output);

}
