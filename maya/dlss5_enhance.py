"""DLSS 5 Enhanced Detail for Arnold renders (Maya 2024).

Same-resolution neural post-process: Arnold beauty + Z -> DLSS 5 neural rendering -> image.
No upscaling. The DLSS work happens in runtime/dlss5_host.exe (a separate process that loads
the user's own ReShade + dlss5-feed + renodx-dlss5 stack), so Maya never loads ReShade.

Pipeline per job:
  1. (scene jobs) export .ass per frame with a Z AOV, render with Arnold's kick
     (scene settings untouched: the Z AOV / EXR driver changes are reverted after export)
  2. EXR -> PFM colour + depth with Arnold's oiiotool
  3. dlss5_host --list: every frame in playback order in ONE session (temporal history kept)
  4. result -> PNG/TIF/EXR in the output folder; the last frame is loaded into the Render View

UI:  import dlss5_enhance; dlss5_enhance.show()
Headless / scripting:  process_scene_frames(...), process_exr_sequence(...)
"""
import glob
import os
import re
import shutil
import subprocess
import threading
import time

import maya.cmds as cmds
import maya.utils

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
HOST = os.path.join(RUNTIME, "dlss5_host.exe")
INI = os.path.join(RUNTIME, "ReShade.ini")
WORK_ROOT = os.path.join(ROOT, "work")

WIN = "dlss5EnhanceWin"
KEEP_WORK = False  # tests set this to keep the EXRs / PFMs / motion vectors of a run
FORCE_CHUNK = None  # tests: fixed chunk size instead of the free-disk-space estimate
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW for kick/oiiotool consoles
_state = {"proc": None, "cancel": False, "busy": False}


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# tools

def arnold_bin():
    """Arnold's bin folder (kick, oiiotool) next to the loaded MtoA plug-in."""
    cmds.loadPlugin("mtoa", quiet=True)
    mll = cmds.pluginInfo("mtoa", query=True, path=True)
    return os.path.normpath(os.path.join(os.path.dirname(mll), "..", "bin"))


def arnold_shaders():
    return os.path.normpath(os.path.join(arnold_bin(), "..", "shaders"))


def _run(args, log, cwd=None):
    """Run a tool, stream nothing to Maya, honour Cancel. Returns (code, output)."""
    if _state["cancel"]:
        raise Cancelled()
    p = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         creationflags=_NO_WINDOW if os.path.basename(args[0]) != "dlss5_host.exe" else 0)
    _state["proc"] = p
    out, _ = p.communicate()
    _state["proc"] = None
    text = out.decode("utf-8", "replace")
    if _state["cancel"]:
        raise Cancelled()
    if p.returncode != 0:
        log("  {} exited {}:\n{}".format(os.path.basename(args[0]), p.returncode, text[-1500:]))
    return p.returncode, text


def exr_channels(exr):
    code, text = _run([os.path.join(arnold_bin(), "oiiotool.exe"), "--info", "-v", exr], lambda m: None)
    m = re.search(r"channel list:\s*(.+)", text)
    return [c.strip() for c in m.group(1).split(",")] if m else []


# ---------------------------------------------------------------------------------------------
# settings (existing RenoDX keys in the project-local ReShade.ini only)

# RenoDX "NR Style" -> NGX DLSSNR.Style (index = NRStyle); order confirmed from RenoDX's own panel,
# which shows "Default" for NRStyle=0 (the value the game profile uses).
STYLES = ["Default", "Natural", "Cinematic"]


def write_settings(intensity, structure, style=None):
    vals = {"NeuralUplift": "1", "NRIntensity": "%g" % intensity, "NRLocalStructure": "%g" % structure}
    if style is not None:
        vals["NRStyle"] = str(STYLES.index(style))
    with open(INI, "r") as f:
        lines = f.read().splitlines()
    out = []
    for line in lines:
        key = line.split("=", 1)[0]
        out.append("%s=%s" % (key, vals[key]) if key in vals and "=" in line else line)
    with open(INI, "w") as f:
        f.write("\n".join(out) + "\n")


def read_settings():
    vals = {}
    try:
        with open(INI) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    vals[k] = v
    except IOError:
        pass
    return float(vals.get("NRIntensity", 0.5)), float(vals.get("NRLocalStructure", 2.0))


def read_style():
    try:
        with open(INI) as f:
            for line in f:
                if line.startswith("NRStyle="):
                    return STYLES[int(line.split("=", 1)[1])]
    except (IOError, ValueError, IndexError):
        pass
    return "Default"


# ---------------------------------------------------------------------------------------------
# scene export (main thread only)

def _render_camera():
    cams = [c for c in cmds.ls(type="camera") if cmds.getAttr(c + ".renderable")]
    if not cams:
        raise RuntimeError("No renderable camera in Render Settings.")
    return cams[0]


def export_scene_frames(work, start, end, motion_vectors=False):
    """Export one .ass per frame with a Z AOV and our own EXR path. Scene settings are restored.

    motion_vectors: also write a raw 'motionvector' AOV (raster pixels, previous frame -> this
    frame): motion blur is switched on over the frame interval ending on each frame, with an
    instantaneous shutter at the frame itself so the beauty is not blurred.

    Returns (list of (frame, ass, exr, display_png), camera near clip).
    """
    cmds.loadPlugin("mtoa", quiet=True)
    import mtoa.aovs as aovs
    import mtoa.core as core
    core.createOptions()

    cam = _render_camera()
    near = cmds.getAttr(cam + ".nearClipPlane")
    drv = "defaultArnoldDriver"
    opt = "defaultArnoldRenderOptions"
    plugs = [drv + ".aiTranslator", drv + ".mergeAOVs", drv + ".halfPrecision"]
    if motion_vectors:
        plugs += [opt + "." + a for a in ("motion_blur_enable", "mb_camera_enable", "mb_objects_enable",
                                          "mb_object_deform_enable", "range_type", "motion_frames")]
        plugs += [cam + "." + a for a in ("aiUseGlobalShutter", "aiShutterStart", "aiShutterEnd")]
    saved = {p: cmds.getAttr(p) for p in plugs}
    made = []
    try:
        if not cmds.ls("aiAOV_Z"):
            made.append(aovs.AOVInterface().addAOV("Z", aovType="float").node)
        cmds.setAttr(drv + ".aiTranslator", "exr", type="string")
        cmds.setAttr(drv + ".mergeAOVs", 1)
        cmds.setAttr(drv + ".halfPrecision", 0)
        if motion_vectors:
            for a in ("motion_blur_enable", "mb_camera_enable", "mb_objects_enable", "mb_object_deform_enable"):
                cmds.setAttr(opt + "." + a, 1)
            cmds.setAttr(opt + ".range_type", 2)       # End On Frame: motion range [frame-1, frame]
            cmds.setAttr(opt + ".motion_frames", 1.0)
            cmds.setAttr(cam + ".aiUseGlobalShutter", 0)
            cmds.setAttr(cam + ".aiShutterStart", 1.0)  # shutter closed at the frame itself: no blur
            cmds.setAttr(cam + ".aiShutterEnd", 1.0)
            if not cmds.ls("aiAOV_motionvector"):
                made.append(aovs.AOVInterface().addAOV("motionvector", aovType="rgb").node)
            mv = cmds.shadingNode("aiMotionVector", asShader=True, name="dlss5_motionVector")
            made.append(mv)
            cmds.setAttr(mv + ".raw", 1)                # raster-space pixels, not normalised
            cmds.setAttr(mv + ".time0", 0.0)            # range start = previous frame
            cmds.setAttr(mv + ".time1", 1.0)            # range end   = this frame
            prev = cmds.listConnections("aiAOV_motionvector.defaultValue", source=True, destination=False,
                                        plugs=True)
            if prev:
                saved["__mv_default__"] = prev[0]
            cmds.connectAttr(mv + ".outColor", "aiAOV_motionvector.defaultValue", force=True)
        base = os.path.join(work, "scene").replace("\\", "/")
        # Full node mask: 255 would drop imagers (the denoiser), operators and the colour manager.
        # compressed (.ass.gz, read directly by kick): scenes can be tens of MB per frame
        cmds.arnoldExportAss(f=base + ".ass", cam=cam, mask=0xFFFF, lightLinks=True, shadowLinks=True,
                             compressed=True,
                             startFrame=start, endFrame=end, frameStep=1)
    finally:
        prev_mv = saved.pop("__mv_default__", None)
        for n in made:
            if cmds.objExists(n):
                cmds.delete(n)
        if prev_mv and cmds.objExists("aiAOV_motionvector"):
            cmds.connectAttr(prev_mv, "aiAOV_motionvector.defaultValue", force=True)
        for plug, v in saved.items():
            if isinstance(v, str):
                cmds.setAttr(plug, v, type="string")
            else:
                cmds.setAttr(plug, v)

    asses = sorted(glob.glob(os.path.join(work, "scene*.ass.gz")) or glob.glob(os.path.join(work, "scene*.ass")))
    if len(asses) != end - start + 1:
        raise RuntimeError("Expected %d .ass files, found %d" % (end - start + 1, len(asses)))
    view = display_transform()
    jobs = []
    for frame, ass in zip(range(start, end + 1), asses):
        exr = os.path.join(work, "arnold.%04d.exr" % frame).replace("\\", "/")
        png = os.path.join(work, "display.%04d.png" % frame).replace("\\", "/") if view else None
        _patch_ass(ass, exr, png, view)
        jobs.append((frame, ass, exr, png))
    return jobs, near


