# DLSS 5 Enhanced Detail on Arnold renders: feasibility analysis

Date: 2026-09-27 (revised after inspecting the user's working install)

**Verdict: B + C. Feasible with a custom D3D12 host, fed with Arnold beauty + depth (+ motion vectors for animation).**

An earlier version of this file said "D: not feasible on RTX 2080 Super". That was wrong. The local logs prove that feature 18 runs on this GPU.

## 1. The stack that is actually running (D:\Games\Fallout 4 GOTY; also Skyrim SE)

| File | Version | Role |
|---|---|---|
| `dlss5-feed.addon64` + `reshade-shaders\Shaders\DLSS5_Feed.fx` | 0.14.0.0 | Grabs colour/depth/motion vectors through ReShade and creates a **DLAA** (SuperSampling, 1:1) NGX feature |
| `renodx-dlss5.addon64` | 0.2026.0828.0517 | Hooks that DLSS/DLAA evaluation and creates **NGX feature 18** (neural rendering) chained after it |
| `dlss5-lab-overlay-*.addon64` | n/a | UI for the settings (Structure Intensity, Global Tone Intensity, NR on/off) |
| `nvngx_dlssnr.dll` | 310.8.0.0 "NVIDIA DLSSNR – DVS PRODUCTION" | Neural rendering runtime (installed next to the game, not in the driver store) |
| `nvngx_dlss.dll` | 310.8.0.0 | DLSS/DLAA runtime |

This is **not** NIGos/dlss5-bridge. That bridge is a similar relay design, but it is not what is installed here.

## 2. Evidence that it runs on RTX 2080 Super (Turing, driver 616.56)

`ReShade.log`:
```
[DLSS 5 Neural Rendering] DLSS5 Generic: feature 18 created via the signed snippet after DLSS/DLAA
  for NR input 1920x1080 -> output 1920x1080 with guides 1920x1080
[DLSS 5 Neural Rendering] NR screenshot pair written: ...NR_CAPTURE_..._NR_OFF.png and ...NR_CAPTURE_....png
```
`dlss5-feed.log`: GPU cost is about 42 ms/frame at 1080p with NR on, and about 1.4 ms with it off. The public NGX capability query for feature 18 returns `0xBAD00012 NotImplemented`. The feature is created anyway through RenoDX's own path.

## 3. Input contract as observed

- **Same resolution in and out.** NR input 1920×1080 → output 1920×1080, "guides" at 1920×1080. No upscaling is needed.
- **NR is chained after a DLSS/DLAA evaluation.** It reuses that evaluation's inputs:
  - Colour: R8G8B8A8_UNORM (SDR), or HDR float
  - Depth: R32_FLOAT, reversed flag passed (`DepthInverted`)
  - Motion vectors: R16G16_FLOAT, `MVLowRes`
  - Flags seen: `74 = SDR | MVLowRes | DepthInverted | AutoExposure`; jitter 0, pre-exposure 1
- **Depth:** the feed warns loudly when depth is flat ("DLSS and the neural pass get no depth"). Treat real depth as required.
- **Motion vectors:** the log shows NR running with **all-zero MVs** (`mean |mv| 0.000 px`). So zero MVs are accepted, which makes a still frame viable. Temporal stability over animation needs real MVs.
- **Exposed controls:** Enable Neural Rendering, Structure Intensity, Global Tone Intensity.
- **Unknown:** feature 18's own NGX parameter names. They are inside RenoDX ("signed snippet"). I will not invent them. So the design **hosts the existing add-on stack** rather than calling feature 18 directly.

## 4. Consequence for the Maya design

No publicly documented NGX parameters exist for feature 18. The honest, non-invented route is:

> A tiny isolated D3D12 host app that shows the Arnold frame as its back buffer and exposes Arnold Z as depth. ReShade + dlss5-feed + renodx-dlss5 are loaded unmodified from a **project-local copy** of the user's own files (nothing global, Fallout 4 untouched). The app drives N evaluations, then reads the output back to EXR/PNG.

Arnold supplies: beauty (colour), `Z` AOV (converted to reversed 0..1 depth), and `motionvector` AOV for animation (zeros for a still).
