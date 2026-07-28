param(
    [switch]$SkipSelfTest,
    [int]$SelfTestAdapter = 0,
    [int]$SelfTestLanes = 4096
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw "CMake was not found. Install CMake and Visual Studio 2022 Build Tools with the Desktop development with C++ workload and a Windows 10/11 SDK."
}

cmake -S native -B native/build -A x64
cmake --build native/build --config Release --clean-first

New-Item -ItemType Directory -Force native/bin | Out-Null
Copy-Item native/build/Release/gpu_host_runtime.dll native/bin/gpu_host_runtime.dll -Force
Copy-Item native/build/Release/process_isolation_runtime.dll native/bin/process_isolation_runtime.dll -Force

$pdb = "native/build/Release/gpu_host_runtime.pdb"
if (Test-Path $pdb) {
    Copy-Item $pdb native/bin/gpu_host_runtime.pdb -Force
}
$isolationPdb = "native/build/Release/process_isolation_runtime.pdb"
if (Test-Path $isolationPdb) {
    Copy-Item $isolationPdb native/bin/process_isolation_runtime.pdb -Force
}

Write-Host "Built native/bin/gpu_host_runtime.dll (D3D12 GPU virtual-machine ABI 4.0)"
Write-Host "Built native/bin/process_isolation_runtime.dll (early XMRig affinity/priority Job Object ABI 4.1)"

if (-not $SkipSelfTest) {
    $python = Join-Path $PSScriptRoot ".venv/Scripts/python.exe"
    if (-not (Test-Path $python)) {
        $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    }
    if ($python) {
        & $python scripts/native_isolation_selftest.py
        if ($LASTEXITCODE -ne 0) {
            throw "The process isolation DLL built, but its Windows affinity/Job Object self-test failed."
        }
        & $python scripts/native_selftest.py --adapter $SelfTestAdapter --lanes $SelfTestLanes
        if ($LASTEXITCODE -ne 0) {
            throw "The DLL built, but the D3D12 GPU self-test failed. Review the adapter index and driver support."
        }
    } else {
        Write-Warning "Python was not found, so the post-build GPU self-test was skipped."
    }
}
