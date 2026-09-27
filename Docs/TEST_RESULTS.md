# Test results

> **Correction (2026-09-28): Tests 1 and 2 ran on non-denoised, non-ACES input.**
> The `.ass` export used `mask=255`, which drops imagers, so the OIDN denoiser link was cut.
> The host also encoded ACEScg-linear values with a plain sRGB curve instead of the scene's
> "ACES 1.0 SDR-video (sRGB)" view. Both are fixed in `dlss5_enhance.py`:
> - full export mask; the denoiser is kept on the EXR and on a new PNG driver
> - the PNG driver applies the view transform through Arnold's own OCIO colour manager
> - the host gets `--display-referred`
>
> On the test scene at strength 0.5 with corrected input, the mean change is 1.5%. The gold stays
> gold-ish; skin is somewhat paler and less saturated. Part of the earlier "grain" and colour
> shift were artefacts of the wrong input. **Re-test the ammonite scene** with the shelf's
> Render button before drawing conclusions.

## Test 7: a character (the key result)

**Input:** the user's stylised anime-girl head, in the live Maya scene (untitled, persp, 960×540, Arnold AA 3), driven through the Maya command port 6111 with `process_scene_frames` and the game's exact settings.

**Result: a strong, character-specific transformation.** The model re-interprets the stylised face toward a photoreal one:
- the eyes become smaller and more realistic, with a less saturated iris
- the lips gain shape, shading and a highlight
- the skin gains realistic tone variation and blush
- the nose is modelled with shading
- the hair colour becomes more natural
- the grey hands get shading

The face "reads" as a different, realistic person.

Numbers:
- contrast 0.178 → 0.185
- saturation +8%
- fine-detail energy −16% (the hair and background are smoothed)
- 87% of the change is local, not a global grade

**Conclusion.** The dramatic in-game effect is the **character/skin path** of the NR model (RenoDX exposes it as "Character/Skin Structure" and "Automatic / Character Mask"). Environments and props get only the mild relight/soften that tests 1–6 measured. For Arnold, DLSS 5 is a photoreal **character re-rendering** filter, not a general detail enhancer.

### Test 7b: face strength sweep and 10-frame orbit

**Strength** (`test/head_sweep/strength_strip.png`):
- 0.25 adds realistic lips, skin tone and nose shading while keeping the stylised eyes.
- 0.5–0.75 progressively realises the eyes.
- 0.98 is a full re-interpretation.

**Orbit** (`tools/head_anim_snippet.py`, `test/head_anim/`):
- Setup: a temporary camera orbiting ±8° over 10 frames, exact Arnold MVs, strength 0.98. The user's persp and scene were restored after export.
- Visually, the realistic face is **consistent across frames**: same identity, eyes and lips.
- Edit flicker ratio (change of the DLSS edit between frames ÷ its size, not motion-compensated, so real motion inflates it): whole frame **0.37** vs 0.75–0.85 on the environment test; face box 0.53, with real motion there at 0.05.
- Much more stable than on environments. Needs a playback look to judge fine shimmer.

### Test 7c: 16-bit HDR path (`dlss5_host --hdr 1`, `tools/hdr_test.py`)

