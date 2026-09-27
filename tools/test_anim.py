"""10-frame animation test: DLSS 5 with zero motion vectors vs Arnold's exact motionvector AOV.
Measures detail per frame and flicker. (LumeniteFX optical flow was measured in an earlier
version of this test: worse than zero; see Docs/TEST_RESULTS.md.)

Run:  mayapy test_anim.py
"""
import glob
import os
import re
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
from measure_detail import box_blur, load, luma

OUT = os.path.join(ROOT, "test", "anim_mv").replace("\\", "/")
N = 10

cmds.file(os.path.join(ROOT, "test", "test_anim.ma"), open=True, force=True)
shutil.rmtree(OUT, ignore_errors=True)
render = os.path.join(OUT, "render").replace("\\", "/")
os.makedirs(render)
jobs, near = de.export_scene_frames(render, 1, N, motion_vectors=True)
for frame, ass, exr, png in jobs:
    print("kick frame", frame)
    de._kick(ass, print)

ini_orig = open(de.INI).read()
preset_path = os.path.join(de.RUNTIME, "ReShadePreset.ini")
preset_orig = open(preset_path).read()
runs = {}
try:
    de.write_settings(0.98, 2.0, "Natural")  # overwritten below: NRStyle=0 is what we want
    txt = re.sub(r"(?m)^NRStyle=.*$", "NRStyle=0", open(de.INI).read())
    open(de.INI, "w").write(txt)
    for tag, use_mv in (("zero", False), ("arnold", True)):
        work = de._new_work()
        frames = [(f, None, shutil.copy(exr, work), shutil.copy(png, work)) for f, _, exr, png in jobs]
        got = {}
        de._state["busy"] = True
        de._run_pipeline(frames, OUT, tag, "png", near, work, False, tag == "zero", print,
                         lambda r, e: got.update(r=r, e=e), render_scene=False, use_mv=use_mv)
        runs[tag] = got.get("r") or []
        print("%s: %d outputs, error %s" % (tag, len(runs[tag]), got.get("e")))
finally:
    open(de.INI, "w").write(ini_orig)
    open(preset_path, "w").write(preset_orig)

orig = [load(p) for p in sorted(glob.glob(os.path.join(OUT, "zero_original.*.png")))]


def detail(img):
    l = luma(img)[150:]
    return np.sqrt(np.mean((l - box_blur(l)) ** 2))


seqs = {t: [load(r) for r in runs[t]] for t in runs}
print("\nframe  orig-detail  " + "  ".join("%s-detail" % t for t in seqs))
for i in range(N):
    d0 = detail(orig[i])
    print("%5d  %10.5f  " % (i + 1, d0) + "  ".join("%+11.1f%%" % (100 * (detail(seqs[t][i]) / d0 - 1)) for t in seqs))

print("\nflicker = mean |(dlss_t - orig_t) - (dlss_t-1 - orig_t-1)| relative to the DLSS change size")
for t, s in seqs.items():
    delta = [s[i][150:] - orig[i][150:] for i in range(N)]
    fl = np.mean([np.mean(np.abs(delta[i] - delta[i - 1])) for i in range(1, N)])
    mag = np.mean([np.mean(np.abs(d)) for d in delta])
    print("%-7s flicker %.4f  change %.4f  ratio %.2f" % (t, fl, mag, fl / mag))

oiio = os.path.join(de.arnold_bin(), "oiiotool.exe")
args = [oiio]
for f in (1, 4, 7, 10):
    for tag in ("zero_original", "zero_dlss5", "arnold_dlss5"):
        args += [os.path.join(OUT, "%s.%04d.png" % (tag, f)), "--cut", "420x300+420+160"]
subprocess.run(args + ["--mosaic", "3x4", "-o", os.path.join(OUT, "sheet.png")], check=True)
maya.standalone.uninitialize()
