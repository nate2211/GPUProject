#include "gpu_host_runtime.h"

#include <Windows.h>
#include <dxgi1_6.h>
#include <wrl/client.h>

#include <algorithm>
#include <cstring>

using Microsoft::WRL::ComPtr;

namespace {

int create_factory(ComPtr<IDXGIFactory1>& factory) {
    const HRESULT result = CreateDXGIFactory1(IID_PPV_ARGS(&factory));
    return SUCCEEDED(result) ? 0 : -1;
}

}  // namespace

extern "C" GHR_API int ghr_get_adapter_count() {
    ComPtr<IDXGIFactory1> factory;
    if (create_factory(factory) != 0) {
        return -1;
    }

    int count = 0;
    while (true) {
        ComPtr<IDXGIAdapter1> adapter;
        const HRESULT result = factory->EnumAdapters1(
            static_cast<UINT>(count),
            &adapter
        );
        if (result == DXGI_ERROR_NOT_FOUND) {
            break;
        }
        if (FAILED(result)) {
            return -2;
        }
        ++count;
    }
    return count;
}

extern "C" GHR_API int ghr_get_adapter_info(
    const int index,
    GhrAdapterInfo* output
) {
    if (index < 0 || output == nullptr) {
        return -10;
    }

    ComPtr<IDXGIFactory1> factory;
    if (create_factory(factory) != 0) {
        return -1;
    }

    ComPtr<IDXGIAdapter1> adapter;
    const HRESULT enum_result = factory->EnumAdapters1(
        static_cast<UINT>(index),
        &adapter
    );
    if (enum_result == DXGI_ERROR_NOT_FOUND) {
        return -11;
    }
    if (FAILED(enum_result)) {
        return -2;
    }

    DXGI_ADAPTER_DESC1 description{};
    const HRESULT description_result = adapter->GetDesc1(&description);
    if (FAILED(description_result)) {
        return -3;
    }

    std::memset(output, 0, sizeof(GhrAdapterInfo));
    wcsncpy_s(
        output->name,
        _countof(output->name),
        description.Description,
        _TRUNCATE
    );
    output->dedicated_video_memory =
        static_cast<std::uint64_t>(description.DedicatedVideoMemory);
    output->vendor_id = description.VendorId;
    output->device_id = description.DeviceId;
    output->subsystem_id = description.SubSysId;
    output->revision = description.Revision;
    return 0;
}
