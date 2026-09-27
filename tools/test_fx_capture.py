"""Does the reshade_finish_effects capture hold the DLSS 5 result, without the ReShade banner, even
with a short warm-up? Renders the test scene once (HDR inputs kept), then runs the host:
  A: --fx-capture 1 --warmup-sec 1     (new: capture before the overlay)
  B: --fx-capture 0 --warmup-sec 20    (old: post-Present after the banner has gone)
A must match B (the DLSS result) and must differ from the input (DLSS applied); A's banner box must
match B's (no banner baked in).

Run:  mayapy test_fx_capture.py
"""
import glob
import os
import subprocess
import sys

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
import dlss5_enhance as de

cmds.file(os.path.join(ROOT, "test", "test_scene.ma"), open=True, force=True)
de.KEEP_WORK = True
res = {}
de.process_scene_frames(1, 1, os.path.join(ROOT, "test", "fxcap").replace("\\", "/"), intensity=0.98,
                        structure=2.0, log=lambda m: None, done=lambda r, e: res.update(r=r, e=e), block=True)
de.KEEP_WORK = False
work = sorted(glob.glob(os.path.join(de.WORK_ROOT, "*")), key=os.path.getmtime)[-1]
c, z = os.path.join(work, "f0001_c.pfm"), os.path.join(work, "f0001_z.pfm")


def host(out, extra):
    r = subprocess.run([de.HOST, "--color", c, "--depth", z, "--out", out, "--hdr", "1"] + extra,
                       cwd=de.RUNTIME, capture_output=True, text=True)
    print("  host exit %d: %s" % (r.returncode, " | ".join(l for l in r.stdout.splitlines() if "wrote" in l)))
    return de._read_pfm(out)


A = host(os.path.join(work, "A.pfm"), ["--fx-capture", "1", "--warmup-sec", "1"])
B = host(os.path.join(work, "B.pfm"), ["--fx-capture", "0", "--warmup-sec", "20"])
I = de._read_pfm(c)
body = (slice(60, None), slice(None))
box = (slice(0, 45), slice(0, 650))
rel = lambda x, y, s: np.mean(np.abs(x[s] - y[s])) / np.mean(np.abs(y[s]))
checks = [
    ("A (new capture) applied DLSS: differs from input by %.1f%%" % (100 * rel(A, I, body)), rel(A, I, body) > 0.01),
    ("A matches B outside banner box (%.2f%%)" % (100 * rel(A, B, body)), rel(A, B, body) < 0.01),
    ("A has no banner: banner box matches B (%.2f%%)" % (100 * rel(A, B, box)), rel(A, B, box) < 0.02),
]
for n, ok in checks:
    print("%s  %s" % ("PASS" if ok else "FAIL", n))
maya.standalone.uninitialize()
