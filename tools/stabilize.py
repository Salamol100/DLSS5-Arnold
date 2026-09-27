"""Temporal stabilisation of the DLSS 5 edit using Arnold's exact motion vectors.

For each frame t:  delta_t = dlss_t - orig_t   (display-referred)
                   S_t = a * warp(S_t-1, mv_t) + (1 - a) * delta_t      (where history is valid)
                   out_t = orig_t + S_t
Warp convention (verified in test_mv.py): previous position = (x - 2*dx, y + 2*dy).
History is rejected where the warped previous ORIGINAL disagrees with the current original
(disocclusion / lighting change), so the fresh DLSS edit is used there.

Usage:  mayapy stabilize.py <run_out_dir> <work_dir> [alpha=0.6]
Writes <run_out_dir>/stab_a<alpha>/<name>_stab.####.png and a side-by-side sequence.
"""
import glob
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_detail import load

OIIO = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
run, work = sys.argv[1], sys.argv[2]
alpha = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6


def read_pfm(path):
    with open(path, "rb") as f:
        tag = f.readline().strip()
        w, h = map(int, f.readline().split())
        scale = float(f.readline())
        c = 3 if tag == b"PF" else 1
        d = np.frombuffer(f.read(), dtype="<f4" if scale < 0 else ">f4").reshape(h, w, c)
    return np.flipud(d).astype(np.float64)


def bilinear(img, x, y):
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return ((img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy) +
            (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy)


def write_png(img, path):
    h, w = img.shape[:2]
    tmp = path + ".pfm"
    with open(tmp, "wb") as f:
        f.write(b"PF\n%d %d\n-1.0\n" % (w, h))
        f.write(np.flipud(np.clip(img, 0, 1)).astype("<f4").tobytes())
    subprocess.run([OIIO, tmp, "-d", "uint8", "-o", path], check=True)
    os.remove(tmp)


origs = sorted(glob.glob(os.path.join(run, "*_original.*.png")))
dlss = sorted(glob.glob(os.path.join(run, "*_dlss5.*.png")))
frames = [int(re.search(r"\.(-?\d+)\.png$", p).group(1)) for p in origs]
out = os.path.join(run, "stab_a%g" % alpha)
os.makedirs(os.path.join(out, "sbs"), exist_ok=True)

S = prev_o = None
for i, f in enumerate(frames):
    o, d = load(origs[i]), load(dlss[i])
    delta = d - o
    if S is None:
        S = delta
    else:
        mv = read_pfm(os.path.join(work, "f%04d_mv.pfm" % f))
        h, w = o.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        px, py = xx - 2 * mv[..., 0], yy + 2 * mv[..., 1]
        hist = bilinear(S, px, py)
        agree = np.mean(np.abs(bilinear(prev_o, px, py) - o), axis=2, keepdims=True)
        valid = np.clip(1.0 - (agree - 0.03) / 0.05, 0.0, 1.0)  # full trust < 0.03, none > 0.08
        valid *= ((px >= 0) & (px <= w - 1) & (py >= 0) & (py <= h - 1))[..., None]
        a = alpha * valid
        S = a * hist + (1 - a) * delta
    prev_o = o
    res = o + S
    name = os.path.basename(origs[i]).replace("_original.", "_stab.")
    write_png(res, os.path.join(out, name))
    write_png(np.concatenate([o, d, res], axis=1), os.path.join(out, "sbs", "sbs.%04d.png" % f))
print("wrote", len(frames), "frames to", out)
