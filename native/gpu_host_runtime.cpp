#include "gpu_host_runtime.h"

#include <Windows.h>
#include <d3d12.h>
#include <d3dcompiler.h>
#include <dxgi1_6.h>
#include <wrl/client.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace {

constexpr std::uint32_t kAbiVersion = 0x00040000;
constexpr std::uint32_t kRegisterCount = 16;
constexpr std::uint32_t kMaximumLanes = 1u << 20;
constexpr std::uint32_t kMaximumInstructions = 4096;
constexpr std::uint32_t kMaximumSteps = 1u << 20;

thread_local std::wstring g_last_error;

void set_error(const std::wstring& message) {
    g_last_error = message;
}

void set_hresult_error(const wchar_t* context, const HRESULT result) {
    wchar_t buffer[256]{};
    _snwprintf_s(
        buffer,
        _countof(buffer),
        _TRUNCATE,
        L"%s failed with HRESULT 0x%08X",
        context,
        static_cast<unsigned int>(result)
    );
    set_error(buffer);
}

int create_factory(ComPtr<IDXGIFactory6>& factory) {
    const HRESULT result = CreateDXGIFactory1(IID_PPV_ARGS(&factory));
    if (FAILED(result)) {
        set_hresult_error(L"CreateDXGIFactory1", result);
        return -1;
    }
    return 0;
}

int get_adapter(const int index, ComPtr<IDXGIAdapter1>& adapter) {
    if (index < 0) {
        set_error(L"Adapter index cannot be negative.");
        return -10;
    }

    ComPtr<IDXGIFactory6> factory;
    if (create_factory(factory) != 0) {
        return -1;
    }

    const HRESULT result = factory->EnumAdapters1(static_cast<UINT>(index), &adapter);
    if (result == DXGI_ERROR_NOT_FOUND) {
        set_error(L"The requested DXGI adapter does not exist.");
        return -11;
    }
    if (FAILED(result)) {
        set_hresult_error(L"IDXGIFactory::EnumAdapters1", result);
        return -2;
    }
    return 0;
}

D3D12_RESOURCE_BARRIER transition_barrier(
    ID3D12Resource* resource,
    const D3D12_RESOURCE_STATES before,
    const D3D12_RESOURCE_STATES after
) {
    D3D12_RESOURCE_BARRIER barrier{};
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition.pResource = resource;
    barrier.Transition.StateBefore = before;
    barrier.Transition.StateAfter = after;
    barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    return barrier;
}

D3D12_RESOURCE_BARRIER uav_barrier(ID3D12Resource* resource) {
    D3D12_RESOURCE_BARRIER barrier{};
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV;
    barrier.UAV.pResource = resource;
    return barrier;
}

HRESULT create_buffer(
    ID3D12Device* device,
    const UINT64 size,
    const D3D12_HEAP_TYPE heap_type,
    const D3D12_RESOURCE_STATES initial_state,
    const D3D12_RESOURCE_FLAGS flags,
    ComPtr<ID3D12Resource>& output
) {
    D3D12_HEAP_PROPERTIES heap{};
    heap.Type = heap_type;
    heap.CPUPageProperty = D3D12_CPU_PAGE_PROPERTY_UNKNOWN;
    heap.MemoryPoolPreference = D3D12_MEMORY_POOL_UNKNOWN;
    heap.CreationNodeMask = 1;
    heap.VisibleNodeMask = 1;

    D3D12_RESOURCE_DESC description{};
    description.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    description.Alignment = 0;
    description.Width = std::max<UINT64>(size, 4);
    description.Height = 1;
    description.DepthOrArraySize = 1;
    description.MipLevels = 1;
    description.Format = DXGI_FORMAT_UNKNOWN;
    description.SampleDesc.Count = 1;
    description.SampleDesc.Quality = 0;
    description.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    description.Flags = flags;

    return device->CreateCommittedResource(
        &heap,
        D3D12_HEAP_FLAG_NONE,
        &description,
        initial_state,
        nullptr,
        IID_PPV_ARGS(&output)
    );
}

const char* kVirtualMachineShader = R"HLSL(
struct GhrInstruction {
    uint opcode;
    uint dst;
    uint src_a;
    uint src_b;
    uint immediate;
};

