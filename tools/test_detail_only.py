"""'Detail only': keep Arnold's broad look (lighting, colour, SSS) and take only DLSS 5's fine detail.

out = orig * (dlss / blur_s(dlss)) / (orig / blur_s(orig))   (per channel, scene-linear scRGB)
i.e. Arnold's low frequencies + DLSS's high-frequency detail ratio, for split sigma s.

Usage:  mayapy test_detail_only.py <c.pfm (Arnold scRGB)> <dlss.pfm (DLSS scRGB)> <out_dir>
"""
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dlss5_enhance as de
from measure_detail import box_blur, luma

OIIO = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
c_path, d_path, out = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(out, exist_ok=True)
O = de._read_pfm(c_path).astype(np.float64)
D = de._restore_gamut(O, de._read_pfm(d_path).astype(np.float64))
FACE = (slice(70, 300), slice(380, 580))


def gblur(img, s):
    r = int(3 * s + 0.5)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / s) ** 2)
    k /= k.sum()
    pad = np.pad(img, ((r, r), (r, r), (0, 0)), mode="edge")
    t = np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 0, pad)
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 1, t)


def detail_only(s):
    eps = 1e-4
    return O * ((np.abs(D) + eps) / (np.abs(gblur(D, s)) + eps)) / ((np.abs(O) + eps) / (np.abs(gblur(O, s)) + eps))


view = lambda x: de._view(x / de.SCRGB_WHITE, True)


def detail(v):
    l = luma(v[FACE])
    return np.sqrt(np.mean((l - box_blur(l)) ** 2))


vo = view(O)
rows = [("original", vo), ("full DLSS", view(D))]
for s in (1.5, 3.0, 6.0):
    rows.append(("detail s%g" % s, view(detail_only(s))))
print("%-12s %12s %16s" % ("", "face detail", "colour vs orig"))
tiles = []
for name, v in rows:
    col = np.mean(np.abs(gblur(v[FACE], 6) - gblur(vo[FACE], 6)))  # broad look difference
    print("%-12s %12.5f %16.4f" % (name, detail(v), col))
    p = os.path.join(out, name.replace(" ", "_") + ".pfm")
    de._write_pfm(v[FACE], p)
    subprocess.run([OIIO, p, "-d", "uint8", "-o", p[:-4] + ".png"], check=True)
    tiles.append(p[:-4] + ".png")
subprocess.run([OIIO] + tiles + ["--mosaic", "5x1", "--resize", "1750x400", "-o", os.path.join(out, "sheet.png")], check=True)
