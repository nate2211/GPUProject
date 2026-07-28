#include "process_isolation_runtime.h"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <tlhelp32.h>

#include <algorithm>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace {
constexpr std::uint32_t kAbiVersion = 0x00040100;
thread_local std::wstring g_last_error;
std::mutex g_job_mutex;
std::unordered_map<DWORD, HANDLE> g_jobs;

void set_last_error(const std::wstring& text) {
    g_last_error = text;
}

std::wstring win32_error(const wchar_t* prefix, DWORD code = ::GetLastError()) {
    wchar_t* system_text = nullptr;
    const DWORD flags = FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                        FORMAT_MESSAGE_IGNORE_INSERTS;
    const DWORD length = ::FormatMessageW(
        flags,
        nullptr,
        code,
        MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        reinterpret_cast<wchar_t*>(&system_text),
        0,
        nullptr
    );
    std::wstring result(prefix);
    result += L" (" + std::to_wstring(code) + L")";
    if (length > 0 && system_text != nullptr) {
        std::wstring detail(system_text, length);
        while (!detail.empty() && (detail.back() == L'\r' || detail.back() == L'\n' || detail.back() == L' ')) {
            detail.pop_back();
        }
        result += L": " + detail;
        ::LocalFree(system_text);
    }
    return result;
}

void copy_message(const std::wstring& text, wchar_t* output, std::uint32_t chars) {
    if (output == nullptr || chars == 0) {
        return;
    }
    const std::size_t count = std::min<std::size_t>(text.size(), chars - 1);
    std::copy_n(text.c_str(), count, output);
    output[count] = L'\0';
}

DWORD priority_class_from_level(int level) {
    switch (level) {
        case 0: return IDLE_PRIORITY_CLASS;
        case 1: return BELOW_NORMAL_PRIORITY_CLASS;
        case 2: return NORMAL_PRIORITY_CLASS;
        case 3: return ABOVE_NORMAL_PRIORITY_CLASS;
        case 4: return HIGH_PRIORITY_CLASS;
        default: return IDLE_PRIORITY_CLASS;
    }
}

std::vector<HANDLE> suspend_process_threads(DWORD pid) {
    std::vector<HANDLE> suspended;
    HANDLE snapshot = ::CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return suspended;
    }

    THREADENTRY32 entry{};
    entry.dwSize = sizeof(entry);
    if (::Thread32First(snapshot, &entry)) {
        do {
            if (entry.th32OwnerProcessID != pid) {
                continue;
            }
            HANDLE thread = ::OpenThread(THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION, FALSE, entry.th32ThreadID);
            if (thread == nullptr) {
                continue;
            }
            if (::SuspendThread(thread) == static_cast<DWORD>(-1)) {
                ::CloseHandle(thread);
                continue;
            }
            suspended.push_back(thread);
        } while (::Thread32Next(snapshot, &entry));
    }
    ::CloseHandle(snapshot);
    return suspended;
}

void resume_process_threads(std::vector<HANDLE>& threads) {
    for (auto it = threads.rbegin(); it != threads.rend(); ++it) {
        ::ResumeThread(*it);
        ::CloseHandle(*it);
    }
    threads.clear();
}

bool apply_eco_qos(HANDLE process, bool enabled, std::wstring& warning) {
#if defined(PROCESS_POWER_THROTTLING_CURRENT_VERSION) && defined(PROCESS_POWER_THROTTLING_EXECUTION_SPEED)
    PROCESS_POWER_THROTTLING_STATE state{};
    state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION;
    state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED;
    state.StateMask = enabled ? PROCESS_POWER_THROTTLING_EXECUTION_SPEED : 0;
    if (!::SetProcessInformation(
            process,
            ProcessPowerThrottling,
            &state,
            sizeof(state))) {
        warning = win32_error(L"EcoQoS was not applied");
        return false;
    }
    return true;
#else
    (void)process;
    (void)enabled;
    warning = L"EcoQoS is unavailable in this Windows SDK build";
    return false;
#endif
}