StructuredBuffer<GhrInstruction> program_buffer : register(t0);
RWStructuredBuffer<uint> data_buffer : register(u0);

cbuffer RuntimeConstants : register(b0) {
    uint instruction_count;
    uint data_word_count;
    uint lane_count;
    uint max_steps;
};

[numthreads(64, 1, 1)]
void main(uint3 dispatch_id : SV_DispatchThreadID) {
    const uint lane = dispatch_id.x;
    if (lane >= lane_count || data_word_count == 0) {
        return;
    }

    uint registers[16];
    [unroll]
    for (uint register_index = 0; register_index < 16; ++register_index) {
        registers[register_index] = 0;
    }

    uint pc = 0;
    uint steps = 0;
    bool running = true;

    [loop]
    while (running && pc < instruction_count && steps < max_steps) {
        GhrInstruction instruction = program_buffer[pc];
        const uint dst = instruction.dst & 15;
        const uint src_a = instruction.src_a & 15;
        const uint src_b = instruction.src_b & 15;
        uint next_pc = pc + 1;

        switch (instruction.opcode) {
            case 0: // NOP
                break;
            case 1: // MOV_IMM
                registers[dst] = instruction.immediate;
                break;
            case 2: // MOV_LANE
                registers[dst] = lane;
                break;
            case 3: // MOV
                registers[dst] = registers[src_a];
                break;
            case 4: // ADD
                registers[dst] = registers[src_a] + registers[src_b];
                break;
            case 5: // SUB
                registers[dst] = registers[src_a] - registers[src_b];
                break;
            case 6: // MUL_LO
                registers[dst] = registers[src_a] * registers[src_b];
                break;
            case 7: // XOR
                registers[dst] = registers[src_a] ^ registers[src_b];
                break;
            case 8: // AND
                registers[dst] = registers[src_a] & registers[src_b];
                break;
            case 9: // OR
                registers[dst] = registers[src_a] | registers[src_b];
                break;
            case 10: // SHL
                registers[dst] = registers[src_a] << (registers[src_b] & 31);
                break;
            case 11: // SHR
                registers[dst] = registers[src_a] >> (registers[src_b] & 31);
                break;
            case 12: { // LOAD
                const uint address = (registers[src_a] + instruction.immediate) % data_word_count;
                registers[dst] = data_buffer[address];
                break;
            }
            case 13: { // STORE
                const uint address = (registers[src_a] + instruction.immediate) % data_word_count;
                data_buffer[address] = registers[src_b];
                break;
            }
            case 14: // CMP_LT
                registers[dst] = registers[src_a] < registers[src_b] ? 1 : 0;
                break;
            case 15: // JNZ
                if (registers[src_a] != 0) {
                    next_pc = min(instruction.immediate, instruction_count);
                }
                break;
            case 16: // ADD_IMM
                registers[dst] = registers[src_a] + instruction.immediate;
                break;
            case 255: // HALT
                running = false;
                break;
            default:
                running = false;
                break;
        }

        pc = next_pc;
        ++steps;
    }
}
)HLSL";

class Runtime {
public:
    Runtime() = default;
    ~Runtime() {
        if (fence_event_ != nullptr) {
            CloseHandle(fence_event_);
            fence_event_ = nullptr;
        }
    }

    int initialize(const GhrRuntimeConfig& config) {
        if (config.lane_count == 0 || config.lane_count > kMaximumLanes) {
            set_error(L"Lane count must be between 1 and 1,048,576.");
            return -20;
        }
        if (config.max_steps_per_lane == 0 || config.max_steps_per_lane > kMaximumSteps) {
            set_error(L"max_steps_per_lane must be between 1 and 1,048,576.");
            return -21;
        }

        ComPtr<IDXGIAdapter1> adapter;
        const int adapter_result = get_adapter(static_cast<int>(config.adapter_index), adapter);
        if (adapter_result != 0) {
            return adapter_result;
        }

        DXGI_ADAPTER_DESC1 description{};
        const HRESULT description_result = adapter->GetDesc1(&description);
        if (FAILED(description_result)) {
            set_hresult_error(L"IDXGIAdapter::GetDesc1", description_result);
            return -3;
        }
        if ((description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) != 0) {
            set_error(L"Software DXGI adapters are not accepted by the GPU virtual-machine runtime.");
            return -22;
        }

        HRESULT result = D3D12CreateDevice(
            adapter.Get(),
            D3D_FEATURE_LEVEL_11_0,
            IID_PPV_ARGS(&device_)
        );
        if (FAILED(result)) {
            set_hresult_error(L"D3D12CreateDevice", result);
            return -23;
        }

        D3D12_COMMAND_QUEUE_DESC queue_description{};
        queue_description.Type = D3D12_COMMAND_LIST_TYPE_COMPUTE;
        queue_description.Priority = D3D12_COMMAND_QUEUE_PRIORITY_NORMAL;
        queue_description.Flags = D3D12_COMMAND_QUEUE_FLAG_NONE;
        result = device_->CreateCommandQueue(&queue_description, IID_PPV_ARGS(&queue_));
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12Device::CreateCommandQueue", result);
            return -24;
        }

