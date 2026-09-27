"""HDR (16-bit float scRGB) DLSS 5 test on a kept work folder.

ACEScg linear (Arnold EXR) -> linear Rec.709 * (272/80) = scRGB with diffuse white at RenoDX's
272-nit bridge -> dlss5_host --hdr 1 -> back to ACEScg -> EXR.

Usage:  mayapy hdr_test.py <work_dir> <out_dir>
"""
import glob
import os
import subprocess
import sys

import numpy as np

work, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT = os.path.join(ROOT, "runtime")
OIIO = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
SCALE = 272.0 / 80.0  # scRGB 1.0 = 80 nits; RenoDX Diffuse White = 272 nits

# ACEScg (AP1, D60) -> linear Rec.709 (D65), Bradford; and its inverse.
AP1_TO_709 = np.array([[1.70505, -0.62179, -0.08326],
                       [-0.13026, 1.14080, -0.01055],
                       [-0.02400, -0.12897, 1.15297]])
REC709_TO_AP1 = np.linalg.inv(AP1_TO_709)


def read_pfm(path):
    with open(path, "rb") as f:
        tag = f.readline().strip()
        w, h = map(int, f.readline().split())
        scale = float(f.readline())
        c = 3 if tag == b"PF" else 1
        d = np.frombuffer(f.read(), dtype="<f4" if scale < 0 else ">f4").reshape(h, w, c)
    return np.flipud(d).astype(np.float64)


def write_pfm(img, path):
    h, w = img.shape[:2]
    with open(path, "wb") as f:
        f.write(b"PF\n%d %d\n-1.0\n" % (w, h))
        f.write(np.flipud(img).astype("<f4").tobytes())


exr = glob.glob(os.path.join(work, "arnold.*.exr"))[0]
depth = glob.glob(os.path.join(work, "*_z.pfm"))[0]
lin = os.path.join(out, "acescg.pfm")
subprocess.run([OIIO, exr, "--ch", "R,G,B", "-o", lin], check=True)
acescg = read_pfm(lin)
scrgb = np.maximum(acescg @ AP1_TO_709.T, 0.0) * SCALE
write_pfm(scrgb, os.path.join(out, "in_scrgb.pfm"))
print("input scRGB: mean %.3f  99th pct %.3f  max %.3f  (x80 = nits)" % (
    scrgb.mean(), np.percentile(scrgb, 99), scrgb.max()))

r = subprocess.run([os.path.join(RT, "dlss5_host.exe"), "--color", os.path.join(out, "in_scrgb.pfm"),
                    "--depth", depth, "--out", os.path.join(out, "out_scrgb.pfm"), "--hdr", "1"],
                   cwd=RT, capture_output=True, text=True)
print("host exit", r.returncode, "|", " ".join(l for l in r.stdout.splitlines() if "wrote" in l))
res = read_pfm(os.path.join(out, "out_scrgb.pfm"))
print("output scRGB: mean %.3f  99th pct %.3f  max %.3f" % (res.mean(), np.percentile(res, 99), res.max()))
back = (res / SCALE) @ REC709_TO_AP1.T
write_pfm(back, os.path.join(out, "hdr_dlss5_acescg.pfm"))
subprocess.run([OIIO, os.path.join(out, "hdr_dlss5_acescg.pfm"), "-d", "half", "-o",
                os.path.join(out, "hdr_dlss5_acescg.exr")], check=True)
subprocess.run([OIIO, lin, "-d", "half", "-o", os.path.join(out, "original_acescg.exr")], check=True)
print("mean |change| in ACEScg: %.4f  (relative %.1f%%)" % (
    np.mean(np.abs(back - acescg)), 100 * np.mean(np.abs(back - acescg)) / np.mean(np.abs(acescg))))
