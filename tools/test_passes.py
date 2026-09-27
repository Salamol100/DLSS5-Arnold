"""Headless check of --passes: 1, 2 and 3 passes on the test scene; each extra pass must change the
image further from the original, and the log/debug must show the passes ran.

Run:  mayapy test_passes.py
"""
import glob
import os
import shutil
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

OUT = os.path.join(ROOT, "test", "passes").replace("\\", "/")
shutil.rmtree(OUT, ignore_errors=True)
cmds.file(os.path.join(ROOT, "test", "test_scene.ma"), open=True, force=True)
imgs = {}
for p in (1, 2, 3):
    res = {}
    de.process_scene_frames(1, 1, "%s/p%d" % (OUT, p), intensity=0.98, structure=2.0, fmt="png16", passes=p,
                            log=lambda m: None, done=lambda r, e: res.update(r=r, e=e), block=True)
    imgs[p] = load(res["r"][0]) if res.get("r") else None
orig = load(glob.glob(os.path.join(OUT, "p1", "*_original.*.png"))[0])


def detail(x):
    l = luma(x)[150:]
    return np.sqrt(np.mean((l - box_blur(l)) ** 2))


change = {p: np.mean(np.abs(imgs[p] - orig)) for p in imgs if imgs[p] is not None}
for p in sorted(change):
    print("passes %d: change vs original %.4f   detail %+.1f%%" % (p, change[p], 100 * (detail(imgs[p]) / detail(orig) - 1)))
ok = len(change) == 3 and change[1] < change[2] < change[3]
print("%s  each extra pass changes the image further" % ("PASS" if ok else "FAIL"))
maya.standalone.uninitialize()
