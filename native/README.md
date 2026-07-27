# Optional native runtime

`gpu_host_runtime.dll` is a small x64 Windows DLL used to enumerate DXGI
display adapters. It is not a GPU CPU emulator and does not execute XMRig.

Build from an x64 Visual Studio developer shell:

```powershell
cd <project-root>
.\build_native.ps1
```
