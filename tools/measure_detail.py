"""Split what DLSS 5 changed into colour/tone vs fine detail.

Run:  mayapy measure_detail.py original.png dlss5.png

- detail energy: RMS of (luma - 5px box blur), i.e. how much fine texture/edges an image has
- colour-matched residual: match each DLSS channel's mean/std to the original, then diff.
  What survives a global colour match is local/structural change (detail, relighting).
"""
import os
import subprocess
import sys

import numpy as np

OIIO = r"C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"


def load(path):
    tmp = path + ".tmp.pfm"
    subprocess.run([OIIO, path, "--ch", "R,G,B", "-d", "float", "-o", tmp], check=True, capture_output=True)
    with open(tmp, "rb") as f:
        tag = f.readline().strip()
        w, h = map(int, f.readline().split())
        scale = float(f.readline())
        data = np.frombuffer(f.read(), dtype="<f4" if scale < 0 else ">f4").reshape(h, w, 3)
    os.remove(tmp)
    return np.flipud(data).astype(np.float64)


def box_blur(x, r=2):
    k = 2 * r + 1
    p = np.pad(x, r, mode="edge")
    c = p.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def luma(img):
    return img @ np.array([0.2126, 0.7152, 0.0722])


def main():
    a, b = load(sys.argv[1]), load(sys.argv[2])
    report(a, b)


def report(a, b):
    la, lb = luma(a), luma(b)
    det_a = np.sqrt(np.mean((la - box_blur(la)) ** 2))
    det_b = np.sqrt(np.mean((lb - box_blur(lb)) ** 2))

    matched = np.empty_like(b)
    for c in range(3):
        matched[..., c] = ((b[..., c] - b[..., c].mean()) / (b[..., c].std() + 1e-9) * a[..., c].std()
                           + a[..., c].mean())
    raw = np.mean(np.abs(a - b))
    resid = np.mean(np.abs(a - matched))

    hp_a, hp_b = la - box_blur(la), lb - box_blur(lb)
    corr = np.corrcoef(hp_a.ravel(), hp_b.ravel())[0, 1]

    sat = lambda x: np.mean(x.max(2) - x.min(2))
    print("size                       %dx%d" % (a.shape[1], a.shape[0]))
    print("mean luma      orig %.4f  dlss %.4f" % (la.mean(), lb.mean()))
    print("contrast (std) orig %.4f  dlss %.4f" % (la.std(), lb.std()))
    print("saturation     orig %.4f  dlss %.4f" % (sat(a), sat(b)))
    print("detail energy  orig %.5f  dlss %.5f  (%+.1f%%)" % (det_a, det_b, 100 * (det_b / det_a - 1)))
    print("fine-detail correlation    %.3f  (1.0 = same texture, lower = texture changed/invented)" % corr)
    print("mean change raw            %.4f" % raw)
    print("mean change after colour match %.4f  (%.0f%% of the change is NOT explained by a global colour grade)"
          % (resid, 100 * resid / raw if raw else 0))


if __name__ == "__main__":
    main()
