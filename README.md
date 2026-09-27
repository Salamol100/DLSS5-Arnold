# DLSS5 Arnold!

**DLSS 5 neural rendering for Arnold renders in Maya 2024**

![Arnold render (left) vs DLSS 5 neural rendering (right)](Docs/images/hero_face.png)

*Left: Arnold render. Right: the same frame after DLSS 5 neural rendering (strength 0.98, 2 passes, +0.75 exposure), same resolution.*

**Passes:** original, then 1, 2 and 3 passes. 2 is the sweet spot; 3 starts to age the face.

![Original, 1, 2 and 3 passes](Docs/images/passes_face.png)

**Stylised characters** get pulled toward photoreal too (strength 0.98, 1 pass):

![Stylised head: Arnold (left) vs DLSS 5 (right)](Docs/images/before_after_face.png)

Runs NVIDIA's **DLSS 5 neural rendering** (NGX feature 18, the "photoreal" pass) on **Arnold renders from Maya**. Output is the **same resolution** as the input; nothing is upscaled.

- **What it's good at:** photoreal re-rendering of **characters and faces**. Skin, eyes and lips get the same transformation you see in games that use DLSS 5.
- **What it's not:** a general detail enhancer. On environments and props it applies a mild relight and softens the image slightly. See [Docs/TEST_RESULTS.md](Docs/TEST_RESULTS.md) for the measurements.
- **Status:** experimental proof of concept. Unofficial, and not supported by NVIDIA.

## How it works

```
Maya scene --(export .ass.gz, + Z AOV, + motionvector AOV)--> Arnold kick --> linear EXR
   EXR --(ACEScg -> scRGB, depth -> reversed-Z, motion vectors -> delta UV)--> dlss5_host.exe
   dlss5_host.exe = tiny D3D11 app + ReShade add-on:
        ReShade + dlss5-feed + renodx-dlss5 (your own install) run DLSS/DLAA + DLSS 5 NR on it
   result (16-bit float) --(stabilise along motion vectors, restore out-of-gamut)--> EXR / PNG16 / PNG8
```

Maya never loads ReShade. The DLSS work happens in a separate helper process (`runtime/dlss5_host.exe`), so a crash there can't take Maya down.

## Requirements

- Windows 10/11, an **NVIDIA RTX GPU** that runs the DLSS 5 NR runtime (tested on an RTX 2080 SUPER, driver 616.56)
- Maya 2024 + MtoA (Arnold 7.3)
- A working **DLSS 5 ReShade setup from a game**. This repo does **not** include it (see below).
- To build the helper: Visual Studio 2022 Build Tools (MSVC v143) + Windows 11 SDK

## Setup

1. **Build the helper.** Run `host\build.bat`, which writes `runtime\dlss5_host.exe`. See [Docs/BUILD.md](Docs/BUILD.md).
2. **Copy your own DLSS 5 runtime into `runtime\`.** These files are *not* included (proprietary or third-party). To get them, **download a DLSS 5 wrapper**: the DLSS5-Feeder + RenoDX DLSS5 setup people use to run DLSS 5 in games. Copy these files from it:

   | File | From |
   |---|---|
   | `dxgi.dll` | ReShade 6.8 (with add-on support) |
   | `dlss5-feed.addon64`, `dlss5-feed.cfg` | DLSS5-Feeder |
   | `renodx-dlss5.addon64` | RenoDX DLSS5 |
   | `dlss5-lab-overlay-*.addon64` | optional |
   | `nvngx_dlss.dll`, `nvngx_dlssnr.dll` | NVIDIA DLSS / DLSS 5 NR runtime (310.x) |
   | `reshade-shaders\Shaders\DLSS5_Feed.fx`, `ReShade.fxh`, `ReShadeUI.fxh` | DLSS5-Feeder / ReShade |

   Then start from `runtime\ReShade.ini.template` and `runtime\ReShadePreset.ini.template` (rename them to drop `.template`).
3. **Add the Maya shelf.** Paste `maya\install_shelf.py` into Maya's Script Editor (Python tab) once, or add it to your `userSetup.py`.

## Using it (DLSS5 shelf)

| Button | Does |
|---|---|
| **Panel** | Strength, Structure, Style, output folder, output format, stabilisation, frame range, EXR sequence |
| **Render** | The current frame: Arnold → DLSS 5 → Render View. Writes a 16-bit PNG. |
| **Seq** | Pick any frame of a rendered EXR sequence (with a `Z` AOV) → DLSS 5 on the whole sequence |
| **Out** | Opens `<project>/images/dlss5` |

- **Strength:** 0.25 keeps a stylised character's design and adds realistic skin. 0.98 is a full photoreal re-interpretation.
- **Passes** (1–3): runs DLSS 5 again on its own output. **2 is the strong setting** on faces (visible pores, stubble, deeper form). 3 overcooks: faces age and drift from the design, and environments soften more.
- **Exposure** (−2 to +2 stops): brightens or darkens the DLSS 5 output. Extra passes darken faces slightly, and +0.5 to +1 compensates.
- **Frame ranges** use Arnold's exact motion vectors plus temporal stabilisation, which gives about 30% less "wobble" on faces.
- **Output:** Auto (single frame → PNG 16-bit, ranges → half-float EXR in the rendering space + 8-bit preview), or EXR / PNG16 / PNG8.
- **Debug images** (Panel checkbox, off by default): per frame, `compare` (original | DLSS 5), `diff` (where DLSS 5 changed things locally, with the overall colour shift removed) and `inputs` (what DLSS 5 received: colour | depth | motion vectors). They're written to `output/debug`.
- **Disk:** frames are processed in chunks sized to your free space, and temp files are deleted per frame.

## Batch / render farm

The helper must open a window on the GPU. It works from `mayapy` or from a Deadline Worker running **in a logged-in desktop session**, but **not** from a Worker running as a Windows service (session 0). Scripting API: `dlss5_enhance.process_scene_frames(...)`, `process_exr_sequence(...)`.

## Docs

- [Docs/DLSS5_ENHANCED_DETAIL_ANALYSIS.md](Docs/DLSS5_ENHANCED_DETAIL_ANALYSIS.md): what the DLSS 5 stack is and why this design
- [Docs/MAYA_ARNOLD_INTEGRATION.md](Docs/MAYA_ARNOLD_INTEGRATION.md): pipeline details
- [Docs/INPUT_REQUIREMENTS.md](Docs/INPUT_REQUIREMENTS.md): colour, depth and motion-vector contract
- [Docs/BUILD.md](Docs/BUILD.md): building the helper
- [Docs/TEST_RESULTS.md](Docs/TEST_RESULTS.md): all measurements

## Author

**Biscuit Beetle**: concept, direction, testing and all the art (the Arnold scenes, characters and ammonites used for the tests). Biscuit Beetle drove the key discoveries: that the effect lives in the character path, the Fallout 4 comparisons, and the 16-bit and wobble investigations.
Implementation was written with Claude (Anthropic) as a coding assistant.

## Licence and credits

The code in this repository is © Biscuit Beetle, released under the [MIT licence](LICENSE).
`host/third_party/reshade/include` holds the ReShade 6.8.0 add-on API headers (BSD-3-Clause OR MIT, © Patrick Mours; see their `LICENSE.md`).
DLSS, NGX and the DLSS 5 runtime are NVIDIA's. ReShade, DLSS5-Feeder, RenoDX and LumeniteFX belong to their respective authors. None of their binaries or shaders are included here.
