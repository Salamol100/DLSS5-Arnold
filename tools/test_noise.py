"""Does DLSS 5 clean up noisy / aliased Arnold renders better than Arnold's own denoiser?

Renders the test scene four ways (display-referred ACES PNGs), runs DLSS 5 on the rough one,
and measures each against a high-sample reference.

Run:  mayapy test_noise.py
"""
import os
import re
import shutil
import sys

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dlss5_enhance as de

OUT = os.path.join(ROOT, "test", "noise").replace("\\", "/")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
cmds.file(os.path.join(ROOT, "test", "test_scene.ma"), open=True, force=True)


def variant(tag, aa, denoise):
    work = os.path.join(OUT, "work_" + tag).replace("\\", "/")
    os.makedirs(work)
    jobs, near = de.export_scene_frames(work, 1, 1)
    frame, ass, exr, png = jobs[0]
    with open(ass, encoding="utf-8", errors="surrogateescape") as f:
        t = f.read()
    t = re.sub(r"\n(\s*)AA_samples\s+\d+", r"\n\1AA_samples %d" % aa, t, count=1)
    if not denoise:
        t = re.sub(r'\n\s*input "defaultArnoldDenoiser"', "", t)
    with open(ass, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(t)
    de._kick(ass, print)
    return (frame, ass, exr, png), near, work


ref, _, _ = variant("ref", 10, False)
rough, near, rough_work = variant("rough", 1, False)
oidn, _, _ = variant("oidn", 1, True)

# DLSS 5 on the rough frame (already rendered: render_scene=False), strength 0.98 and 0.5.
results = {}
for strength in (0.98, 0.5):
    de.write_settings(strength, 2.0)
    work = de._new_work()
    shutil.copy(rough[2], work)
    shutil.copy(rough[3], work)
    fr = (1, None, os.path.join(work, os.path.basename(rough[2])), os.path.join(work, os.path.basename(rough[3])))
    got = {}
    de._state["busy"] = True
    de._run_pipeline([fr], OUT, "rough_dlss%g" % strength, "png", near, work, False, False, print,
                     lambda r, e: got.update(r=r, e=e), render_scene=False)
    results[strength] = got["r"][0]

from measure_detail import load  # noqa: E402

imgs = {"reference AA10": load(ref[3]), "rough AA1": load(rough[3]), "rough + Arnold OIDN": load(oidn[3])}
for s, p in results.items():
    imgs["rough + DLSS 5 @%g" % s] = load(p)
for k, p in (("ref", ref[3]), ("rough", rough[3]), ("oidn", oidn[3])):
    shutil.copy(p, os.path.join(OUT, k + ".png"))

R = imgs["reference AA10"]
print("\n%-24s %10s %10s" % ("image", "RMSE vs ref", "PSNR dB"))
for k, im in imgs.items():
    rmse = np.sqrt(np.mean((im - R) ** 2))
    print("%-24s %10.4f %10.2f" % (k, rmse, 20 * np.log10(1 / rmse) if rmse else 99))
maya.standalone.uninitialize()
