"""Approximate the ACES 1.0 SDR-video (sRGB) view with Stephen Hill's fitted RRT+ODT, validate it
against Arnold's own output-transform PNG, then view the HDR DLSS 5 result with it.

Usage:  mayapy aces_view.py <hdr_dir> <arnold_display_png>
"""
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_detail import load

hdr, ref_png = sys.argv[1], sys.argv[2]
OIIO = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
AP1_TO_709 = np.array([[1.70505, -0.62179, -0.08326], [-0.13026, 1.14080, -0.01055], [-0.02400, -0.12897, 1.15297]])
# Hill's fit (BakingLab ACES.hlsl): sRGB-linear -> RRT_SAT -> fitted RRT/ODT -> ODT_SAT -> sRGB-linear
IN_MAT = np.array([[0.59719, 0.35458, 0.04823], [0.07600, 0.90834, 0.01566], [0.02840, 0.13383, 0.83777]])
OUT_MAT = np.array([[1.60475, -0.53108, -0.07367], [-0.10208, 1.10813, -0.00605], [-0.00327, -0.07276, 1.07602]])


def read_pfm(path):
    with open(path, "rb") as f:
        tag = f.readline().strip()
        w, h = map(int, f.readline().split())
        scale = float(f.readline())
        c = 3 if tag == b"PF" else 1
        d = np.frombuffer(f.read(), dtype="<f4" if scale < 0 else ">f4").reshape(h, w, c)
    return np.flipud(d).astype(np.float64)


def view(acescg):
    v = acescg @ AP1_TO_709.T @ IN_MAT.T
    v = (v * (v + 0.0245786) - 0.000090537) / (v * (0.983729 * v + 0.4329510) + 0.238081)
    v = np.clip(v @ OUT_MAT.T, 0, 1)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def save(img, path):
    tmp = path + ".pfm"
    h, w = img.shape[:2]
    with open(tmp, "wb") as f:
        f.write(b"PF\n%d %d\n-1.0\n" % (w, h))
        f.write(np.flipud(img).astype("<f4").tobytes())
    subprocess.run([OIIO, tmp, "-d", "uint8", "-o", path], check=True)
    os.remove(tmp)


orig = read_pfm(os.path.join(hdr, "acescg.pfm"))
dl = read_pfm(os.path.join(hdr, "hdr_dlss5_acescg.pfm"))
vo = view(orig)
ref = load(ref_png)
print("validation: fitted view of original vs Arnold ACES PNG: mean abs error %.4f" % np.mean(np.abs(vo - ref)))
save(vo, os.path.join(hdr, "original_fitview.png"))
save(view(dl), os.path.join(hdr, "hdr_dlss5_fitview.png"))
