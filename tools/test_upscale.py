"""Can DLSS 5 upscale a half-resolution Arnold render (render ~4x faster) and still add the neural pass?

Uses a kept full-res frame (work dir with f####_c.pfm scRGB and f####_z.pfm):
  ref    : full-res input, normal DLSS 5 (what the tool does today)
  up     : half-res input, --upscale 2, feed work_resolution=50 + work_upscale=1
  up_nr  : same + RenoDX NREnableUpscaling=1
  resize : half-res input, plain bilinear enlarge (no DLSS) - baseline
Everything compared to the full-res Arnold original (ACES view), face box and whole frame.

Usage:  mayapy test_upscale.py <work_dir> <out_dir>
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
full = de._read_pfm(c)
H, W = full.shape[:2]
ch, zh = os.path.join(out, "half_c.pfm"), os.path.join(out, "half_z.pfm")
subprocess.run([OIIO, c, "--resize:filter=box", "%dx%d" % (W // 2, H // 2), "-o", ch], check=True)
subprocess.run([OIIO, z, "--resample", "%dx%d" % (W // 2, H // 2), "-o", zh], check=True)

INI = os.path.join(RT, "ReShade.ini")
CFG = os.path.join(RT, "dlss5-feed.cfg")
ini0, cfg0 = open(INI).read(), open(CFG).read()


def run(tag, color, depth, upscale, nr_up):
    cfg = re.sub(r"(?m)^work_resolution=.*$", "work_resolution=%d" % (100 // upscale), cfg0)
    cfg = re.sub(r"(?m)^work_upscale=.*$", "work_upscale=%d" % (1 if upscale > 1 else 0), cfg)
    open(CFG, "w").write(cfg)
    open(INI, "w").write(re.sub(r"(?m)^NREnableUpscaling=.*$", "NREnableUpscaling=%d" % nr_up, ini0))
    o = os.path.join(out, tag + ".pfm")
    subprocess.run([de.HOST, "--color", color, "--depth", depth, "--out", o, "--hdr", "1", "--upscale", str(upscale)],
                   cwd=RT, capture_output=True)
    feed = open(os.path.join(RT, "dlss5-feed.log"), errors="replace").read()
    b = re.findall(r"building: (.*)", feed)
    log = open(os.path.join(RT, "ReShade.log"), errors="replace").read()
    nr = re.findall(r"feature 18 created (.*)", log)
    print("%-6s feed: %s" % (tag, b[-1][:90] if b else "?"))
    print("       NR:   %s" % (nr[-1][:100] if nr else "NOT created"))
    return de._read_pfm(o)


try:
    res = {"ref": run("ref", c, z, 1, 0), "up": run("up", ch, zh, 2, 0), "up_nr": run("up_nr", ch, zh, 2, 1)}
finally:
    open(INI, "w").write(ini0)
    open(CFG, "w").write(cfg0)
tmp = os.path.join(out, "resize.pfm")
subprocess.run([OIIO, ch, "--resize:filter=triangle", "%dx%d" % (W, H), "-o", tmp], check=True)
res["resize"] = de._read_pfm(tmp)

view = lambda x: de._view(x / de.SCRGB_WHITE, True)
orig = view(full)
FACE = (slice(70, 300), slice(380, 580))


def detail(v, s):
    l = luma(v[s])
    return np.sqrt(np.mean((l - box_blur(l)) ** 2))


print("\n%-7s %14s %14s %12s" % ("", "face vs orig", "whole vs orig", "face detail"))
print("%-7s %14s %14s %12.5f" % ("orig", "-", "-", detail(orig, FACE)))
tiles = []
for k in ("ref", "up", "up_nr", "resize"):
    v = view(res[k])
    print("%-7s %14.4f %14.4f %12.5f" % (k, np.mean(np.abs(v[FACE] - orig[FACE])), np.mean(np.abs(v - orig)), detail(v, FACE)))
    p = os.path.join(out, "view_%s.pfm" % k)
    de._write_pfm(v[FACE], p)
    subprocess.run([OIIO, p, "-d", "uint8", "-o", p[:-4] + ".png"], check=True)
    tiles.append(p[:-4] + ".png")
p = os.path.join(out, "view_orig.pfm")
de._write_pfm(orig[FACE], p)
subprocess.run([OIIO, p, "-d", "uint8", "-o", p[:-4] + ".png"], check=True)
subprocess.run([OIIO, p[:-4] + ".png"] + tiles + ["--mosaic", "5x1", "--resize", "1500x345", "-o",
                os.path.join(out, "sheet.png")], check=True)