Pipeline:
- ACEScg EXR → linear Rec.709 × 272/80 (scRGB, diffuse white at RenoDX's 272 nits) → R16G16B16A16_FLOAT back buffer.
- Feed: `flags=75 (HDR …)`. RenoDX: "reversible NR color bridge … linear HDR BT.709". Feature 18 evaluated.
- Float read-back → ACEScg EXR. Highlights are kept (scRGB max 4.23).

Viewing:
- Maya's Render View writeImage did not reproduce the ACES view (0.176 error).
- Hill's fitted ACES RRT/ODT matched Arnold's own ACES PNG to 0.003 and was used instead (`tools/aces_view.py`).

Results on the head (ACES view):

| | contrast | saturation | detail | change vs original |
|---|---|---|---|---|
| original | 0.160 | 0.035 | 0.0201 | – |
| SDR 8-bit DLSS 5 | 0.168 | 0.037 | 0.0168 (−17%) | 0.019 |
| **HDR 16-bit DLSS 5** | 0.170 | 0.038 | **0.0189 (−6%)** | 0.018 |

**HDR keeps much more fine detail**: hair strands, and skin texture near the eyes. The photoreal face change is the same, and the output is a float EXR.

### HDR is now the default (`tools/test_hdr_outputs.py`, 8/8 PASS)

- All processing runs on the 16-bit scRGB path, from the Arnold EXR in the scene's rendering space.
- Output "auto": a single frame gives a 16-bit PNG (fitted ACES view); ranges and sequences give a half-float EXR in the rendering space plus an 8-bit preview PNG. PNG 8/16 can be chosen explicitly.
- Stabilisation runs in scRGB. Its disocclusion test uses x/(1+x)-compressed values.
- Highlights survive: the Arnold sun specular peaks at 594 and the DLSS 5 EXR at 337.

## Test 6: same pipeline, game frame vs Arnold frame (the decisive check)

**Settings.** The RenoDX overlay in the running game was screen-captured. Every setting equals `runtime/ReShade.ini`: Preset #1, Style "Default" (= `NRStyle=0`), intensity 0.98, global tone 2.00, local tone 1.15, structure 2.00, skin 1.97, character mask on, UI correction off, 272 nits, depth "use game NGX flag", MV scale 2. So no setting was missed.

**Game on/off pair.** The user's Game Bar pair (DLSS 5 off/on, bar interior) was measured on rows 150–980, which excludes the HUD:

| | contrast | detail | brightness | green cast |
|---|---|---|---|---|
| game OFF | 0.067 | 0.0150 | 0.178 | +0.025 |
| game ON (in-game DLSS 5) | 0.081 (+21%) | 0.0184 (+23%) | 0.189 | +0.024 |
| game OFF through our pipeline (flat depth) | 0.073 (+9%) | 0.0159 (+6%) | 0.181 | +0.025 |

**Arnold test frame through the same pipeline:** −24.0% detail with real depth, −24.2% with flat depth.

**Conclusion.**
- The pipeline reproduces the direction of the in-game effect on game content: more contrast and detail, at roughly 40% of the in-game strength. The remaining gap is plausibly real depth and motion history in-game.
- On Arnold frames the same model smooths instead. The effect depends on the image content.
- It is not caused by depth encoding, motion vectors, or any RenoDX setting.

## Test 5: 10-frame animation (`tools/test_anim.py`)

Setup:
- Test scene with a camera truck/pan, a spinning torus and a drifting sphere; about 8% change between consecutive frames.
- ACES, OIDN, Style 0, intensity 0.98, one NR evaluation per new frame.

Detail vs original:

| frame | 1 | 4 | 7 | 10 |
|---|---|---|---|---|
| no MVs (provider 0) | −24% | −28% | −27% | −27% |
| LumeniteFX optical-flow MVs (provider 3, as in the FO4 preset) | −23% | −32% | −34% | −36% |

Flicker (frame-to-frame change of the DLSS edit itself, relative to its size):
- no MVs: 0.76
- Lumenite MVs: 0.85

The DLSS edit is far from stable.

Visually:
- the thin wires ghost and fade over the sequence
- the far checker washes out
- the skin texture is smoothed

Motion makes it **worse**, and the game-style optical-flow MVs (1/8 resolution) make it worse again. This looks like temporal accumulation with imprecise motion.

### Test 5b: exact Arnold motion vectors (`tools/test_mv.py`, `tools/test_anim.py`)

How the vectors are produced:
- Export: motion blur on over [frame-1, frame] ("End On Frame", 1 frame), camera shutter 1..1 (instantaneous), `aiMotionVector` raw into a `motionvector` AOV.
- The beauty matches the non-MV render (mean diff ≤ 0.0002): no blur introduced.

Convention, verified by backward warping on horizontal and crane moves:
- `prev = (x - 2*dx, y + 2*dy)`: the AOV covers half a frame and has y up.
- The minimum at scale 2.0 is sharp (1.9 and 2.1 are clearly worse).
- Warp error drops 80–85% compared with zero motion.

How they reach DLSS 5:
- `dlss5_host.exe` registers itself as a ReShade add-on and, on `reshade_begin_effects`, uploads `mv_uv = (-2dx/W, +2dy/H)` into `DLSS5_Feed.fx`'s `texMotionVectors` (provider 0), with `MV_VALIDATE=0`.
- Confirmed: 127/129 presents uploaded, and the feed's own debug view shows the vectors (`test/anim_mv/verify/debug_mv.png`).
- The feed's log warning "no known texMotionVectors shader… motion vectors will be zero" is a file check only.

Result on the 10-frame shot:

| | detail, frames 2–10 | flicker ratio |
|---|---|---|
| zero MVs | −24 … −28% | 0.76 |
| exact Arnold MVs | −25 … −28% | 0.75 |

**Exact motion vectors change nothing measurable.** The smoothing and the frame-to-frame instability are per-evaluation behaviour of the NR model on these frames, not a history/reprojection problem.

## Test 3: noisy/aliased input vs Arnold OIDN (`tools/test_noise.py`)

Test scene, ACES view, compared with an AA10 reference:

| Image | RMSE | PSNR |
|---|---|---|
| rough AA1, no denoise | 0.0315 | 30.0 |
| rough + Arnold OIDN | **0.0163** | **35.8** |
| rough + DLSS 5 @0.5 | 0.0346 | 29.2 |
| rough + DLSS 5 @0.98 | 0.0457 | 26.8 |

Visually, DLSS 5 leaves nearly all the sampling noise in and only desaturates it. **It is not a denoiser for Arnold.** Use Arnold's own denoiser.

## Test 4: NRPreset × NRStyle sweep (`tools/sweep_preset_style.py`)

Names found in `renodx-dlss5.addon64`:
- NR Preset: Default, Preset #1, Preset #2, Preset #3 → NGX `DLSSNR.Hint.Render.Preset`
- NR Style: Natural, Cinematic → NGX `DLSSNR.Style`

RenoDX's log confirmed each value was applied.

| | detail | contrast | saturation |
|---|---|---|---|
| original | – | 0.123 | 0.087 |
| Natural (all presets) | −24% | 0.104 | 0.068 |
| Cinematic (all presets) | −19% | 0.112 | 0.083 |

- The presets have no measurable effect.
- Cinematic preserves colour and contrast far better, and is now the default (`NRStyle=1`; Style option in the panel).
- No combination increases fine detail.

**Conclusion so far:** on clean, denoised Arnold frames this DLSS 5 build acts as a look/relight grade that mildly smooths the image. It does not add detail and does not denoise.

## Test 1: single Arnold still through DLSS 5 NR (2026-09-28)

**Setup**
- `test/test_scene.ma`: SSS skin sphere with fine noise bump, 60×60 checker ground, gold metal torus (roughness 0.12), 24 wires of radius 0.012, soft sun + sky.
- Rendered with Arnold 7.3.7 (`kick`, AA 5) at 1280×720. Beauty + Z in `test_render.exr`.
- `runtime/dlss5_host.exe`, 900 frames at vsync:
  - frame 450: DLAA-only capture, then F6 (RenoDX NR toggle)
  - frame 900: NR capture
- Motion vectors: none (provider 0, all zero). Depth: Arnold Z → reversed-Z `0.1/Z`.
- RenoDX settings (copied from the user's Fallout 4 ini): `NRIntensity=0.98 NRGlobalTone=2 NRLocalTone=1.15 NRLocalStructure=2 NRSkinStructure=1.97 NRPreset=1`.

**Result: feature 18 ran on the Arnold frame at 1280×720 → 1280×720.**
```
NGX feature create intercepted: feature=18 (DLSSNR/reserved-18)
feature 18 created via the signed snippet after DLSS/DLAA for NR input 1280x720 -> output 1280x720 with guides 1280x720
inline feature 18 evaluation succeeded (count=60, ... output 1280x720 [native])
```
Feature 18 was recreated about every 2 s while the image stayed static. The cause is not yet known.

**Numbers (8-bit sRGB, 0–1)**

| Pair | Mean abs | RMS | Max |
|---|---|---|---|
| original vs DLAA only | 0.0007 | 0.0031 | 0.094 |
| DLAA vs DLSS 5 NR | 0.030 | 0.040 | 0.188 |

DLAA is almost a no-op, so nearly all the change comes from the neural pass.

**What NR did (visual; `test/original.png`, `dlaa.png`, `dlss5_nr.png`, `compare_crop.png`)**
- **Global re-grade:** lower contrast, lifted shadows, a slightly hazy/desaturated look. Sky blue → grey-blue; skin pink → greyer pink.
- **Material change:** the gold torus came out **near-neutral silver**. That is a hallucinated material change, not detail.
- **Skin:** pore bump reads slightly more structured, but softer in contrast.
- **Thin wires:** preserved with no breakup. Slightly lighter.
- **Shadows:** softer and lighter.
- **Far checker:** hazier and lower contrast. No new aliasing seen.
- No obvious ringing or sharpening halos at this zoom.

**Tone check:** with `NRGlobalTone=0 NRLocalTone=0` the desaturation and gold→silver shift remain (mean change 2.3% vs 3.0%). The re-grade is mostly the model, not the tone settings.

## Test 2: user's ammonite scene, 1620×2880 (2026-09-28)

- Exported from the user's scene, rendered with `kick` on CPU (3:40; the scene is set to GPU, but the GPU was busy).
- NR ran at 1620×2880 → 1620×2880. About 1.3 s/frame at this size on 8 GB VRAM, so the host needs only about 80 frames (60 create-delay + a few NR).
- NR on/off is persisted as `NeuralUplift` in ReShade.ini; the host sets it to 1 instead of pressing F6.

| NRIntensity | Mean abs change vs original |
|---|---|
| 0.25 | 0.008 |
| 0.5 | 0.017 |
| 0.75 | 0.025 |
| 0.98 | 0.033 |

Roughly linear, so it is a usable strength slider.

What NR did (`test/user/compare_*.png`, `diff_x6.png`):
- removed the chromatic/rainbow edge fringing on the ribs
- neutralised the blue/purple tint in crevices
- **added fine grain/speckle micro-texture** to the shell (invented)
- duller, darker, less saturated shell; less sheen
- thin spines preserved, no halos; background almost unchanged

**Caveat:** the tone settings are the user's aggressive Fallout 4 values (`NRGlobalTone=2` is the maximum used there). Much of the re-grade is likely those settings, not the model's detail pass. Next test: rerun with neutral tone settings to isolate the detail change.
