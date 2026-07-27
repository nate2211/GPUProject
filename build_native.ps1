$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw "CMake was not found. Install CMake and Visual Studio Build Tools."
}

cmake -S native -B native/build -A x64
cmake --build native/build --config Release

New-Item -ItemType Directory -Force native/bin | Out-Null
Copy-Item native/build/Release/gpu_host_runtime.dll native/bin/gpu_host_runtime.dll -Force

Write-Host "Built native/bin/gpu_host_runtime.dll"