        result = device_->CreateCommandAllocator(
            D3D12_COMMAND_LIST_TYPE_COMPUTE,
            IID_PPV_ARGS(&allocator_)
        );
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12Device::CreateCommandAllocator", result);
            return -25;
        }

        result = create_pipeline();
        if (FAILED(result)) {
            return -26;
        }

        result = device_->CreateCommandList(
            0,
            D3D12_COMMAND_LIST_TYPE_COMPUTE,
            allocator_.Get(),
            pipeline_.Get(),
            IID_PPV_ARGS(&command_list_)
        );
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12Device::CreateCommandList", result);
            return -27;
        }
        command_list_->Close();

        D3D12_DESCRIPTOR_HEAP_DESC heap_description{};
        heap_description.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
        heap_description.NumDescriptors = 2;
        heap_description.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
        result = device_->CreateDescriptorHeap(&heap_description, IID_PPV_ARGS(&descriptor_heap_));
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12Device::CreateDescriptorHeap", result);
            return -28;
        }
        descriptor_size_ = device_->GetDescriptorHandleIncrementSize(
            D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV
        );

        result = device_->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence_));
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12Device::CreateFence", result);
            return -29;
        }
        fence_event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (fence_event_ == nullptr) {
            set_error(L"CreateEventW failed while creating the GPU completion event.");
            return -30;
        }

        config_ = config;
        adapter_name_ = description.Description;
        return 0;
    }

    int execute(
        const GhrInstruction* instructions,
        const std::uint32_t instruction_count,
        std::uint32_t* words,
        const std::uint32_t word_count,
        double* elapsed_ms
    ) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (instructions == nullptr || instruction_count == 0) {
            set_error(L"At least one GPU virtual-ISA instruction is required.");
            return -40;
        }
        if (instruction_count > kMaximumInstructions) {
            set_error(L"The GPU virtual-ISA program exceeds 4,096 instructions.");
            return -41;
        }
        if (words == nullptr || word_count == 0) {
            set_error(L"The GPU data buffer must contain at least one 32-bit word.");
            return -42;
        }

        const UINT64 program_bytes = static_cast<UINT64>(instruction_count) * sizeof(GhrInstruction);
        const UINT64 data_bytes = static_cast<UINT64>(word_count) * sizeof(std::uint32_t);

        ComPtr<ID3D12Resource> program_upload;
        ComPtr<ID3D12Resource> program_gpu;
        ComPtr<ID3D12Resource> data_upload;
        ComPtr<ID3D12Resource> data_gpu;
        ComPtr<ID3D12Resource> data_readback;

        HRESULT result = create_buffer(
            device_.Get(), program_bytes, D3D12_HEAP_TYPE_UPLOAD,
            D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_FLAG_NONE, program_upload
        );
        if (FAILED(result)) {
            set_hresult_error(L"Create program upload buffer", result);
            return -43;
        }
        result = create_buffer(
            device_.Get(), program_bytes, D3D12_HEAP_TYPE_DEFAULT,
            D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_FLAG_NONE, program_gpu
        );
        if (FAILED(result)) {
            set_hresult_error(L"Create program GPU buffer", result);
            return -44;
        }
        result = create_buffer(
            device_.Get(), data_bytes, D3D12_HEAP_TYPE_UPLOAD,
            D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_FLAG_NONE, data_upload
        );
        if (FAILED(result)) {
            set_hresult_error(L"Create data upload buffer", result);
            return -45;
        }
        result = create_buffer(
            device_.Get(), data_bytes, D3D12_HEAP_TYPE_DEFAULT,
            D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, data_gpu
        );
        if (FAILED(result)) {
            set_hresult_error(L"Create data GPU buffer", result);
            return -46;
        }
        result = create_buffer(
            device_.Get(), data_bytes, D3D12_HEAP_TYPE_READBACK,
            D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_FLAG_NONE, data_readback
        );
        if (FAILED(result)) {
            set_hresult_error(L"Create readback buffer", result);
            return -47;
        }

        void* mapped = nullptr;
        D3D12_RANGE no_read{0, 0};
        result = program_upload->Map(0, &no_read, &mapped);
        if (FAILED(result)) {
            set_hresult_error(L"Map program upload buffer", result);
            return -48;
        }
        std::memcpy(mapped, instructions, static_cast<std::size_t>(program_bytes));
        program_upload->Unmap(0, nullptr);

        mapped = nullptr;
        result = data_upload->Map(0, &no_read, &mapped);
        if (FAILED(result)) {
            set_hresult_error(L"Map data upload buffer", result);
            return -49;
        }
        std::memcpy(mapped, words, static_cast<std::size_t>(data_bytes));
        data_upload->Unmap(0, nullptr);

        D3D12_CPU_DESCRIPTOR_HANDLE cpu_handle = descriptor_heap_->GetCPUDescriptorHandleForHeapStart();
        D3D12_SHADER_RESOURCE_VIEW_DESC srv{};
        srv.Format = DXGI_FORMAT_UNKNOWN;
        srv.ViewDimension = D3D12_SRV_DIMENSION_BUFFER;
        srv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
        srv.Buffer.FirstElement = 0;
        srv.Buffer.NumElements = instruction_count;
        srv.Buffer.StructureByteStride = sizeof(GhrInstruction);
        srv.Buffer.Flags = D3D12_BUFFER_SRV_FLAG_NONE;
        device_->CreateShaderResourceView(program_gpu.Get(), &srv, cpu_handle);

        cpu_handle.ptr += descriptor_size_;
        D3D12_UNORDERED_ACCESS_VIEW_DESC uav{};
        uav.Format = DXGI_FORMAT_UNKNOWN;
        uav.ViewDimension = D3D12_UAV_DIMENSION_BUFFER;
        uav.Buffer.FirstElement = 0;
        uav.Buffer.NumElements = word_count;
        uav.Buffer.StructureByteStride = sizeof(std::uint32_t);
        uav.Buffer.CounterOffsetInBytes = 0;
        uav.Buffer.Flags = D3D12_BUFFER_UAV_FLAG_NONE;
        device_->CreateUnorderedAccessView(data_gpu.Get(), nullptr, &uav, cpu_handle);

        result = allocator_->Reset();
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12CommandAllocator::Reset", result);
            return -50;
        }
        result = command_list_->Reset(allocator_.Get(), pipeline_.Get());
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12GraphicsCommandList::Reset", result);
            return -51;
        }

        command_list_->CopyBufferRegion(program_gpu.Get(), 0, program_upload.Get(), 0, program_bytes);
        command_list_->CopyBufferRegion(data_gpu.Get(), 0, data_upload.Get(), 0, data_bytes);
        D3D12_RESOURCE_BARRIER initial_barriers[] = {
            transition_barrier(
                program_gpu.Get(), D3D12_RESOURCE_STATE_COPY_DEST,
                D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE
            ),
            transition_barrier(
                data_gpu.Get(), D3D12_RESOURCE_STATE_COPY_DEST,
                D3D12_RESOURCE_STATE_UNORDERED_ACCESS
            ),
        };
        command_list_->ResourceBarrier(_countof(initial_barriers), initial_barriers);

        ID3D12DescriptorHeap* heaps[] = {descriptor_heap_.Get()};
        command_list_->SetDescriptorHeaps(1, heaps);
        command_list_->SetComputeRootSignature(root_signature_.Get());
        D3D12_GPU_DESCRIPTOR_HANDLE gpu_handle = descriptor_heap_->GetGPUDescriptorHandleForHeapStart();
        command_list_->SetComputeRootDescriptorTable(0, gpu_handle);
        gpu_handle.ptr += descriptor_size_;
        command_list_->SetComputeRootDescriptorTable(1, gpu_handle);
        const std::uint32_t constants[4] = {
            instruction_count,
            word_count,
            config_.lane_count,
            config_.max_steps_per_lane,
        };
        command_list_->SetComputeRoot32BitConstants(2, 4, constants, 0);
        command_list_->Dispatch((config_.lane_count + 63) / 64, 1, 1);

        D3D12_RESOURCE_BARRIER after_dispatch[] = {
            uav_barrier(data_gpu.Get()),
            transition_barrier(
                data_gpu.Get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                D3D12_RESOURCE_STATE_COPY_SOURCE
            ),
        };
        command_list_->ResourceBarrier(_countof(after_dispatch), after_dispatch);
        command_list_->CopyBufferRegion(data_readback.Get(), 0, data_gpu.Get(), 0, data_bytes);

        result = command_list_->Close();
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12GraphicsCommandList::Close", result);
            return -52;
        }

        const auto start = std::chrono::steady_clock::now();
        ID3D12CommandList* lists[] = {command_list_.Get()};
        queue_->ExecuteCommandLists(1, lists);
        const int wait_result = wait_for_gpu();
        const auto stop = std::chrono::steady_clock::now();
        if (wait_result != 0) {
            return wait_result;
        }

        const double measured_ms = std::chrono::duration<double, std::milli>(stop - start).count();
        D3D12_RANGE read_range{0, static_cast<SIZE_T>(data_bytes)};
        mapped = nullptr;
        result = data_readback->Map(0, &read_range, &mapped);
        if (FAILED(result)) {
            set_hresult_error(L"Map readback buffer", result);
            return -53;
        }
        std::memcpy(words, mapped, static_cast<std::size_t>(data_bytes));
        D3D12_RANGE no_write{0, 0};
        data_readback->Unmap(0, &no_write);

        ++executions_;
        last_instruction_count_ = instruction_count;
        last_data_words_ = word_count;
        last_elapsed_ms_ = measured_ms;
        if (elapsed_ms != nullptr) {
            *elapsed_ms = measured_ms;
        }
        return 0;
    }

    void status(GhrRuntimeStatus& output) const {
        std::lock_guard<std::mutex> lock(mutex_);
        std::memset(&output, 0, sizeof(output));
        output.active = 1;
        output.adapter_index = config_.adapter_index;
        output.lane_count = config_.lane_count;
        output.max_steps_per_lane = config_.max_steps_per_lane;
        output.executions = executions_;
        output.last_instruction_count = last_instruction_count_;
        output.last_data_words = last_data_words_;
        output.last_elapsed_ms = last_elapsed_ms_;
        wcsncpy_s(output.adapter_name, _countof(output.adapter_name), adapter_name_.c_str(), _TRUNCATE);
    }