bool attach_job(HANDLE process, DWORD pid, std::uint64_t affinity_mask, DWORD priority_class, std::wstring& warning) {
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
                                              JOB_OBJECT_LIMIT_PRIORITY_CLASS;
    limits.BasicLimitInformation.PriorityClass = priority_class;
    if (affinity_mask != 0) {
        limits.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_AFFINITY;
        limits.BasicLimitInformation.Affinity = static_cast<ULONG_PTR>(affinity_mask);
    }

    {
        std::scoped_lock lock(g_job_mutex);
        auto existing = g_jobs.find(pid);
        if (existing != g_jobs.end()) {
            if (!::SetInformationJobObject(
                    existing->second,
                    JobObjectExtendedLimitInformation,
                    &limits,
                    sizeof(limits))) {
                warning = win32_error(L"Updating the isolation Job Object failed");
                return false;
            }
            return true;
        }
    }

    HANDLE job = ::CreateJobObjectW(nullptr, nullptr);
    if (job == nullptr) {
        warning = win32_error(L"CreateJobObjectW failed");
        return false;
    }

    if (!::SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits,
            sizeof(limits))) {
        warning = win32_error(L"SetInformationJobObject failed");
        ::CloseHandle(job);
        return false;
    }

    if (!::AssignProcessToJobObject(job, process)) {
        warning = win32_error(L"AssignProcessToJobObject failed; direct process controls remain active");
        ::CloseHandle(job);
        return false;
    }

    std::scoped_lock lock(g_job_mutex);
    g_jobs.emplace(pid, job);
    return true;
}
} // namespace

std::uint32_t pir_get_abi_version() {
    return kAbiVersion;
}

int pir_apply_isolation(
    std::uint32_t process_id,
    std::uint64_t affinity_mask,
    int priority_level,
    int enable_eco_qos,
    int suspend_during_apply,
    wchar_t* message,
    std::uint32_t message_chars
) {
    g_last_error.clear();
    const DWORD pid = static_cast<DWORD>(process_id);
    HANDLE process = ::OpenProcess(
        PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA |
            PROCESS_TERMINATE | SYNCHRONIZE,
        FALSE,
        pid
    );
    if (process == nullptr) {
        set_last_error(win32_error(L"OpenProcess failed"));
        copy_message(g_last_error, message, message_chars);
        return -1;
    }

    std::vector<HANDLE> suspended;
    if (suspend_during_apply != 0) {
        suspended = suspend_process_threads(pid);
    }

    int result = 0;
    std::vector<std::wstring> notes;
    const DWORD priority_class = priority_class_from_level(priority_level);

    if (affinity_mask != 0) {
        if (!::SetProcessAffinityMask(process, static_cast<DWORD_PTR>(affinity_mask))) {
            set_last_error(win32_error(L"SetProcessAffinityMask failed"));
            result = -2;
        } else {
            notes.emplace_back(L"affinity applied before RandomX initialization");
        }
    }

    if (result == 0) {
        if (!::SetPriorityClass(process, priority_class)) {
            set_last_error(win32_error(L"SetPriorityClass failed"));
            result = -3;
        } else {
            notes.emplace_back(L"priority class applied");
        }
    }

    if (result == 0) {
        std::wstring warning;
        const bool eco_enabled = enable_eco_qos != 0;
        if (apply_eco_qos(process, eco_enabled, warning)) {
            notes.emplace_back(eco_enabled ? L"EcoQoS enabled" : L"EcoQoS disabled");
        } else if (!warning.empty()) {
            notes.emplace_back(warning);
        }
    }

    if (result == 0) {
        std::wstring warning;
        if (attach_job(process, pid, affinity_mask, priority_class, warning)) {
            notes.emplace_back(L"persistent isolation Job Object attached");
        } else if (!warning.empty()) {
            notes.emplace_back(warning);
        }
    }

    resume_process_threads(suspended);
    ::CloseHandle(process);

    if (result != 0) {
        copy_message(g_last_error, message, message_chars);
        return result;
    }

    std::wstring summary = L"native isolation: ";
    for (std::size_t index = 0; index < notes.size(); ++index) {
        if (index != 0) {
            summary += L", ";
        }
        summary += notes[index];
    }
    if (notes.empty()) {
        summary += L"no optional controls requested";
    }
    copy_message(summary, message, message_chars);
    return 0;
}

int pir_release_process(std::uint32_t process_id) {
    std::scoped_lock lock(g_job_mutex);
    const DWORD pid = static_cast<DWORD>(process_id);
    auto found = g_jobs.find(pid);
    if (found == g_jobs.end()) {
        return 0;
    }
    ::CloseHandle(found->second);
    g_jobs.erase(found);
    return 0;
}

int pir_get_last_error(wchar_t* buffer, std::uint32_t buffer_chars) {
    copy_message(g_last_error, buffer, buffer_chars);
    return static_cast<int>(g_last_error.size());
}
