"""Push the RenoDX 'reality' settings past their UI ranges on one kept frame and measure the face.

Usage:  mayapy sweep_reality.py <work_dir_with_f####_c.pfm/_z.pfm> <out_dir>
Face box is for the user's 960x540 head render.
"""
import glob
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maya"))
work, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT = os.path.join(ROOT, "runtime")
INI = os.path.join(RT, "ReShade.ini")
OIIO = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
c = glob.glob(os.path.join(work, "*_c.pfm"))[0]
z = glob.glob(os.path.join(work, "*_z.pfm"))[0]
FACE = (slice(70, 300), slice(380, 580))
BASE = {"NeuralUplift": "1", "NRIntensity": "0.98", "NRLocalStructure": "2", "NRSkinStructure": "1.97",
        "NRGlobalTone": "2", "NRLocalTone": "1.15", "NRStyle": "0", "NRPreset": "1"}
VARIANTS = [
    ("base", {}),
    ("intensity 1.5", {"NRIntensity": "1.5"}),
    ("intensity 2", {"NRIntensity": "2"}),
    ("skin 3", {"NRSkinStructure": "3"}),
    ("skin 5", {"NRSkinStructure": "5"}),
    ("structure 4", {"NRLocalStructure": "4"}),
    ("all pushed", {"NRIntensity": "1.5", "NRSkinStructure": "4", "NRLocalStructure": "4"}),
]

ini_orig = open(INI).read()


def read_pfm(path):
    with open(path, "rb") as f:
        tag = f.readline().strip()
        w, h = map(int, f.readline().split())
        scale = float(f.readline())
        ch = 3 if tag == b"PF" else 1
        d = np.frombuffer(f.read(), dtype="<f4" if scale < 0 else ">f4").reshape(h, w, ch)
    return np.flipud(d).astype(np.float64)


import dlss5_enhance as de  # for the ACES view and gamut restore (no Maya needed for these)

orig = read_pfm(c)
res = {}
try:
    for name, over in VARIANTS:
        vals = dict(BASE, **over)
        txt = ini_orig
        for k, v in vals.items():
            txt = re.sub(r"(?m)^%s=.*$" % k, "%s=%s" % (k, v), txt)
        open(INI, "w").write(txt)
        o = os.path.join(out, re.sub(r"\W+", "_", name) + ".pfm")
        subprocess.run([de.HOST, "--color", c, "--depth", z, "--out", o, "--hdr", "1"], cwd=RT, capture_output=True)
        log = open(os.path.join(RT, "ReShade.log"), errors="replace").read()
        active = re.findall(r"active settings: (.*)", log)
        res[name] = (de._restore_gamut(orig, read_pfm(o)), active[0] if active else "?")
finally:
    open(INI, "w").write(ini_orig)

ov = de._view(orig / de.SCRGB_WHITE, True)
base_face = None
print("%-14s %10s %12s   RenoDX reports" % ("variant", "face change", "vs base"))
for name, (img, active) in res.items():
    v = de._view(img / de.SCRGB_WHITE, True)
    fc = np.mean(np.abs(v[FACE] - ov[FACE]))
    if base_face is None:
        base_face = v
    vb = np.mean(np.abs(v[FACE] - base_face[FACE]))
    print("%-14s %10.4f %12.4f   %s" % (name, fc, vb, active[:110]))
    tmp = os.path.join(out, "view_" + re.sub(r"\W+", "_", name) + ".pfm")
    de._write_pfm(v[FACE], tmp)
    subprocess.run([OIIO, tmp, "-d", "uint8", "-o", tmp[:-4] + ".png"], check=True)
de._write_pfm(ov[FACE], os.path.join(out, "view_original.pfm"))
subprocess.run([OIIO, os.path.join(out, "view_original.pfm"), "-d", "uint8", "-o", os.path.join(out, "view_original.png")], check=True)
tiles = [os.path.join(out, "view_original.png")] + [os.path.join(out, "view_" + re.sub(r"\W+", "_", n) + ".png") for n, _ in VARIANTS]
subprocess.run([OIIO] + tiles + ["--mosaic", "4x2", "-o", os.path.join(out, "sheet.png")], check=True)
