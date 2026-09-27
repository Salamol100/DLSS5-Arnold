"""Motion-compensated flicker of the DLSS 5 edit: |delta_t - warp(delta_t-1)| in a face box.
A perfectly stable edit that moves with the object scores 0.

Usage:  mayapy mc_flicker.py <run_out_dir> <work_dir>
"""
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_detail import load

run, work = sys.argv[1], sys.argv[2]
FACE = (slice(110, 360), slice(340, 620))


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


orig = [load(p) for p in sorted(glob.glob(os.path.join(run, "*_original.*.png")))]
h, w = orig[0].shape[:2]
yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
warps = []
for i in range(1, len(orig)):
    mv = read_pfm(os.path.join(work, "f%04d_mv.pfm" % (i + 1)))
    warps.append((xx - 2 * mv[..., 0], yy + 2 * mv[..., 1]))

for tag, pat in (("raw DLSS 5", "*_dlss5.*.png"), ("stabilised 0.6", "stab_a0.6/*_stab.*.png"),
                 ("stabilised 0.8", "stab_a0.8/*_stab.*.png")):
    seq = [load(p) for p in sorted(glob.glob(os.path.join(run, pat)))]
    if len(seq) != len(orig):
        continue
    delta = [s - o for s, o in zip(seq, orig)]
    fl = np.mean([np.mean(np.abs(delta[i] - bilinear(delta[i - 1], *warps[i - 1]))[FACE])
                  for i in range(1, len(delta))])
    mag = np.mean([np.mean(np.abs(d)[FACE]) for d in delta])
    print("%-15s face, motion-compensated: flicker %.4f  size of change %.4f  ratio %.2f" % (tag, fl, mag, fl / mag))
