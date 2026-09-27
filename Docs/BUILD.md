# Building `dlss5_host.exe`

## Toolchain

- Visual Studio 2022 **Build Tools**, workload "Desktop development with C++":
  - MSVC v143 x64
  - Windows 11 SDK (10.0.22621 or newer), for D3D11/DXGI/d3dcompiler headers
- No other dependencies. The ReShade 6.8.0 add-on API headers are vendored in `host/third_party/reshade/include` (tag `v6.8.0` of crosire/reshade).

To install the Build Tools from the command line:
```
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.Windows11SDK.22621"
```

## Build

```
host\build.bat
```
This runs `vcvars64.bat`, then:
```
cl /O2 /EHsc /std:c++17 /utf-8 /Ithird_party\reshade\include dlss5_host.cpp /Fe:..\runtime\dlss5_host.exe
```
The exe **must** sit in `runtime\`, next to ReShade's `dxgi.dll`. That's how ReShade is loaded into it.

## What the helper is

A single-file D3D11 program (`host/dlss5_host.cpp`, about 600 lines) that:

1. Creates a window + swap chain the size of the image. The format is R16G16B16A16_FLOAT with `--hdr 1` (scRGB), otherwise R8G8B8A8_UNORM.
2. Draws the colour and writes a **real depth buffer** from Arnold's Z (reversed-Z `near/Z`), so ReShade's generic depth detection hands it to the DLSS 5 feed exactly as it would a game's depth.
3. **Registers itself as a ReShade add-on** (the exe exports `NAME`/`DESCRIPTION`):
   - `reshade_begin_effects`: uploads Arnold motion vectors into the feed's `texMotionVectors` (RG16F delta UV)
   - `reshade_finish_effects`: copies the back buffer after the DLSS 5 pass and before ReShade's overlay/banner. This is the captured output.
4. Processes a list of frames in one session (`--list`), one evaluation per frame, with a short warm-up on the first.

Run `dlss5_host.exe` with no arguments to see all options. They're also documented at the top of the source.
