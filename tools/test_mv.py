"""Verify Arnold motion vectors before feeding them to DLSS 5.

1. Render frames 1-4 of test_anim.ma with the motionvector AOV (dlss5_enhance MV export).
2. The beauty must match the earlier non-MV render (instantaneous shutter = no motion blur).
3. Backward-warp test: prev(x + sx*dx, y + sy*dy) should reproduce cur(x, y) far better than
   zero motion, for exactly one sign convention (sx, sy).

Run:  mayapy test_mv.py
"""
import glob
import os
import shutil
import subprocess
import sys

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dlss5_enhance as de
from measure_detail import load

OUT = os.path.join(ROOT, "test", "mv").replace("\\", "/")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
cmds.file(os.path.join(ROOT, "test", "test_anim.ma"), open=True, force=True)
if "--vertical" in sys.argv:
    # Crane move so the y convention is unambiguous (not saved to the scene file).
    cam_t = cmds.listRelatives(de._render_camera(), parent=True)[0]
    cmds.cutKey(cam_t, attribute=["translateX", "rotateY"], clear=True)
    cmds.setKeyframe(cam_t, attribute="translateY", time=1, value=2.6)
    cmds.setKeyframe(cam_t, attribute="translateY", time=4, value=3.8)
    cmds.keyTangent(cam_t, inTangentType="linear", outTangentType="linear")
jobs, near = de.export_scene_frames(OUT, 1, 4, motion_vectors=True)
for f, ass, exr, png in jobs:
    de._kick(ass, print)

print("channels:", de.exr_channels(jobs[0][2]))
oiio = os.path.join(de.arnold_bin(), "oiiotool.exe")


def read_pfm(path):
    with open(path, "rb") as f:
        tag = f.readline().strip()
        w, h = map(int, f.readline().split())
        scale = float(f.readline())
        c = 3 if tag == b"PF" else 1
        d = np.frombuffer(f.read(), dtype="<f4" if scale < 0 else ">f4").reshape(h, w, c)
    return np.flipud(d).astype(np.float64)


def mv_of(exr):
    chans = de.exr_channels(exr)
    names = [c for c in chans if c.lower().startswith("motionvector")][:2]
    tmp = exr + ".mv.pfm"
    subprocess.run([oiio, exr, "--ch", ",".join(names + [names[0]]), "-o", tmp], check=True, capture_output=True)
    return read_pfm(tmp)[..., :2], names


def bilinear(img, x, y):
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return ((img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy) +
            (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy)


beauty = [load(png) for _, _, _, png in jobs]
earlier = sorted(glob.glob(os.path.join(ROOT, "test", "anim", "mv0_original.000[1-4].png")))
for i, p in enumerate(earlier):
    print("frame %d beauty vs earlier non-MV render: mean abs diff %.4f" % (i + 1, np.mean(np.abs(beauty[i] - load(p)))))

h, w = beauty[0].shape[:2]
yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
for k in range(1, 4):
    mv, names = mv_of(jobs[k][2])
    dx, dy = mv[..., 0], mv[..., 1]
    cur, prev = beauty[k], beauty[k - 1]
    base = np.mean(np.abs(prev - cur))
    res = {}
    for sx in (1, -1):
        for sy in (1, -1):
            res[(sx, sy)] = np.mean(np.abs(bilinear(prev, xx + sx * dx, yy + sy * dy) - cur))
    best = min(res, key=res.get)
    scales = {s: np.mean(np.abs(bilinear(prev, xx + best[0] * s * dx, yy + best[1] * s * dy) - cur))
              for s in (0.5, 1.0, 1.8, 1.9, 2.0, 2.1, 2.2)}
    print("   scale test with best signs: " + " ".join("x%.1f %.4f" % kv for kv in scales.items()))
    print("frame %d: |mv| mean %.2f px max %.2f px (%s) | error zero-MV %.4f | " % (
        k + 1, np.mean(np.hypot(dx, dy)), np.max(np.hypot(dx, dy)), "/".join(names), base) +
        " ".join("s%+d%+d %.4f" % (a, b, v) for (a, b), v in res.items()) + " | best %s" % (best,))
maya.standalone.uninitialize()