private:
    HRESULT create_pipeline() {
        D3D12_DESCRIPTOR_RANGE ranges[2]{};
        ranges[0].RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_SRV;
        ranges[0].NumDescriptors = 1;
        ranges[0].BaseShaderRegister = 0;
        ranges[0].RegisterSpace = 0;
        ranges[0].OffsetInDescriptorsFromTableStart = 0;
        ranges[1].RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_UAV;
        ranges[1].NumDescriptors = 1;
        ranges[1].BaseShaderRegister = 0;
        ranges[1].RegisterSpace = 0;
        ranges[1].OffsetInDescriptorsFromTableStart = 0;

        D3D12_ROOT_PARAMETER parameters[3]{};
        parameters[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
        parameters[0].DescriptorTable.NumDescriptorRanges = 1;
        parameters[0].DescriptorTable.pDescriptorRanges = &ranges[0];
        parameters[0].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
        parameters[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
        parameters[1].DescriptorTable.NumDescriptorRanges = 1;
        parameters[1].DescriptorTable.pDescriptorRanges = &ranges[1];
        parameters[1].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
        parameters[2].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
        parameters[2].Constants.ShaderRegister = 0;
        parameters[2].Constants.RegisterSpace = 0;
        parameters[2].Constants.Num32BitValues = 4;
        parameters[2].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;

        D3D12_ROOT_SIGNATURE_DESC root_description{};
        root_description.NumParameters = _countof(parameters);
        root_description.pParameters = parameters;
        root_description.NumStaticSamplers = 0;
        root_description.pStaticSamplers = nullptr;
        root_description.Flags = D3D12_ROOT_SIGNATURE_FLAG_NONE;

        ComPtr<ID3DBlob> serialized;
        ComPtr<ID3DBlob> errors;
        HRESULT result = D3D12SerializeRootSignature(
            &root_description,
            D3D_ROOT_SIGNATURE_VERSION_1,
            &serialized,
            &errors
        );
        if (FAILED(result)) {
            if (errors) {
                const auto* error_text = static_cast<const char*>(errors->GetBufferPointer());
                const int length = MultiByteToWideChar(
                    CP_UTF8, 0, error_text, static_cast<int>(errors->GetBufferSize()), nullptr, 0
                );
                std::wstring wide(static_cast<std::size_t>(std::max(length, 0)), L'\0');
                if (length > 0) {
                    MultiByteToWideChar(
                        CP_UTF8, 0, error_text, static_cast<int>(errors->GetBufferSize()), wide.data(), length
                    );
                }
                set_error(L"Root signature serialization failed: " + wide);
            } else {
                set_hresult_error(L"D3D12SerializeRootSignature", result);
            }
            return result;
        }

        result = device_->CreateRootSignature(
            0,
            serialized->GetBufferPointer(),
            serialized->GetBufferSize(),
            IID_PPV_ARGS(&root_signature_)
        );
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12Device::CreateRootSignature", result);
            return result;
        }

        ComPtr<ID3DBlob> shader;
        errors.Reset();
        result = D3DCompile(
            kVirtualMachineShader,
            std::strlen(kVirtualMachineShader),
            "gpu_virtual_machine.hlsl",
            nullptr,
            nullptr,
            "main",
            "cs_5_0",
            D3DCOMPILE_OPTIMIZATION_LEVEL3 | D3DCOMPILE_ENABLE_STRICTNESS,
            0,
            &shader,
            &errors
        );
        if (FAILED(result)) {
            if (errors) {
                const auto* error_text = static_cast<const char*>(errors->GetBufferPointer());
                const int length = MultiByteToWideChar(
                    CP_UTF8, 0, error_text, static_cast<int>(errors->GetBufferSize()), nullptr, 0
                );
                std::wstring wide(static_cast<std::size_t>(std::max(length, 0)), L'\0');
                if (length > 0) {
                    MultiByteToWideChar(
                        CP_UTF8, 0, error_text, static_cast<int>(errors->GetBufferSize()), wide.data(), length
                    );
                }
                set_error(L"GPU virtual-machine shader compilation failed: " + wide);
            } else {
                set_hresult_error(L"D3DCompile", result);
            }
            return result;
        }

        D3D12_COMPUTE_PIPELINE_STATE_DESC pipeline_description{};
        pipeline_description.pRootSignature = root_signature_.Get();
        pipeline_description.CS.pShaderBytecode = shader->GetBufferPointer();
        pipeline_description.CS.BytecodeLength = shader->GetBufferSize();
        result = device_->CreateComputePipelineState(&pipeline_description, IID_PPV_ARGS(&pipeline_));
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12Device::CreateComputePipelineState", result);
        }
        return result;
    }

    int wait_for_gpu() {
        const UINT64 signal_value = ++fence_value_;
        HRESULT result = queue_->Signal(fence_.Get(), signal_value);
        if (FAILED(result)) {
            set_hresult_error(L"ID3D12CommandQueue::Signal", result);
            return -60;
        }
        if (fence_->GetCompletedValue() < signal_value) {
            result = fence_->SetEventOnCompletion(signal_value, fence_event_);
            if (FAILED(result)) {
                set_hresult_error(L"ID3D12Fence::SetEventOnCompletion", result);
                return -61;
            }
            const DWORD wait = WaitForSingleObject(fence_event_, 30000);
            if (wait != WAIT_OBJECT_0) {
                set_error(wait == WAIT_TIMEOUT
                    ? L"GPU dispatch timed out after 30 seconds."
                    : L"WaitForSingleObject failed while waiting for the GPU.");
                return wait == WAIT_TIMEOUT ? -62 : -63;
            }
        }
        return 0;
    }

    GhrRuntimeConfig config_{};
    std::wstring adapter_name_;
    ComPtr<ID3D12Device> device_;
    ComPtr<ID3D12CommandQueue> queue_;
    ComPtr<ID3D12CommandAllocator> allocator_;
    ComPtr<ID3D12GraphicsCommandList> command_list_;
    ComPtr<ID3D12RootSignature> root_signature_;
    ComPtr<ID3D12PipelineState> pipeline_;
    ComPtr<ID3D12DescriptorHeap> descriptor_heap_;
    ComPtr<ID3D12Fence> fence_;
    HANDLE fence_event_ = nullptr;
    UINT descriptor_size_ = 0;
    UINT64 fence_value_ = 0;
    std::uint64_t executions_ = 0;
    std::uint32_t last_instruction_count_ = 0;
    std::uint32_t last_data_words_ = 0;
    double last_elapsed_ms_ = 0.0;
    mutable std::mutex mutex_;
};

