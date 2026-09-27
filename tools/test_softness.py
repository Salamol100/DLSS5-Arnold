"""Does DLSS 5's effect flip with input sharpness? Blur the clean Arnold frame by several amounts,
run each through DLSS 5 (the user's game settings), measure detail and distance to the sharp
original.

Run:  mayapy test_softness.py
"""
import os
import re
import shutil
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from measure_detail import box_blur, load, luma

SRC = os.path.join(ROOT, "test", "screen")       # input.png (ACES view), z.pfm
OUT = os.path.join(ROOT, "test", "softness")
RT = os.path.join(ROOT, "runtime")
OIIO = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)

ini_path = os.path.join(RT, "ReShade.ini")
ini_orig = open(ini_path).read()
game = {"NeuralUplift": "1", "NRIntensity": "0.98", "NRStyle": "0", "NRGlobalTone": "2",
        "NRLocalTone": "1.15", "NRLocalStructure": "2", "NRSkinStructure": "1.97", "NRPreset": "1"}
ini = ini_orig
for k, v in game.items():
    ini = re.sub(r"(?m)^%s=.*$" % k, "%s=%s" % (k, v), ini)

BLURS = [0, 0.7, 1.2, 2.0]   # gaussian sigma in pixels
try:
    open(ini_path, "w").write(ini)
    for s in BLURS:
        tag = "b%.1f" % s
        src = os.path.join(OUT, tag + "_in.png")
        args = [OIIO, os.path.join(SRC, "input.png")]
        if s > 0:
            w = int(np.ceil(s * 3)) * 2 + 1
            args += ["--blur:kernel=gaussian", "%dx%d" % (w, w)]
        subprocess.run(args + ["-o", src], check=True)
        pfm = os.path.join(OUT, tag + "_c.pfm")
        subprocess.run([OIIO, src, "--ch", "R,G,B", "-d", "float", "-o", pfm], check=True)
        subprocess.run([os.path.join(RT, "dlss5_host.exe"), "--color", pfm, "--depth", os.path.join(SRC, "z.pfm"),
                        "--out", os.path.join(OUT, tag + "_out.ppm"), "--display-referred", "1"],
                       cwd=RT, check=True, capture_output=True)
finally:
    open(ini_path, "w").write(ini_orig)


def detail(img):
    l = luma(img)[150:]
    return np.sqrt(np.mean((l - box_blur(l)) ** 2))


sharp = load(os.path.join(SRC, "input.png"))
print("sigma   in-detail  out-detail  change    | RMSE to sharp: in     out   (lower = closer)")
for s in BLURS:
    tag = "b%.1f" % s
    a, b = load(os.path.join(OUT, tag + "_in.png")), load(os.path.join(OUT, tag + "_out.ppm"))
    da, db = detail(a), detail(b)
    ra = np.sqrt(np.mean((a[150:] - sharp[150:]) ** 2))
    rb = np.sqrt(np.mean((b[150:] - sharp[150:]) ** 2))
    print("%4.1f   %9.5f  %10.5f  %+6.1f%%   |              %.4f  %.4f" % (s, da, db, 100 * (db / da - 1), ra, rb))

args = [OIIO]
for s in BLURS:
    tag = "b%.1f" % s
    for f in (tag + "_in.png", tag + "_out.ppm"):
        args += [os.path.join(OUT, f), "--cut", "420x300+420+160"]
subprocess.run(args + ["--mosaic", "2x4", "-o", os.path.join(OUT, "sheet.png")], check=True)