def display_transform():
    """The scene's colour-managed output transform (e.g. 'ACES 1.0 SDR-video (sRGB)'), or None
    when colour management is off. Arnold's own OCIO colour manager applies it at render time."""
    try:
        if cmds.colorManagementPrefs(q=True, cmEnabled=True):
            return cmds.colorManagementPrefs(q=True, outputTransformName=True) or None
    except RuntimeError:
        pass
    return None


def _patch_ass(ass, exr, display_png=None, view=None):
    """Point the EXR driver at our file; optionally add a PNG driver that writes the beauty
    through the scene's view transform (what the Render View shows). The driver's imager
    (denoiser) link is kept; it is only dropped if the imager node is genuinely missing from
    the file, which kick would reject."""
    import gzip
    opener = gzip.open if ass.endswith(".gz") else open
    with opener(ass, "rt", encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    for name in set(re.findall(r'driver_exr\s*\{[^}]*?\binput\s+"([^"]+)"', text)):
        if not re.search(r'\n\s*name\s+"?%s"?\s*\n' % re.escape(name), text):
            text = re.sub(r'\n\s*input "%s"' % re.escape(name), "", text)
    text, n = re.subn(r'(driver_exr\s*\{[^}]*?filename\s+)"[^"]*"', lambda m: m.group(1) + '"%s"' % exr, text)
    if not n:
        raise RuntimeError("No EXR driver found in " + ass)

    if display_png:
        rgba = re.search(r'"RGBA RGBA (\S+) (\S+)"', text)
        if not rgba:
            raise RuntimeError("No RGBA output in " + ass)
        filt, drv = rgba.group(1), rgba.group(2)
        imager = re.search(r'\bname\s+%s\s*\n\s*input\s+"([^"]+)"' % re.escape(drv), text)
        text, n = re.subn(r'(\n\s*outputs\s+)(\d+)(\s+1\s+STRING\s*\n)',
                          lambda m: '%s%d%s  "RGBA RGBA %s dlss5_display"\n' % (
                              m.group(1), int(m.group(2)) + 1, m.group(3), filt), text, count=1)
        if not n:
            raise RuntimeError("No outputs array in " + ass)
        text += '\ndriver_png\n{\n name dlss5_display\n%s filename "%s"\n color_space "%s"\n}\n' % (
            ' input "%s"\n' % imager.group(1) if imager else "", display_png, view)
    with opener(ass, "wt", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)


# ---------------------------------------------------------------------------------------------
# worker (any thread)

def _kick(ass, log):
    kick = os.path.join(arnold_bin(), "kick.exe")
    args = [kick, "-i", ass, "-dw", "-dp", "-v", "1", "-l", arnold_shaders()]
    code, text = _run(args, log, cwd=os.path.dirname(ass))
    if "no GPU matching requirements" in text or "device selection failed" in text:
        log("  GPU unavailable for Arnold, rendering this frame on CPU")
        code, text = _run(args + ["-set", "options.render_device", "CPU"], log, cwd=os.path.dirname(ass))
    if code != 0 or "render done" not in text:
        raise RuntimeError("kick failed on " + os.path.basename(ass))


def _mv_pfm(exr, work, tag, log):
    """Arnold raw motionvector AOV -> 3-channel PFM (host uses R, G), or None if absent."""
    names = [c for c in exr_channels(exr) if c.lower().startswith("motionvector.")]
    if len(names) < 2:
        return None
    mv = os.path.join(work, tag + "_mv.pfm")
    oiio = os.path.join(arnold_bin(), "oiiotool.exe")
    chans = ",".join(sorted(names)[:3] if len(names) >= 3 else names + names[:1])
    if _run([oiio, exr, "--ch", chans, "-o", mv], log)[0] != 0:
        raise RuntimeError("Could not read motion vectors from " + exr)
    return mv


def write_preset(arnold_mv):
    """Project-local ReShade preset: DLSS5_Feed with provider 0 (texMotionVectors). With Arnold's
    exact vectors the feed's optical-flow validation is switched off (MV_VALIDATE is the
    effect's own documented uniform); it exists to catch optical-flow errors, and would only
    zero correct vectors."""
    preset = os.path.join(RUNTIME, "ReShadePreset.ini")
    body = ("\nTechniques=DLSS5_Feed@DLSS5_Feed.fx\n\nTechniqueSorting=DLSS5_Feed@DLSS5_Feed.fx\n\n"
            "PreprocessorDefinitions=DLSS5_MV_PROVIDER=0\n\n[DLSS5_Feed.fx]\n"
            "PreprocessorDefinitions=DLSS5_MV_PROVIDER=0\n")
    if arnold_mv:
        body += "MV_VALIDATE=0\n"
    with open(preset, "w") as f:
        f.write(body)


def _to_pfm(exr, display_png, work, tag, allow_flat_depth, log):
    """Colour PFM (from the view-transformed PNG when there is one, else linear EXR RGB) and
    depth PFM (EXR Z)."""
    oiio = os.path.join(arnold_bin(), "oiiotool.exe")
    chans = exr_channels(exr)
    color = os.path.join(work, tag + "_c.pfm")
    depth = os.path.join(work, tag + "_z.pfm")
    if display_png:
        args = [oiio, display_png, "--ch", "R,G,B", "-d", "float", "-o", color]
    else:
        rgb = ",".join(c for c in ("R", "G", "B") if c in chans) or "0,1,2"
        args = [oiio, exr, "--ch", rgb, "-o", color]
    if _run(args, log)[0] != 0:
        raise RuntimeError("Could not read colour for " + tag)
    zname = next((c for c in chans if c == "Z" or c.endswith(".Z")), None)
    if zname:
        code = _run([oiio, exr, "--ch", zname, "-o", depth], log)[0]
    elif allow_flat_depth:
        code = _run([oiio, exr, "--ch", "R", "--mulc", "0", "--addc", "1", "-o", depth], log)[0]
    else:
        raise RuntimeError("%s has no Z channel (add a Z AOV, or allow flat depth)" % os.path.basename(exr))
    if code != 0:
        raise RuntimeError("Could not read depth from " + exr)
    return color, depth, bool(zname)


def _read_pfm(path):
    import numpy as np
    with open(path, "rb") as f:
        tag = f.readline().strip()
        w, h = map(int, f.readline().split())
        scale = float(f.readline())
        c = 3 if tag == b"PF" else 1
        d = np.frombuffer(f.read(), dtype="<f4" if scale < 0 else ">f4").reshape(h, w, c)
    return np.flipud(d).astype(np.float32)


def _read_ppm(path):
    import numpy as np
    with open(path, "rb") as f:
        assert f.readline().strip() == b"P6"
        w, h = map(int, f.readline().split())
        f.readline()
        return (np.frombuffer(f.read(), dtype=np.uint8).reshape(h, w, 3) / 255.0).astype(np.float32)


def _write_pfm(img, path, clip=True):
    import numpy as np
    h, w = img.shape[:2]
    if clip:
        img = np.clip(img, 0, 1)
    with open(path, "wb") as f:
        f.write(b"PF\n%d %d\n-1.0\n" % (w, h))
        f.write(np.flipud(img).astype("<f4").tobytes())


def _read_img(path):
    return _read_ppm(path) if path.lower().endswith(".ppm") else _read_pfm(path)


# ---------------------------------------------------------------------------------------------
# HDR colour (the DLSS 5 pass runs on a 16-bit float scRGB back buffer: linear Rec.709,
# 1.0 = 80 nits; RenoDX's HDR bridge treats 272 nits as diffuse white, so scene 1.0 -> 272/80).
# Measured on the head: HDR keeps far more fine detail than 8-bit SDR (-6% vs -17%).

SCRGB_WHITE = 272.0 / 80.0
_AP1_TO_709 = ((1.70505, -0.62179, -0.08326), (-0.13026, 1.14080, -0.01055), (-0.02400, -0.12897, 1.15297))
# Stephen Hill's fitted ACES 1.0 RRT + sRGB ODT; matched Arnold's own "ACES 1.0 SDR-video (sRGB)"
# output to 0.003 mean abs error on the test head (tools/aces_view.py).
_HILL_IN = ((0.59719, 0.35458, 0.04823), (0.07600, 0.90834, 0.01566), (0.02840, 0.13383, 0.83777))
_HILL_OUT = ((1.60475, -0.53108, -0.07367), (-0.10208, 1.10813, -0.00605), (-0.00327, -0.07276, 1.07602))


def _to_709_matrix(space):
    import numpy as np
    return np.array(_AP1_TO_709) if space and "acescg" in space.lower() else np.eye(3)


def _view(lin709, aces):
    """Display-referred sRGB (0..1) from scene-linear Rec.709: ACES fitted view or plain sRGB."""
    import numpy as np
    v = lin709
    if aces:
        v = v @ np.array(_HILL_IN).T
        v = (v * (v + 0.0245786) - 0.000090537) / (v * (0.983729 * v + 0.4329510) + 0.238081)
        v = v @ np.array(_HILL_OUT).T
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def _bilinear(img, x, y):
    import numpy as np
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return ((img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy) +
            (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy)


def stabilise_sequence(orig_pfms, dlss_ppms, mv_pfms, alpha, log):
    """Temporal stabilisation of the DLSS 5 edit along Arnold's motion vectors.

    The model re-invents fine detail every frame (measured: its own history does not lock it,
    even at 4x frame rate), which reads as wobble. The per-frame edit delta = dlss - orig is
    blended with the previous frame's edit carried along the exact vectors:
        S_t = a * warp(S_t-1) + (1 - a) * delta_t,   out_t = orig_t + S_t
    a is dropped to 0 where the warped previous original disagrees with the current one
    (disocclusion). Warp convention verified in tools/test_mv.py: prev = (x - 2dx, y + 2dy).
    alpha 0.8 measured -30% face wobble with an unchanged edit size (Docs/TEST_RESULTS.md).
    Works on display-referred (PPM) or scRGB (PFM) frames; the disocclusion test compares
    x / (1 + x) compressed values so its thresholds hold for both.
    Returns the stabilised frames (float arrays, same space as the inputs)."""
    st = Stabiliser(alpha)
    out = [st.step(_read_pfm(op), _read_img(dp), _read_pfm(mp) if mp else None)
           for op, dp, mp in zip(orig_pfms, dlss_ppms, mv_pfms)]
    log("Stabilised %d frame(s) along Arnold motion vectors (strength %g)" % (len(out), alpha))
    return out


class Stabiliser(object):
    """Incremental form of stabilise_sequence: frames are fed one at a time, so a long shot can
    be processed in disk-bounded chunks with the stabilisation state carried across them."""

    def __init__(self, alpha):
        self.alpha, self.S, self.prev_o, self.n = alpha, None, None, 0

    def step(self, o, d, mv):
        import numpy as np
        delta = d - o
        if self.S is None or mv is None:
            self.S = delta
        else:
            h, w = o.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            px, py = xx - 2 * mv[..., 0], yy + 2 * mv[..., 1]
            comp = lambda x: x / (1.0 + np.abs(x))
            agree = 2.0 * np.mean(np.abs(comp(_bilinear(self.prev_o, px, py)) - comp(o)), axis=2, keepdims=True)
            valid = np.clip(1.0 - (agree - 0.03) / 0.05, 0.0, 1.0)
            valid *= ((px >= 0) & (px <= w - 1) & (py >= 0) & (py <= h - 1))[..., None]
            a = self.alpha * valid
            self.S = a * _bilinear(self.S, px, py) + (1 - a) * delta
        self.prev_o = o
        self.n += 1
        return o + self.S


FORMATS = {"exr": ("exr", None), "png16": ("png", "uint16"), "png8": ("png", "uint8"),
           "png": ("png", "uint8"), "tif": ("tif", "uint16")}


def _hdr_input(exr, work, tag, space, log):
    """Arnold EXR RGB (rendering space) -> scRGB PFM for the HDR back buffer."""
    import numpy as np
    oiio = os.path.join(arnold_bin(), "oiiotool.exe")
    raw = os.path.join(work, tag + "_lin.pfm")
    chans = exr_channels(exr)
    rgb = ",".join(c for c in ("R", "G", "B") if c in chans) or "0,1,2"
    if _run([oiio, exr, "--ch", rgb, "-o", raw], log)[0] != 0:
        raise RuntimeError("Could not read colour from " + exr)
    lin = _read_pfm(raw)
    # scRGB is extended-range: colours outside Rec.709 (saturated ACEScg blues, e.g. deep water)
    # have negative components. They are NOT clipped here (clipping shifted deep blue to cyan);
    # whatever part the DLSS pass does not carry through is restored in _restore_gamut().
    scrgb = lin @ _to_709_matrix(space).T * SCRGB_WHITE
    out = os.path.join(work, tag + "_c.pfm")
    _write_pfm(scrgb, out, clip=False)
    return out


def _box_blur(img, r):
    """Box blur of radius r (integer) on an HxWxC array, edge-clamped, via cumulative sums."""
    import numpy as np
    if r < 1:
        return img
    k = 2 * r + 1
    p = np.pad(img, ((r, r), (r, r), (0, 0)), mode="edge")
    c = np.cumsum(np.cumsum(p, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0), (0, 0)))
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def _gauss(img, sigma):
    """Gaussian approximation: three box blurs (sigma^2 = 3 * (w^2 - 1) / 12 per pass)."""
    import math
    w = math.sqrt(4.0 * sigma * sigma + 1.0)
    r = max(1, int(round((w - 1) / 2)))
    for _ in range(3):
        img = _box_blur(img, r)
    return img


def detail_only(orig, dlss, sigma=3.0):
    """'Detail only' mode: keep Arnold's broad look (lighting, colour, SSS) and take only DLSS 5's
    fine detail - pores, creases, stubble - avoiding the 'game character / photogrammetry' regrade.

        out = orig * (dlss / blur(dlss)) / (orig / blur(orig))     per channel, scene-linear

    Measured on a face (tools/test_detail_only.py, split sigma 3 px): colour/lighting change vs
    Arnold 0.003 (full DLSS: 0.024) with more fine detail than full DLSS (0.0228 vs 0.0212)."""
    import numpy as np
    eps = 1e-4
    ratio_d = (np.abs(dlss) + eps) / (np.abs(_gauss(dlss, sigma)) + eps)
    ratio_o = (np.abs(orig) + eps) / (np.abs(_gauss(orig, sigma)) + eps)
    return orig * ratio_d / ratio_o


def _restore_gamut(orig_scrgb, dlss_scrgb):
    """Put back the out-of-Rec.709 part of the input that the DLSS pass dropped.

    The 16-bit pass may clamp negative scRGB values. For each pixel, the input's negative
    ('outside the gamut') component is added back where the output has lost it, so a saturated
    colour keeps its hue; in-gamut pixels are untouched."""
    import numpy as np
    neg_in = np.minimum(orig_scrgb, 0.0)
    neg_out = np.minimum(dlss_scrgb, 0.0)
    return dlss_scrgb + (neg_in - neg_out) * (neg_out > neg_in)


def _write_outputs(img_scrgb, frame, name, out_dir, work, fmt, space, aces, log, exposure=0.0):
    """scRGB result -> requested format (+ 8-bit preview for EXR). Returns the path to show.
    exposure: stops applied to the output (scene-linear, before the view), not to the DLSS input."""
    import numpy as np
    oiio = os.path.join(arnold_bin(), "oiiotool.exe")
    lin709 = img_scrgb / SCRGB_WHITE * (2.0 ** exposure)
    ext, depth = FORMATS[fmt]
    dst = os.path.join(out_dir, "%s_dlss5.%04d.%s" % (name, frame, ext))
    tmp = os.path.join(work, "f%04d_final.pfm" % frame)
    if ext == "exr":
        # back to the rendering space, scene-linear, half float
        _write_pfm(lin709 @ np.linalg.inv(_to_709_matrix(space)).T, tmp, clip=False)
        if _run([oiio, tmp, "-d", "half", "-o", dst], log)[0] != 0:
            raise RuntimeError("Could not write " + dst)
        show = os.path.join(out_dir, "%s_dlss5_preview.%04d.png" % (name, frame))
        _write_pfm(_view(lin709, aces), tmp)
        _run([oiio, tmp, "-d", "uint8", "-o", show], log)
        return show
    _write_pfm(_view(lin709, aces), tmp)
    if _run([oiio, tmp, "-d", depth, "-o", dst], log)[0] != 0:
        raise RuntimeError("Could not write " + dst)
    return dst


def _heat(x):
    """0..1 scalar -> black/red/yellow/white heat colours."""
    import numpy as np
    x = np.clip(x, 0, 1)[..., None]
    return np.concatenate([np.clip(3 * x, 0, 1), np.clip(3 * x - 1, 0, 1), np.clip(3 * x - 2, 0, 1)], axis=2)


def _write_debug(frame, name, out_dir, work, orig_view, dlss_view, depth_path, mv_path, log):
    """Debug images for one frame, in <out_dir>/debug:
      *_compare  : original | DLSS 5 side by side (display-referred)
      *_diff     : where DLSS 5 changed the image (luma difference x5, heat colours)
      *_inputs   : what DLSS 5 received - colour | depth (near = bright) | motion vectors
                   (hue = direction, brightness = speed; black = none)
    """
    import numpy as np
    oiio = os.path.join(arnold_bin(), "oiiotool.exe")
    ddir = os.path.join(out_dir, "debug")
    os.makedirs(ddir, exist_ok=True)
    tmp = os.path.join(work, "f%04d_dbg.pfm" % frame)

    def save(img, suffix):
        _write_pfm(img, tmp)
        _run([oiio, tmp, "-d", "uint8", "-o", os.path.join(ddir, "%s_%s.%04d.png" % (name, suffix, frame))], log)

    save(np.concatenate([orig_view, dlss_view], axis=1), "compare")
    # Change map: DLSS 5 also shifts the overall colour slightly everywhere, which would light up
    # the whole map. Match the DLSS image's per-channel mean/contrast to the original first, so
    # only LOCAL changes (re-invented detail, relit areas) remain; scale to the 99th percentile.
    matched = np.empty_like(dlss_view)
    for ch in range(3):
        o, d = orig_view[..., ch], dlss_view[..., ch]
        matched[..., ch] = (d - d.mean()) / (d.std() + 1e-6) * o.std() + o.mean()
    local = np.mean(np.abs(matched - orig_view), axis=2)
    save(_heat(local / max(np.percentile(local, 99), 1e-6)), "diff")

    z = _read_pfm(depth_path)[..., 0]
    hit = (z > 0) & (z < 1e20)
    dv = np.zeros_like(z)
    if hit.any():
        inv = np.where(hit, 1.0 / np.maximum(z, 1e-6), 0.0)
        lo, hi = np.percentile(inv[hit], 1), np.percentile(inv[hit], 99)
        dv = np.where(hit, np.clip((inv - lo) / max(hi - lo, 1e-9), 0, 1), 0.0)
    depth_img = np.repeat(dv[..., None], 3, axis=2)
    panels = [orig_view, depth_img]
    if mv_path:
        mv = _read_pfm(mv_path)
        dx, dy = mv[..., 0] * 2, mv[..., 1] * 2  # pixels per frame (see LoadMV / test_mv.py)
        mag = np.hypot(dx, dy)
        ang = (np.arctan2(dy, dx) / (2 * np.pi)) % 1.0
        v = np.clip(mag / max(np.percentile(mag, 99), 1e-6), 0, 1)
        h6 = ang * 6
        c = np.stack([np.clip(np.abs(h6 - 3) - 1, 0, 1), np.clip(2 - np.abs(h6 - 2), 0, 1),
                      np.clip(2 - np.abs(h6 - 4), 0, 1)], axis=2)
        panels.append(c * v[..., None])
    save(np.concatenate(panels, axis=1), "inputs")


def _run_pipeline(frames, out_dir, name, fmt, near, work, allow_flat_depth, keep_original,
                  log, done, render_scene, use_mv=False, stabilise=0.0, hdr=True, space="ACEScg",
                  aces=True, debug=False, passes=1, exposure=0.0, mode="full", detail_size=3.0):
    """frames: list of (frame, ass_or_None, exr, display_png_or_None).
    Runs kick (if ass), PFM, host, output. use_mv: pass Arnold's motionvector AOV (as exported by
    export_scene_frames(motion_vectors=True)) to DLSS 5. hdr: run DLSS 5 on a 16-bit float scRGB
    back buffer from the linear EXR (default); False = the older 8-bit display-referred path."""
    t0 = time.time()
    try:
        oiio = os.path.join(arnold_bin(), "oiiotool.exe")
        os.makedirs(out_dir, exist_ok=True)
        display = all(f[3] for f in frames)
        if hdr:
            log("Colour: 16-bit HDR from linear EXR (%s), view %s" % (space, "ACES" if aces else "sRGB"))
        else:
            log("Colour: %s" % ("scene view transform (as the Render View shows it)" if display
                                else "plain sRGB from linear EXR (no view transform available)"))
        log("Motion vectors: %s" % ("Arnold motionvector AOV" if use_mv else "none (zero)"))
        write_preset(use_mv)
        _cleanup_old_work(work, log)
        out_ext = "pfm" if hdr else "ppm"
        stab = (Stabiliser(stabilise) if use_mv and stabilise > 0 and len(frames) > 1 and (display or hdr)
                else None)

        def remove(*paths):
            if KEEP_WORK:
                return
            for p in paths:
                if p and os.path.exists(p):
                    os.remove(p)

        def prepare(i, frame, ass, exr, png):
            """Render (if ours) + convert one frame; returns its temp-file record."""
            if render_scene:
                log("Arnold: rendering frame %d (%d/%d)" % (frame, i + 1, len(frames)))
                _kick(ass, log)
                remove(ass)
            log("Preparing frame %d" % frame)
            tag = "f%04d" % frame
            c, z, real_z = _to_pfm(exr, png if (display and not hdr) else None, work, tag, allow_flat_depth, log)
            if hdr:
                c = _hdr_input(exr, work, tag, space, log)
                remove(os.path.join(work, tag + "_lin.pfm"))
            if not real_z and i == 0:
                log("  no Z channel: using FLAT depth (DLSS gets no depth; results less reliable)")
            if keep_original:
                orig = os.path.join(out_dir, "%s_original.%04d.png" % (name, frame))
                if display:
                    shutil.copyfile(png, orig)
                else:
                    _run([oiio, exr, "--ch", "R,G,B", "--colorconvert", "linear", "sRGB", "-d", "uint8",
                          "-o", orig], log)
            mv = None
            if use_mv:
                mv = _mv_pfm(exr, work, tag, log)
                if mv is None:
                    raise RuntimeError("Frame %d has no motionvector AOV" % frame)
            if render_scene:        # our own render: its EXR / preview PNG are no longer needed
                remove(exr, png)    # (an existing sequence's EXRs are the user's files: never touched)
            return dict(frame=frame, c=c, z=z, mv=mv, out=os.path.join(work, "%s_out.%s" % (tag, out_ext)))

        def run_host(batch):
            _acquire_host_lock(log)
            try:
                _run_host(batch)
            finally:
                _release_host_lock()

        def _run_host(batch):
            lst = os.path.join(work, "frames.txt")
            with open(lst, "w") as f:
                for j in batch:
                    f.write("\t".join([j["c"], j["z"], j["out"]] + ([j["mv"]] if j["mv"] else [])) + "\n")
            log("DLSS 5: processing %d frame(s) (a window opens; leave it alone)" % len(batch))
            # One evaluation per new frame, as in a game.
            args = [HOST, "--list", lst, "--near", "%g" % near, "--per-frame", "1", "--passes", str(passes)]
            if hdr:
                args += ["--hdr", "1"]
            elif display:
                args += ["--display-referred", "1"]
            code, text = _run(args, log, cwd=RUNTIME)
            for line in text.splitlines():
                if "add-on registration" in line or "motion vector" in line:
                    log("  " + line.strip())
            if code != 0:
                raise RuntimeError("DLSS host failed (code %d). See runtime/ReShade.log and dlss5-feed.log." % code)

        def finish(j):
            """Stabilise + write outputs for one frame, then delete its temp files."""
            frame = j["frame"]
            dl = _read_img(j["out"])
            if hdr:
                orig_in = _read_pfm(j["c"])
                dl = _restore_gamut(orig_in, dl)
            if mode == "detail":
                dl = detail_only(orig_in if hdr else _read_pfm(j["c"]), dl, detail_size)
            if stab is not None:
                img = stab.step(orig_in if hdr else _read_pfm(j["c"]), dl,
                                _read_pfm(j["mv"]) if stab.n else None)
            else:
                img = dl
            tmp = os.path.join(work, "f%04d_final.pfm" % frame)
            if hdr:
                shown = _write_outputs(img, frame, name, out_dir, work, fmt, space, aces, log, exposure)
            else:
                ext, depth = FORMATS.get(fmt, ("png", "uint8"))
                if ext == "exr":
                    ext, depth = "png", "uint16"  # the 8-bit path has nothing float to put in an EXR
                shown = os.path.join(out_dir, "%s_dlss5.%04d.%s" % (name, frame, ext))
                _write_pfm(img, tmp)
                if _run([oiio, tmp, "-d", depth, "-o", shown], log)[0] != 0:
                    raise RuntimeError("Could not write " + shown)
            if debug:
                if hdr:
                    gain = 2.0 ** exposure  # same exposure on both sides of the comparison
                    ov = _view(orig_in / SCRGB_WHITE * gain, aces)
                    dv = _view(img / SCRGB_WHITE * gain, aces)
                else:
                    ov, dv = _read_pfm(j["c"]), img
                _write_debug(frame, name, out_dir, work, ov, dv, j["z"], j["mv"], log)
            remove(j["c"], j["z"], j["mv"], j["out"], tmp)
            return shown

        # Chunked so temp data stays bounded: after the first frame is prepared its temp size is
        # measured and chunks are sized to use at most half of the free disk space.
        results, i, chunk, n_chunks = [], 0, len(frames), 0
        while i < len(frames):
            batch = []
            while i < len(frames) and len(batch) < chunk:
                batch.append(prepare(i, *frames[i]))
                i += 1
                if i == 1 and len(frames) > 1:
                    first = batch[0]
                    per = sum(os.path.getsize(p) for p in (first["c"], first["z"], first["mv"]) if p)
                    per += 3 * os.path.getsize(first["c"])  # DLSS output + final / preview temporaries
                    free = shutil.disk_usage(work).free
                    chunk = FORCE_CHUNK or max(1, min(len(frames), int(0.5 * free / max(per, 1))))
                    if chunk < len(frames):
                        log("Disk: ~%d MB temp per frame, %.1f GB free -> processing in chunks of %d frames"
                            % (per / 2 ** 20, free / 2 ** 30, chunk))
            run_host(batch)
            n_chunks += 1
            if stab is not None and n_chunks == 1:
                log("Stabilising along Arnold motion vectors (strength %g)" % stabilise)
            results += [finish(j) for j in batch]
        if stab is not None:
            log("Stabilised %d frame(s) along Arnold motion vectors (strength %g)" % (stab.n, stabilise))
        log("Done: %d frame(s) in %.0f s -> %s" % (len(results), time.time() - t0, out_dir))
        if not KEEP_WORK:
            shutil.rmtree(work, ignore_errors=True)
        done(results, None)
    except Cancelled:
        log("Cancelled.")
        done([], "cancelled")
    except Exception as e:  # report, keep the work folder for inspection
        log("ERROR: %s (work files kept in %s)" % (e, work))
        done([], str(e))
    finally:
        _state["busy"] = False
        _state["proc"] = None


def _pid_alive(pid):
    """True if a process with this id is running (Windows)."""
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def _folder_in_use(path):
    """A work folder is in use while the process that created it (owner.pid) is alive: Maya and
    a mayapy batch can run at the same time and must not delete each other's files."""
    try:
        with open(os.path.join(path, "owner.pid")) as f:
            return _pid_alive(int(f.read().strip()))
    except (IOError, ValueError):
        return False


_HOST_LOCK = os.path.join(RUNTIME, "host.lock")


def _helper_running():
    """True if any dlss5_host.exe process exists."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq dlss5_host.exe", "/NH"],
                             capture_output=True, text=True, creationflags=_NO_WINDOW).stdout
        return "dlss5_host.exe" in out.lower()
    except OSError:
        return True  # can't tell: assume busy, the owner-alive test still applies


def _lock_is_stale(owner):
    """A lock is stale if its owner process is gone, or if no DLSS helper has been running for a
    while even though the owner is still alive (e.g. Maya kept open after an aborted job)."""
    if not owner or not _pid_alive(owner):
        return True
    try:
        age = time.time() - os.path.getmtime(_HOST_LOCK)
    except OSError:
        return True
    return age > 20 and not _helper_running()


def _acquire_host_lock(log):
    """One DLSS 5 helper at a time across processes (Maya + batch jobs share runtime/ and its
    ReShade.ini). A stale lock (see _lock_is_stale) is taken over."""
    waited = False
    while True:
        try:
            fd = os.open(_HOST_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                with open(_HOST_LOCK) as f:
                    owner = int(f.read().strip() or 0)
            except (IOError, ValueError):
                owner = 0
            if owner == os.getpid() or _lock_is_stale(owner):
                try:
                    os.remove(_HOST_LOCK)
                except OSError:
                    pass
                continue
            if not waited:
                log("Waiting for another DLSS 5 job to finish its helper pass...")
                waited = True
            if _state["cancel"]:
                raise Cancelled()
            time.sleep(1)


def _release_host_lock():
    try:
        with open(_HOST_LOCK) as f:
            if int(f.read().strip() or 0) == os.getpid():
                os.remove(_HOST_LOCK)
    except (IOError, ValueError, OSError):
        pass


def _cleanup_old_work(current, log):
    """Delete work folders left behind by failed or cancelled runs (they are kept at the time for
    troubleshooting). Never touches the current run's folder or one another live process owns."""
    if KEEP_WORK or not os.path.isdir(WORK_ROOT):
        return
    freed = 0
    cur = os.path.normcase(os.path.abspath(current))
    for d in os.listdir(WORK_ROOT):
        p = os.path.join(WORK_ROOT, d)
        if (os.path.isdir(p) and os.path.normcase(os.path.abspath(p)) != cur
                and not _folder_in_use(p)):
            for dp, _, fs in os.walk(p):
                freed += sum(os.path.getsize(os.path.join(dp, f)) for f in fs)
            shutil.rmtree(p, ignore_errors=True)
    if freed:
        log("Cleaned up %.0f MB of old work files" % (freed / 2 ** 20))


def _new_work():
    base = os.path.join(WORK_ROOT, time.strftime("%Y%m%d_%H%M%S")).replace("\\", "/")
    work, n = base, 1
    while os.path.exists(work):  # two runs in the same second (Maya + a batch) get separate folders
        work, n = "%s_%d" % (base, n), n + 1
    os.makedirs(work)
    with open(os.path.join(work, "owner.pid"), "w") as f:
        f.write(str(os.getpid()))
    return work


def _start(target, kwargs, block):
    if _state["busy"]:
        raise RuntimeError("DLSS 5 is already processing.")
    _state["busy"] = True
    _state["cancel"] = False
    if block:
        target(**kwargs)
    else:
        threading.Thread(target=target, kwargs=kwargs, daemon=True).start()


def colour_setup():
    """(rendering space, ACES view?) from the scene's colour management (main thread only)."""
    try:
        if cmds.colorManagementPrefs(q=True, cmEnabled=True):
            return (cmds.colorManagementPrefs(q=True, renderingSpaceName=True),
                    "ACES" in (cmds.colorManagementPrefs(q=True, viewTransformName=True) or ""))
    except RuntimeError:
        pass
    return "scene-linear Rec.709-sRGB", False


def _resolve_fmt(fmt, n_frames):
    if fmt in (None, "", "auto"):
        return "png16" if n_frames == 1 else "exr"
    return fmt


def process_scene_frames(start, end, out_dir, intensity=0.5, structure=2.0, fmt="auto",
                         keep_original=True, log=print, done=None, block=False, style=None,
                         motion_vectors=None, stabilise=0.8, hdr=True, debug=False, passes=1, exposure=0.0,
                         mode="full", detail_size=3.0):
    """Render frames start..end of the open scene with Arnold, then DLSS 5 them in order.
    motion_vectors: None = automatic (on for ranges, off for a single frame).
    stabilise: 0 = off, else strength of the motion-vector stabilisation of the DLSS edit
    (ranges with motion vectors only).
    fmt: 'auto' (PNG 16-bit for one frame, EXR for ranges), 'exr', 'png16', 'png8'."""
    write_settings(intensity, structure, style)
    fmt = _resolve_fmt(fmt, end - start + 1)
    space, aces = colour_setup()
    use_mv = (end > start) if motion_vectors is None else bool(motion_vectors)
    work = _new_work()
    log("Exporting frames %d-%d" % (start, end))
    frames, near = export_scene_frames(work, start, end, motion_vectors=use_mv)
    name = os.path.splitext(os.path.basename(cmds.file(q=True, sn=True) or "untitled"))[0] or "untitled"
    _start(_run_pipeline, dict(frames=frames, out_dir=out_dir, name=name, fmt=fmt, near=near, work=work,
                               allow_flat_depth=False, keep_original=keep_original, log=log,
                               done=done or (lambda r, e: None), render_scene=True, use_mv=use_mv,
                               stabilise=stabilise, hdr=hdr, space=space, aces=aces, debug=debug,
                               passes=passes, exposure=exposure, mode=mode, detail_size=detail_size), block)


def find_sequence(one_file):
    """All EXRs in the same numbered sequence as one_file, sorted by frame number."""
    d, base = os.path.split(one_file)
    m = re.match(r"^(.*?)(\d+)(\.exr)$", base, re.IGNORECASE)
    if not m:
        return [(0, one_file)]
    pre, ext = m.group(1), m.group(3)
    seq = []
    for p in glob.glob(os.path.join(d, glob.escape(pre) + "*" + ext)):
        mm = re.match(r"^" + re.escape(pre) + r"(\d+)" + re.escape(ext) + "$", os.path.basename(p), re.IGNORECASE)
        if mm:
            seq.append((int(mm.group(1)), p.replace("\\", "/")))
    return sorted(seq)


def process_exr_sequence(exrs, out_dir, near=0.1, intensity=0.5, structure=2.0, fmt="auto",
                         allow_flat_depth=False, keep_original=True, log=print, done=None, block=False,
                         style=None, hdr=True, debug=False, passes=1, exposure=0.0, mode="full",
                         detail_size=3.0):
    """DLSS 5 an already-rendered EXR sequence: list of (frame, path). The EXRs are assumed to be
    in the scene's rendering space (ACEScg with Maya's default colour management)."""
    write_settings(intensity, structure, style)
    fmt = _resolve_fmt(fmt, len(exrs))
    space, aces = colour_setup()
    work = _new_work()
    name = re.sub(r"[._]+$", "", re.sub(r"\d+\.exr$", "", os.path.basename(exrs[0][1]), flags=re.I)) or "seq"
    frames = [(f, None, p, None) for f, p in exrs]
    _start(_run_pipeline, dict(frames=frames, out_dir=out_dir, name=name, fmt=fmt, near=near, work=work,
                               allow_flat_depth=allow_flat_depth, keep_original=keep_original, log=log,
                               done=done or (lambda r, e: None), render_scene=False, hdr=hdr,
                               space=space, aces=aces, debug=debug, passes=passes, exposure=exposure,
                               mode=mode, detail_size=detail_size), block)


def cancel():
    _state["cancel"] = True
    p = _state["proc"]
    if p is not None and p.poll() is None:
        p.kill()


# ---------------------------------------------------------------------------------------------
# UI

_UI_FORMATS = {
    "Auto (PNG 16-bit single, EXR ranges)": "auto",
    "EXR float, ACEScg (+ 8-bit preview)": "exr",
    "PNG 16-bit (ACES view)": "png16",
    "PNG 8-bit (ACES view)": "png8",
}


def _ui_log(msg):
    print("[DLSS5] " + msg)
    def upd():
        if cmds.control(WIN + "_status", exists=True):
            cmds.text(WIN + "_status", e=True, label=msg)
    maya.utils.executeDeferred(upd)


_SHOW_VAR = "dlss5_showResultWindow"  # Maya optionVar: remembered across sessions, used by Panel + shelf


def _show_result_enabled():
    return bool(cmds.optionVar(q=_SHOW_VAR)) if cmds.optionVar(exists=_SHOW_VAR) else False


def show_in_viewer(results):
    """Open the result in FCheck (Maya's image viewer). It stays open until closed and runs as a
    separate process. A sequence opens as an animation (frames numbered name.####.ext)."""
    if not results:
        return
    fcheck = os.path.join(os.environ.get("MAYA_LOCATION", r"C:\Program Files\Autodesk\Maya2024"), "bin", "fcheck.exe")
    first = results[0]
    args = [fcheck, first]
    if len(results) > 1:
        m = re.match(r"^(.*\.)(-?\d+)(\.\w+)$", first)
        nums = [int(re.match(r"^.*\.(-?\d+)\.\w+$", r).group(1)) for r in results]
        if m:
            args = [fcheck, "-n", str(min(nums)), str(max(nums)), "1", m.group(1) + "#" + m.group(3)]
    try:
        subprocess.Popen(args, creationflags=0x00000008)  # DETACHED_PROCESS: independent of Maya
    except OSError as e:
        print("[DLSS5] could not open FCheck: %s" % e)


def _ui_done(results, err):
    def upd():
        if cmds.control(WIN + "_go", exists=True):
            for b in ("_go", "_range", "_seq"):
                cmds.button(WIN + b, e=True, enable=True)
        if results:
            try:
                cmds.renderWindowEditor("renderView", e=True, loadImage=results[-1])
            except RuntimeError:
                pass
            if _show_result_enabled():
                show_in_viewer(results)
        if err and err != "cancelled":
            cmds.confirmDialog(title="DLSS 5", message=err, button=["OK"])
    maya.utils.executeDeferred(upd)


def _ui_vals():
    return dict(
        intensity=cmds.floatSliderGrp(WIN + "_int", q=True, value=True),
        structure=cmds.floatSliderGrp(WIN + "_str", q=True, value=True),
        style=cmds.optionMenuGrp(WIN + "_sty", q=True, value=True),
        out_dir=cmds.textFieldButtonGrp(WIN + "_out", q=True, text=True),
        fmt=_UI_FORMATS[cmds.optionMenuGrp(WIN + "_fmt", q=True, value=True)],
        keep_original=cmds.checkBox(WIN + "_orig", q=True, value=True),
        debug=cmds.checkBox(WIN + "_dbg", q=True, value=True),
        passes=cmds.intSliderGrp(WIN + "_pas", q=True, value=True),
        exposure=cmds.floatSliderGrp(WIN + "_exp", q=True, value=True),
        mode="detail" if cmds.optionMenuGrp(WIN + "_mode", q=True, value=True).startswith("Detail") else "full",
        detail_size=cmds.floatSliderGrp(WIN + "_dsz", q=True, value=True),
    )


def _ui_stabilise():
    return 0.8 if cmds.checkBox(WIN + "_stab", q=True, value=True) else 0.0


def _ui_busy():
    for b in ("_go", "_range", "_seq"):
        cmds.button(WIN + b, e=True, enable=False)


def _ui_run(fn):
    try:
        fn()
    except Exception as e:
        _state["busy"] = False
        _ui_done([], str(e))
        cmds.warning("DLSS 5: %s" % e)


def _on_current(*_):
    v = _ui_vals()
    f = int(cmds.currentTime(q=True))
    _ui_busy()
    _ui_run(lambda: process_scene_frames(f, f, log=_ui_log, done=_ui_done, **v))


def _on_range(*_):
    v = _ui_vals()
    s, e = cmds.intFieldGrp(WIN + "_rng", q=True, value=True)[:2]
    if e < s:
        cmds.warning("End frame is before start frame.")
        return
    _ui_busy()
    stab = _ui_stabilise()
    _ui_run(lambda: process_scene_frames(s, e, log=_ui_log, done=_ui_done, stabilise=stab, **v))


def _on_sequence(*_):
    picked = cmds.fileDialog2(fileMode=1, caption="Pick any frame of an Arnold EXR sequence",
                              fileFilter="OpenEXR (*.exr)")
    if not picked:
        return
    seq = find_sequence(picked[0])
    has_z = any(c == "Z" or c.endswith(".Z") for c in exr_channels(seq[0][1]))
    flat = False
    if not has_z:
        r = cmds.confirmDialog(title="DLSS 5", button=["Use flat depth", "Cancel"], defaultButton="Cancel",
                               message="This sequence has no Z channel.\nDLSS 5 needs depth; flat depth works "
                                       "but results are less reliable.\n\nAdd a Z AOV and re-render for best results.")
        if r != "Use flat depth":
            return
        flat = True
    v = _ui_vals()
    near = cmds.floatFieldGrp(WIN + "_near", q=True, value1=True)
    _ui_log("Sequence: %d frame(s) %d-%d" % (len(seq), seq[0][0], seq[-1][0]))
    _ui_busy()
    _ui_run(lambda: process_exr_sequence(seq, near=near, allow_flat_depth=flat, log=_ui_log, done=_ui_done, **v))


# ---------------------------------------------------------------------------------------------
# one-click shelf actions (use the saved Strength/Structure and the default output folder)

def default_out_dir():
    return os.path.join(cmds.workspace(q=True, rootDirectory=True), "images", "dlss5").replace("\\", "/")


def _shelf_log(msg):
    print("[DLSS5] " + msg)
    maya.utils.executeDeferred(lambda: cmds.inViewMessage(amg="DLSS 5: " + msg, pos="topCenter", fade=True))


def _shelf_done(results, err):
    def upd():
        if results:
            try:
                cmds.renderWindowEditor("renderView", e=True, loadImage=results[-1])
            except RuntimeError:
                pass
            if _show_result_enabled():
                show_in_viewer(results)
        if err and err != "cancelled":
            cmds.confirmDialog(title="DLSS 5", message=err, button=["OK"])
    maya.utils.executeDeferred(upd)


def quick_frame():
    """Shelf 'Render': current frame -> Arnold -> DLSS 5 -> Render View."""
    intensity, structure = read_settings()
    f = int(cmds.currentTime(q=True))
    try:
        process_scene_frames(f, f, default_out_dir(), intensity=intensity, structure=structure,
                             log=_shelf_log, done=_shelf_done)
    except Exception as e:
        _state["busy"] = False
        cmds.warning("DLSS 5: %s" % e)


def quick_sequence():
    """Shelf 'Seq': pick any frame of an EXR sequence -> DLSS 5 the whole sequence."""
    picked = cmds.fileDialog2(fileMode=1, caption="Pick any frame of an Arnold EXR sequence",
                              fileFilter="OpenEXR (*.exr)")
    if not picked:
        return
    seq = find_sequence(picked[0])
    has_z = any(c == "Z" or c.endswith(".Z") for c in exr_channels(seq[0][1]))
    if not has_z:
        r = cmds.confirmDialog(title="DLSS 5", button=["Use flat depth", "Cancel"], defaultButton="Cancel",
                               message="This sequence has no Z channel.\nDLSS 5 needs depth; flat depth works "
                                       "but results are less reliable.")
        if r != "Use flat depth":
            return
    try:
        near = cmds.getAttr(_render_camera() + ".nearClipPlane")
    except Exception:
        near = 0.1
    intensity, structure = read_settings()
    _shelf_log("sequence of %d frame(s) %d-%d" % (len(seq), seq[0][0], seq[-1][0]))
    try:
        process_exr_sequence(seq, default_out_dir(), near=near, intensity=intensity, structure=structure,
                             allow_flat_depth=not has_z, log=_shelf_log, done=_shelf_done)
    except Exception as e:
        _state["busy"] = False
        cmds.warning("DLSS 5: %s" % e)


def open_output():
    d = default_out_dir()
    os.makedirs(d, exist_ok=True)
    os.startfile(d)


def _on_open(*_):
    d = cmds.textFieldButtonGrp(WIN + "_out", q=True, text=True)
    os.makedirs(d, exist_ok=True)
    os.startfile(d)


def _browse(*_):
    d = cmds.fileDialog2(fileMode=3, caption="Output folder")
    if d:
        cmds.textFieldButtonGrp(WIN + "_out", e=True, text=d[0])


def show():
    if cmds.window(WIN, exists=True):
        cmds.deleteUI(WIN)
    intensity, structure = read_settings()
    ws = cmds.workspace(q=True, rootDirectory=True)
    out = os.path.join(ws, "images", "dlss5").replace("\\", "/")
    try:
        near = cmds.getAttr(_render_camera() + ".nearClipPlane")
    except Exception:
        near = 0.1
    start = int(cmds.playbackOptions(q=True, minTime=True))
    end = int(cmds.playbackOptions(q=True, maxTime=True))

    cmds.window(WIN, title="DLSS 5 Enhanced Detail", widthHeight=(440, 380))
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6, columnAttach=("both", 8))
    cmds.text(label="Arnold render -> DLSS 5 neural rendering, same resolution (no upscaling)",
              align="left", font="smallObliqueLabelFont")
    cmds.floatSliderGrp(WIN + "_int", label="Strength", field=True, minValue=0.0, maxValue=1.0,
                        value=intensity, precision=2, columnWidth3=(80, 50, 280),
                        annotation="How much of the DLSS 5 result is used (RenoDX NR Intensity). "
                                   "0.25 = subtle, keeps a stylised design; 0.98 = full photoreal. "
                                   "Values above 1 have no extra effect (the model caps at 1).")
    cmds.floatSliderGrp(WIN + "_str", label="Structure", field=True, minValue=0.0, maxValue=2.0,
                        value=structure, precision=2, columnWidth3=(80, 50, 280),
                        annotation="Fine-detail strength of the neural pass (RenoDX Structure Intensity): contact "
                                   "shadows, micro detail, SSS. 2 = maximum; higher values are ignored.")
    cmds.intSliderGrp(WIN + "_pas", label="Passes", field=True, minValue=1, maxValue=3, value=1,
                      columnWidth3=(80, 50, 280),
                      annotation="Run DLSS 5 again on its own output. 1 = normal, 2 = strong photoreal (sweet spot "
                                 "on faces), 3 = overcooked: faces age and drift, environments soften more")
    cmds.optionMenuGrp(WIN + "_mode", label="Mode", columnWidth2=(80, 300),
                       annotation="Full: DLSS 5's complete look. Detail only: keep Arnold's lighting/colour/SSS "
                                  "and add only DLSS 5's fine skin/surface detail (avoids the 'game scan' look)")
    cmds.menuItem(label="Full (DLSS 5 look)")
    cmds.menuItem(label="Detail only (keep Arnold look)")
    cmds.floatSliderGrp(WIN + "_dsz", label="Detail size", field=True, minValue=1.0, maxValue=8.0, value=3.0,
                        precision=1, columnWidth3=(80, 50, 280),
                        annotation="Detail only: how coarse the taken DLSS detail is, in pixels. 1.5 = pores only, "
                                   "3 = pores + small creases (recommended), 6+ = includes more of DLSS's shading")
    cmds.floatSliderGrp(WIN + "_exp", label="Exposure", field=True, minValue=-2.0, maxValue=2.0, value=0.0,
                        precision=2, columnWidth3=(80, 50, 280),
                        annotation="Stops applied to the DLSS 5 output (EXR and PNG), after the DLSS pass. "
                                   "The saved original is not changed.")
    cmds.optionMenuGrp(WIN + "_sty", label="Style", columnWidth2=(80, 120),
                       annotation="DLSS 5 grading style (RenoDX NR Style). Default = the game setting, best detail. "
                                  "Natural = flatter and softer. Cinematic = deeper shadows, softens more.")
    for s in STYLES:
        cmds.menuItem(label=s)
    cmds.optionMenuGrp(WIN + "_sty", e=True, value=read_style())
    cmds.textFieldButtonGrp(WIN + "_out", label="Folder", text=out, buttonLabel="...",
                            buttonCommand=_browse, columnWidth3=(80, 300, 40),
                            annotation="Where results are saved. Default: <project>/images/dlss5. "
                                       "Rendering the same frame again overwrites its files.")
    cmds.optionMenuGrp(WIN + "_fmt", label="Format", columnWidth2=(80, 260),
                       annotation="Auto = PNG 16-bit for one frame, EXR for ranges. EXR = half float in the "
                                  "rendering space (ACEScg), for compositing, plus an 8-bit preview. "
                                  "PNG 16/8 = finished image through your ACES view.")
    for f in _UI_FORMATS:
        cmds.menuItem(label=f)
    cmds.checkBox(WIN + "_orig", label="Also save original Arnold frame as PNG", value=True,
                  annotation="Saves Arnold's own render (through your view transform) next to the DLSS 5 "
                             "result, so you can flip between them.")
    cmds.checkBox(WIN + "_dbg", label="Save debug images (compare, change map, DLSS inputs) in output/debug", value=False,
                  annotation="Per frame: compare = original | DLSS 5; diff = where DLSS 5 changed things locally "
                             "(heat map); inputs = what DLSS 5 received (colour | depth | motion vectors).")
    cmds.checkBox(WIN + "_show", label="Show result in a viewer window when done (stays open; also for the shelf)",
                  value=_show_result_enabled(),
                  changeCommand=lambda v: cmds.optionVar(intValue=(_SHOW_VAR, int(bool(v)))),
                  annotation="Opens the result in FCheck when finished (sequences play as an animation). "
                             "Remembered between sessions and also used by the shelf Render button.")
    cmds.separator(height=6)
    cmds.button(WIN + "_go", label="Process Current Frame", height=32, command=_on_current,
                annotation="Renders the current frame with Arnold, runs DLSS 5 and shows it in the Render View. "
                           "A helper window appears for a few seconds - leave it alone.")
    cmds.intFieldGrp(WIN + "_rng", label="Frame range", numberOfFields=2, value1=start, value2=end,
                     columnWidth3=(80, 60, 60),
                     annotation="First and last frame for Process Frame Range.")
    cmds.checkBox(WIN + "_stab", label="Stabilise frame ranges (reduces DLSS 5 wobble, uses motion vectors)",
                  value=True,
                  annotation="DLSS 5 re-invents fine detail every frame, which wobbles. This carries the "
                             "change along Arnold's exact motion vectors (about 30% less wobble on faces).")
    cmds.button(WIN + "_range", label="Process Frame Range (render + DLSS 5)", command=_on_range,
                annotation="Renders the range with a Z pass and motion vectors, runs DLSS 5 over it in order, "
                           "then stabilises. Long shots are processed in chunks to limit disk use.")
    cmds.floatFieldGrp(WIN + "_near", label="Near clip", value1=near, precision=4, columnWidth2=(80, 80),
                       annotation="Camera near clip used to encode depth for existing EXR sequences. "
                                  "Scene renders use the render camera's own value.")
    cmds.button(WIN + "_seq", label="Process EXR Sequence...", command=_on_sequence,
                annotation="Pick any frame of an already-rendered EXR sequence (needs a Z channel; flat depth "
                           "is offered otherwise). Your EXR files are never changed.")
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=1)
    cmds.button(label="Open Output Folder", command=_on_open, annotation="Opens the Folder above in Explorer.")
    cmds.button(label="Cancel", command=lambda *_: cancel(),
                annotation="Stops the running job (Arnold render or DLSS 5 pass).")
    cmds.setParent("..")
    cmds.text(WIN + "_status", label="Ready.", align="left", annotation="Progress of the current job.")
    cmds.showWindow(WIN)
