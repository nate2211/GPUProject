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

// A compact GPU virtual-ISA instruction. All fields are 32-bit so the ABI is
// stable for ctypes and other FFI callers.
struct GhrInstruction {
    std::uint32_t opcode;
    std::uint32_t dst;
    std::uint32_t src_a;
    std::uint32_t src_b;
    std::uint32_t immediate;
};

enum GhrOpcode : std::uint32_t {
    GHR_OP_NOP = 0,
    GHR_OP_MOV_IMM = 1,
    GHR_OP_MOV_LANE = 2,
    GHR_OP_MOV = 3,
    GHR_OP_ADD = 4,
    GHR_OP_SUB = 5,
    GHR_OP_MUL_LO = 6,
    GHR_OP_XOR = 7,
    GHR_OP_AND = 8,
    GHR_OP_OR = 9,
    GHR_OP_SHL = 10,
    GHR_OP_SHR = 11,
    GHR_OP_LOAD = 12,
    GHR_OP_STORE = 13,
    GHR_OP_CMP_LT = 14,
    GHR_OP_JNZ = 15,
    GHR_OP_ADD_IMM = 16,
    GHR_OP_HALT = 255,
};

struct GhrRuntimeConfig {
    std::uint32_t adapter_index;
    std::uint32_t lane_count;
    std::uint32_t max_steps_per_lane;
    std::uint32_t flags;
};

struct GhrRuntimeStatus {
    std::uint32_t active;
    std::uint32_t adapter_index;
    std::uint32_t lane_count;
    std::uint32_t max_steps_per_lane;
    std::uint64_t executions;
    std::uint32_t last_instruction_count;
    std::uint32_t last_data_words;
    double last_elapsed_ms;
    wchar_t adapter_name[128];
};

struct GhrSelfTestResult {
    std::uint32_t passed;
    std::uint32_t lane_count;
    std::uint32_t mismatches;
    double elapsed_ms;
    wchar_t message[256];
};

using GhrRuntimeHandle = void*;

// Returns the ABI version encoded as 0xMMMMmmmm (major/minor).
GHR_API std::uint32_t ghr_get_runtime_abi_version();

// Copies the thread-local diagnostic text into the caller's buffer.
GHR_API int ghr_get_last_error(wchar_t* output, int output_chars);

// Returns the number of DXGI adapters, or a negative error code.
GHR_API int ghr_get_adapter_count();

// Returns 0 on success. The caller owns the output structure.
GHR_API int ghr_get_adapter_info(int index, GhrAdapterInfo* output);

// Returns 1 when a Direct3D 12 compute device can be created for the adapter,
// 0 when unsupported, and a negative value on an API failure.
GHR_API int ghr_adapter_supports_compute(int index);

// Creates a persistent D3D12 GPU virtual-machine runtime.
GHR_API int ghr_runtime_create(
    const GhrRuntimeConfig* config,
    GhrRuntimeHandle* output_handle
);

GHR_API int ghr_runtime_destroy(GhrRuntimeHandle handle);
GHR_API int ghr_runtime_get_status(GhrRuntimeHandle handle, GhrRuntimeStatus* output);

// Executes one shared virtual-ISA program across lane_count GPU shader lanes.
// input_output_words is copied to GPU VRAM, mutated by the program, and copied
// back only after the dispatch completes. The buffer must contain at least one
// word and may contain per-lane or shared data chosen by the program.
GHR_API int ghr_runtime_execute(
    GhrRuntimeHandle handle,
    const GhrInstruction* instructions,
    std::uint32_t instruction_count,
    std::uint32_t* input_output_words,
    std::uint32_t data_word_count,
    double* elapsed_ms
);

// Runs a deterministic lane arithmetic test through the same GPU virtual ISA.
GHR_API int ghr_runtime_self_test(
    int adapter_index,
    std::uint32_t lane_count,
    GhrSelfTestResult* output
);

}
