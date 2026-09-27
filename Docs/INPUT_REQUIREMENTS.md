# Input requirements (what DLSS 5 NR is fed)

Everything below was measured or verified. See TEST_RESULTS.md.

| Input | Source | Format sent to the stack | Notes |
|---|---|---|---|
| **Colour** (default HDR path) | Arnold EXR RGB in the rendering space | scRGB on a 16-bit float back buffer: `rec709_linear * 272/80` | ACEScg is converted with the AP1→Rec.709 matrix. **Not clipped**: negative (out-of-gamut) values are kept, and anything the pass drops is restored afterwards. RenoDX's bridge reports "linear HDR BT.709". |
| Colour (legacy 8-bit path) | Arnold's view-transformed PNG | R8G8B8A8 sRGB | Loses about 3× more fine detail than HDR |
| **Depth** | Arnold `Z` AOV | real D32 depth buffer, reversed-Z `near / Z` | Required. The feed ignores flat depth. Depth *range* (near) made no measurable difference. |
| **Motion vectors** (ranges only) | Arnold `motionvector` AOV via `aiMotionVector` raw; motion blur on, *End On Frame*, 1 frame, camera shutter 1..1 (instantaneous, so the beauty isn't blurred) | written into the feed's `texMotionVectors`: `mv_uv = (-2dx/W, +2dy/H)` | The AOV covers half a frame and uses y-up. The sign and 2× scale were verified by backward-warp tests. The feed's `MV_VALIDATE` is switched off for these exact vectors. |
| Resolution | render resolution | same in and out | Tested at 960×540, 1280×720 and 1620×2880 (portrait) |
| Denoiser | the scene's Arnold imager (e.g. OIDN) | kept | Export uses the full node mask, so imagers aren't dropped |

## RenoDX settings (runtime/ReShade.ini, `[RenoDX.DLSS5]`)

These match the Fallout 4 profile the stack was proven on:

| Key | Value | Notes |
|---|---|---|
| `NeuralUplift` | 1 | NR on |
| `NRIntensity` | 0–1 | Strength slider |
| `NRLocalStructure` | 2 | Structure slider |
| `NRStyle` | **0** | "Default". 1 and 2 soften more. |
| `NRPreset` | 1 | No measurable effect |
| `NRSkinStructure` | 1.97 | |
| `NRGlobalTone` / `NRLocalTone` | 2 / 1.15 | |
| `NRAutoMask` | 1 | Character mask |
| `NRDiffuseWhiteNits` | 272 | HDR bridge |

`NRUICorrection`, `NRDepthMode`, `NRTransferStrength` and `NRColorStrength` were tested and had no measurable effect.

## Not required

- Temporal history / high frame rate: 4× more in-between frames did **not** reduce wobble. The model re-invents detail every evaluation, and the stabilisation handles that instead.
- Jitter: none. The feed's DLAA contract uses zero jitter.