Runtime* as_runtime(const GhrRuntimeHandle handle) {
    return reinterpret_cast<Runtime*>(handle);
}

}  // namespace

extern "C" GHR_API std::uint32_t ghr_get_runtime_abi_version() {
    return kAbiVersion;
}

extern "C" GHR_API int ghr_get_last_error(wchar_t* output, const int output_chars) {
    if (output == nullptr || output_chars <= 0) {
        return -1;
    }
    wcsncpy_s(output, static_cast<std::size_t>(output_chars), g_last_error.c_str(), _TRUNCATE);
    return 0;
}

extern "C" GHR_API int ghr_get_adapter_count() {
    ComPtr<IDXGIFactory6> factory;
    if (create_factory(factory) != 0) {
        return -1;
    }

    int count = 0;
    while (true) {
        ComPtr<IDXGIAdapter1> adapter;
        const HRESULT result = factory->EnumAdapters1(static_cast<UINT>(count), &adapter);
        if (result == DXGI_ERROR_NOT_FOUND) {
            break;
        }
        if (FAILED(result)) {
            set_hresult_error(L"IDXGIFactory::EnumAdapters1", result);
            return -2;
        }
        ++count;
    }
    return count;
}

extern "C" GHR_API int ghr_get_adapter_info(const int index, GhrAdapterInfo* output) {
    if (output == nullptr) {
        set_error(L"Adapter output pointer is null.");
        return -10;
    }

    ComPtr<IDXGIAdapter1> adapter;
    const int adapter_result = get_adapter(index, adapter);
    if (adapter_result != 0) {
        return adapter_result;
    }

    DXGI_ADAPTER_DESC1 description{};
    const HRESULT result = adapter->GetDesc1(&description);
    if (FAILED(result)) {
        set_hresult_error(L"IDXGIAdapter::GetDesc1", result);
        return -3;
    }

    std::memset(output, 0, sizeof(GhrAdapterInfo));
    wcsncpy_s(output->name, _countof(output->name), description.Description, _TRUNCATE);
    output->dedicated_video_memory = static_cast<std::uint64_t>(description.DedicatedVideoMemory);
    output->vendor_id = description.VendorId;
    output->device_id = description.DeviceId;
    output->subsystem_id = description.SubSysId;
    output->revision = description.Revision;
    return 0;
}

