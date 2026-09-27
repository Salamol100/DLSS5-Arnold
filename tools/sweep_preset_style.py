"""Sweep RenoDX NRPreset (0-3: Default, Preset #1-#3) x NRStyle (0-1: Natural, Cinematic).

Input: the clean AA10 test frame from test_noise.py (test/noise/work_ref).
Run:  mayapy sweep_preset_style.py
"""
import glob
import os
import shutil
import subprocess
import sys

import maya.standalone
maya.standalone.initialize(name="python")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dlss5_enhance as de
from measure_detail import load, luma, box_blur

import numpy as np

SRC = os.path.join(ROOT, "test", "noise", "work_ref")
OUT = os.path.join(ROOT, "test", "sweep").replace("\\", "/")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
exr = glob.glob(os.path.join(SRC, "arnold.*.exr"))[0]
png = glob.glob(os.path.join(SRC, "display.*.png"))[0]
shutil.copy(png, os.path.join(OUT, "original.png"))

with open(de.INI) as f:
    ini_orig = f.read()


def set_keys(**vals):
    lines = []
    for line in ini_orig.splitlines():
        k = line.split("=", 1)[0]
        lines.append("%s=%s" % (k, vals[k]) if k in vals and "=" in line else line)
    with open(de.INI, "w") as f:
        f.write("\n".join(lines) + "\n")


PRESETS = ["Default", "Preset1", "Preset2", "Preset3"]
STYLES = ["Natural", "Cinematic"]
outs = []
try:
    for p in range(4):
        for s in range(2):
            set_keys(NeuralUplift="1", NRIntensity="0.98", NRLocalStructure="2", NRPreset=str(p), NRStyle=str(s))
            work = de._new_work()
            fr = (1, None, shutil.copy(exr, work), shutil.copy(png, work))
            got = {}
            de._state["busy"] = True
            de._run_pipeline([fr], OUT, "%s_%s" % (PRESETS[p], STYLES[s]), "png", 0.1, work, False, False,
                             lambda m: None, lambda r, e: got.update(r=r, e=e), render_scene=False)
            log = open(os.path.join(de.RUNTIME, "ReShade.log"), errors="replace").read()
            active = [l for l in log.splitlines() if "active settings" in l]
            ok = "evaluation succeeded" in log
            outs.append((PRESETS[p], STYLES[s], got["r"][0] if got.get("r") else None,
                         active[0].split("active settings:")[1].strip() if active else "?", ok))
finally:
    with open(de.INI, "w") as f:
        f.write(ini_orig)

a = load(os.path.join(OUT, "original.png"))
la = luma(a)
det_a = np.sqrt(np.mean((la - box_blur(la)) ** 2))
sat = lambda x: np.mean(x.max(2) - x.min(2))
print("\n%-9s %-10s %8s %8s %8s %8s  %s" % ("preset", "style", "detail%", "contr", "satur", "change", "NR ran / RenoDX says"))
print("%-9s %-10s %8s %8.4f %8.4f %8s" % ("original", "", "", la.std(), sat(a), ""))
for pr, st, path, active, ok in outs:
    if not path:
        print("%-9s %-10s FAILED" % (pr, st))
        continue
    b = load(path)
    lb = luma(b)
    det_b = np.sqrt(np.mean((lb - box_blur(lb)) ** 2))
    print("%-9s %-10s %+7.1f%% %8.4f %8.4f %8.4f  %s | %s" % (pr, st, 100 * (det_b / det_a - 1), lb.std(), sat(b),
                                                           np.mean(np.abs(a - b)), ok, active))

oiio = os.path.join(de.arnold_bin(), "oiiotool.exe")
tiles = [os.path.join(OUT, "original.png")] + [o[2] for o in outs if o[2]]
args = [oiio]
for t in tiles:
    args += [t, "--cut", "420x300+420+160"]
subprocess.run(args + ["--mosaic", "3x3", "-o", os.path.join(OUT, "sheet.png")], check=True)
maya.standalone.uninitialize()
