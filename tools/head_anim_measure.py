"""Flicker of the DLSS 5 edit on the head animation (test/head_anim), whole frame and face box.

Run:  mayapy head_anim_measure.py
"""
import glob
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from measure_detail import load

D = os.path.join(ROOT, "test", "head_anim")
orig = [load(p) for p in sorted(glob.glob(os.path.join(D, "*_original.*.png")))]
dl = [load(p) for p in sorted(glob.glob(os.path.join(D, "*_dlss5.*.png")))]
print("frames:", len(orig), len(dl))
face = (slice(110, 360), slice(340, 620))  # rows, cols at 960x540
for name, sl in (("whole frame", (slice(None), slice(None))), ("face box", face)):
    delta = [d[sl] - o[sl] for d, o in zip(dl, orig)]
    fl = np.mean([np.mean(np.abs(delta[i] - delta[i - 1])) for i in range(1, len(delta))])
    mag = np.mean([np.mean(np.abs(x)) for x in delta])
    real = np.mean([np.mean(np.abs(orig[i][sl] - orig[i - 1][sl])) for i in range(1, len(orig))])
    print("%-11s DLSS change %.4f | frame-to-frame jump of the change %.4f (ratio %.2f) | real motion %.4f"
          % (name, mag, fl, fl / mag, real))

oiio = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
args = [oiio]
for f in (1, 4, 7, 10):
    for tag in ("original", "dlss5"):
        args += [glob.glob(os.path.join(D, "*_%s.%04d.png" % (tag, f)))[0], "--cut", "280x250+340+110"]
subprocess.run(args + ["--mosaic", "2x4", "-o", os.path.join(D, "sheet.png")], check=True)