extern "C" GHR_API int ghr_adapter_supports_compute(const int index) {
    ComPtr<IDXGIAdapter1> adapter;
    const int adapter_result = get_adapter(index, adapter);
    if (adapter_result != 0) {
        return adapter_result;
    }
    ComPtr<ID3D12Device> device;
    const HRESULT result = D3D12CreateDevice(
        adapter.Get(), D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&device)
    );
    if (result == DXGI_ERROR_UNSUPPORTED) {
        return 0;
    }
    if (FAILED(result)) {
        set_hresult_error(L"D3D12CreateDevice", result);
        return -1;
    }
    return 1;
}

extern "C" GHR_API int ghr_runtime_create(
    const GhrRuntimeConfig* config,
    GhrRuntimeHandle* output_handle
) {
    if (config == nullptr || output_handle == nullptr) {
        set_error(L"Runtime config and output handle are required.");
        return -70;
    }
    *output_handle = nullptr;
    std::unique_ptr<Runtime> runtime(new (std::nothrow) Runtime());
    if (!runtime) {
        set_error(L"Unable to allocate the GPU runtime object.");
        return -71;
    }
    const int result = runtime->initialize(*config);
    if (result != 0) {
        return result;
    }
    *output_handle = runtime.release();
    return 0;
}

extern "C" GHR_API int ghr_runtime_destroy(const GhrRuntimeHandle handle) {
    if (handle == nullptr) {
        return 0;
    }
    delete as_runtime(handle);
    return 0;
}

