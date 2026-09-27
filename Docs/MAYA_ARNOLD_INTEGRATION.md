# Maya / Arnold integration

## Why a separate helper process

The DLSS 5 stack only runs inside ReShade, which gets in as `dxgi.dll` next to an exe. Loading it into Maya would hook Maya's viewport and risk crashes. So `maya/dlss5_enhance.py` (Python, in Maya) drives `runtime/dlss5_host.exe` (native, D3D11 + ReShade add-on) as a subprocess. It runs on a background thread, so Maya stays responsive.

## Per job (`process_scene_frames(start, end, out_dir, ...)`)

1. **Export (main thread):** `arnoldExportAss`, one compressed `.ass.gz` per frame, full node mask (keeps the denoiser imager and the OCIO colour manager). Temporarily added, then restored:
   - a `Z` AOV, a merged float EXR driver
   - for ranges: a `motionvector` AOV (`aiMotionVector`, raw), motion blur *End On Frame* over 1 frame, camera shutter 1..1
   - an extra PNG driver with the scene's output transform, used for the "original" image

   The user's scene is left unchanged.
2. **Render:** `kick` per frame (GPU if the scene is set to GPU, with automatic CPU fallback). Each `.ass.gz` is deleted after rendering.
3. **Prepare:** EXR → scRGB colour PFM, `Z` PFM and motion-vector PFM. Our own EXR is deleted once prepared; an existing sequence's EXRs are never touched.
4. **DLSS 5:** `dlss5_host.exe --list frames.txt --hdr 1 --per-frame 1`, with one evaluation per frame. It's guarded by `runtime/host.lock`, so only one helper runs at a time across Maya and batch processes.
5. **Finish (per frame):** restore out-of-gamut colour, stabilise the DLSS edit along the motion vectors (ranges, strength 0.8), and write the output:
   - EXR (half, rendering space) + 8-bit preview, or
   - PNG 16/8 through the ACES view (Hill fit, 0.003 from Arnold's own)

   Then delete the frame's temp files.
6. **Chunking:** after the first frame, the per-frame temp size is measured and frames are processed in chunks using at most half the free disk space. Stabilisation carries across chunks.
7. **Housekeeping:** work folders from dead runs are removed at the start of each run. A run's folder is protected by `owner.pid` while its process is alive.

## Existing EXR sequences (`process_exr_sequence`)

Same steps 3–5 without rendering. The EXRs need a `Z` channel (flat depth is allowed but less reliable). Motion vectors aren't used for foreign sequences, because their convention is unknown.

## UI

`show()` opens the panel. `quick_frame()`, `quick_sequence()` and `open_output()` are the one-click shelf actions. `maya/install_shelf.py` creates the DLSS5 shelf.

## Maya command port (optional)

`tools/maya_port.py <port> <file-or-code>` sends Python to a running Maya (`commandPort -n ":6111" -sourceType "python"`). The test and diagnostic scripts in `tools/` use it.
