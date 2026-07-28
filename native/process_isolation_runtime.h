#pragma once

#include <cstdint>

#if defined(_WIN32)
#  if defined(PROCESS_ISOLATION_RUNTIME_EXPORTS)
#    define PIR_API extern "C" __declspec(dllexport)
#  else
#    define PIR_API extern "C" __declspec(dllimport)
#  endif
#else
#  define PIR_API extern "C"
#endif

PIR_API std::uint32_t pir_get_abi_version();
PIR_API int pir_apply_isolation(
    std::uint32_t process_id,
    std::uint64_t affinity_mask,
    int priority_level,
    int enable_eco_qos,
    int suspend_during_apply,
    wchar_t* message,
    std::uint32_t message_chars
);
PIR_API int pir_release_process(std::uint32_t process_id);
PIR_API int pir_get_last_error(wchar_t* buffer, std::uint32_t buffer_chars);
