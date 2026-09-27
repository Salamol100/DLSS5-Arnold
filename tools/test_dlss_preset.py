"""DLSS (DLAA) model preset before the neural pass: dlss5-feed.cfg 'preset'. Others report preset M
keeps ~30% more detail than Default (github.com/Blueforcer/ComfyUI-DLSS5-Enhancer).
NGX DLSS render presets: 0 = Default, 10 = J, 11 = K, 12 = L, 13 = M.

Usage:  mayapy test_dlss_preset.py <work_dir> <out_dir>
"""
import glob
import os
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dlss5_enhance as de
from measure_detail import box_blur, luma

work, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
RT = de.RUNTIME
OIIO = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
c = glob.glob(os.path.join(work, "*_c.pfm"))[0]
z = glob.glob(os.path.join(work, "*_z.pfm"))[0]
CFG = os.path.join(RT, "dlss5-feed.cfg")
cfg0 = open(CFG).read()
FACE = (slice(70, 300), slice(380, 580))
view = lambda x: de._view(x / de.SCRGB_WHITE, True)
orig = view(de._read_pfm(c))


def detail(v, s=FACE):
    l = luma(v[s])
    return np.sqrt(np.mean((l - box_blur(l)) ** 2))


print("%-9s %12s %12s   feed log" % ("preset", "face detail", "face change"))
print("%-9s %12.5f %12s" % ("original", detail(orig), "-"))
tiles = []
try:
    for name, val in (("Default", 0), ("J", 10), ("K", 11), ("L", 12), ("M", 13)):
        open(CFG, "w").write(re.sub(r"(?m)^preset=.*$", "preset=%d" % val, cfg0))
        o = os.path.join(out, "preset_%s.pfm" % name)
        subprocess.run([de.HOST, "--color", c, "--depth", z, "--out", o, "--hdr", "1"], cwd=RT, capture_output=True)
        feed = open(os.path.join(RT, "dlss5-feed.log"), errors="replace").read()
        info = re.findall(r"(preset[^\n]{0,80})", feed)
        v = view(de._restore_gamut(de._read_pfm(c), de._read_pfm(o)))
        print("%-9s %12.5f %12.4f   %s" % (name, detail(v), np.mean(np.abs(v[FACE] - orig[FACE])),
                                          (info[-1] if info else "")[:70]))
        de._write_pfm(v[FACE], o[:-4] + "_face.pfm")
        subprocess.run([OIIO, o[:-4] + "_face.pfm", "-d", "uint8", "-o", o[:-4] + "_face.png"], check=True)
        tiles.append(o[:-4] + "_face.png")
finally:
    open(CFG, "w").write(cfg0)
subprocess.run([OIIO] + tiles + ["--mosaic", "5x1", "--resize", "1500x345", "-o", os.path.join(out, "sheet.png")], check=True)
