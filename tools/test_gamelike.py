"""Does DLSS 5 'fix' a game-like Arnold render the way it fixes Fallout 4?

Game-like = no indirect bounces (flat, direct light only) + green-tinted lights.
Measures colour cast, contrast and detail before/after DLSS 5 (Style 0, intensity 0.98).

Run:  mayapy test_gamelike.py
"""
import os
import re
import shutil
import subprocess
import sys

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dlss5_enhance as de
from measure_detail import box_blur, load, luma

OUT = os.path.join(ROOT, "test", "gamelike").replace("\\", "/")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
cmds.file(os.path.join(ROOT, "test", "test_scene.ma"), open=True, force=True)

GREEN = "0.62 1 0.55"
results = {}
for tag, gamelike in (("clean", False), ("gamelike", True)):
    work = os.path.join(OUT, "work_" + tag).replace("\\", "/")
    os.makedirs(work)
    jobs, near = de.export_scene_frames(work, 1, 1)
    frame, ass, exr, png = jobs[0]
    if gamelike:
        t = open(ass, encoding="utf-8", errors="surrogateescape").read()
        t = re.sub(r"\n(\s*)GI_diffuse_depth\s+\d+", "", t)
        t = re.sub(r"\n(\s*)GI_specular_depth\s+\d+", "", t)
        t = re.sub(r"(\n\s*AA_samples\s+\d+)", r"\1\n GI_diffuse_depth 0\n GI_specular_depth 0", t, count=1)
        # green cast on every light's colour
        t = re.sub(r"(\n(?:distant_light|skydome_light)\s*\{.*?\n\s*color\s+)[-\d.e]+ [-\d.e]+ [-\d.e]+",
                   lambda m: m.group(1) + GREEN, t, flags=re.S)
        open(ass, "w", encoding="utf-8", errors="surrogateescape").write(t)
    de._kick(ass, print)
    de.write_settings(0.98, 2.0)
    ini = re.sub(r"(?m)^NRStyle=.*$", "NRStyle=0", open(de.INI).read())
    open(de.INI, "w").write(ini)
    run = de._new_work()
    fr = (1, None, shutil.copy(exr, run), shutil.copy(png, run))
    got = {}
    de._state["busy"] = True
    de._run_pipeline([fr], OUT, tag, "png", near, run, False, True, print,
                     lambda r, e: got.update(r=r, e=e), render_scene=False)
    results[tag] = (os.path.join(OUT, "%s_original.0001.png" % tag), got["r"][0])


def stats(img):
    im = img[150:]
    l = luma(im)
    cast = np.mean(im[..., 1] - (im[..., 0] + im[..., 2]) / 2)  # + = green
    return cast, l.std(), np.sqrt(np.mean((l - box_blur(l)) ** 2)), l.mean()


print("\n%-9s %-6s %8s %8s %9s %8s" % ("scene", "", "green", "contrast", "detail", "bright"))
for tag, (o, d) in results.items():
    for lab, p in (("orig", o), ("dlss5", d)):
        c, s, e, m = stats(load(p))
        print("%-9s %-6s %+8.4f %8.4f %9.5f %8.4f" % (tag, lab, c, s, e, m))

oiio = os.path.join(de.arnold_bin(), "oiiotool.exe")
subprocess.run([oiio, results["gamelike"][0], results["gamelike"][1], "--mosaic", "2x1", "--resize", "1600x450",
                "-o", os.path.join(OUT, "gamelike_compare.png")], check=True)
maya.standalone.uninitialize()