extern "C" GHR_API int ghr_runtime_get_status(
    const GhrRuntimeHandle handle,
    GhrRuntimeStatus* output
) {
    if (handle == nullptr || output == nullptr) {
        set_error(L"A valid runtime handle and status output are required.");
        return -72;
    }
    as_runtime(handle)->status(*output);
    return 0;
}

extern "C" GHR_API int ghr_runtime_execute(
    const GhrRuntimeHandle handle,
    const GhrInstruction* instructions,
    const std::uint32_t instruction_count,
    std::uint32_t* input_output_words,
    const std::uint32_t data_word_count,
    double* elapsed_ms
) {
    if (handle == nullptr) {
        set_error(L"The GPU runtime has not been created.");
        return -73;
    }
    return as_runtime(handle)->execute(
        instructions,
        instruction_count,
        input_output_words,
        data_word_count,
        elapsed_ms
    );
}

extern "C" GHR_API int ghr_runtime_self_test(
    const int adapter_index,
    std::uint32_t lane_count,
    GhrSelfTestResult* output
) {
    if (output == nullptr) {
        set_error(L"Self-test output pointer is null.");
        return -80;
    }
    std::memset(output, 0, sizeof(GhrSelfTestResult));
    lane_count = std::clamp<std::uint32_t>(lane_count, 1, 65536);

    GhrRuntimeConfig config{};
    config.adapter_index = static_cast<std::uint32_t>(std::max(adapter_index, 0));
    config.lane_count = lane_count;
    config.max_steps_per_lane = 256;

    GhrRuntimeHandle handle = nullptr;
    int result = ghr_runtime_create(&config, &handle);
    if (result != 0) {
        wcsncpy_s(output->message, _countof(output->message), g_last_error.c_str(), _TRUNCATE);
        return result;
    }

    const GhrInstruction program[] = {
        {GHR_OP_MOV_LANE, 0, 0, 0, 0},
        {GHR_OP_MOV_IMM, 1, 0, 0, 3},
        {GHR_OP_MUL_LO, 2, 0, 1, 0},
        {GHR_OP_ADD_IMM, 2, 2, 0, 1},
        {GHR_OP_STORE, 0, 0, 2, 0},
        {GHR_OP_HALT, 0, 0, 0, 0},
    };
    std::vector<std::uint32_t> words(lane_count, 0);
    double elapsed = 0.0;
    result = ghr_runtime_execute(
        handle,
        program,
        static_cast<std::uint32_t>(_countof(program)),
        words.data(),
        static_cast<std::uint32_t>(words.size()),
        &elapsed
    );

    std::uint32_t mismatches = 0;
    if (result == 0) {
        for (std::uint32_t lane = 0; lane < lane_count; ++lane) {
            const std::uint32_t expected = lane * 3 + 1;
            if (words[lane] != expected) {
                ++mismatches;
            }
        }
    }

    output->passed = result == 0 && mismatches == 0 ? 1u : 0u;
    output->lane_count = lane_count;
    output->mismatches = mismatches;
    output->elapsed_ms = elapsed;
    if (output->passed) {
        wcsncpy_s(
            output->message,
            _countof(output->message),
            L"D3D12 GPU virtual-ISA dispatch and readback passed.",
            _TRUNCATE
        );
    } else if (result != 0) {
        wcsncpy_s(output->message, _countof(output->message), g_last_error.c_str(), _TRUNCATE);
    } else {
        wcsncpy_s(
            output->message,
            _countof(output->message),
            L"GPU dispatch completed, but one or more lane outputs were incorrect.",
            _TRUNCATE
        );
    }

    ghr_runtime_destroy(handle);
    return result == 0 && mismatches == 0 ? 0 : (result != 0 ? result : -81);
}
