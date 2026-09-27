"""Headless check of the HDR pipeline outputs.

- single frame of test_scene.ma, fmt auto -> 16-bit PNG
- frames 1-3 of test_anim.ma (moving), fmt auto -> half-float EXR + 8-bit preview, MVs + stabilisation

Run:  mayapy test_hdr_outputs.py
"""
import glob
import os
import re
import shutil
import subprocess
import sys

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
import dlss5_enhance as de

OIIO = os.path.join(de.arnold_bin(), "oiiotool.exe")
OUT = os.path.join(ROOT, "test", "hdr_outputs").replace("\\", "/")
shutil.rmtree(OUT, ignore_errors=True)
logs, checks = [], []


def info(path):
    return subprocess.run([OIIO, "--info", "-v", path], capture_output=True, text=True).stdout


def run(scene, a, b, sub):
    cmds.file(os.path.join(ROOT, "test", scene), open=True, force=True)
    res = {}
    de.process_scene_frames(a, b, OUT + "/" + sub, intensity=0.98, structure=2.0,
                            log=lambda m: (logs.append(m), print(m)),
                            done=lambda r, e: res.update(r=r, e=e), block=True)
    return res


single = run("test_scene.ma", 1, 1, "single")
checks.append(("single: no error", single.get("e") is None))
png = os.path.join(OUT, "single", "test_scene_dlss5.0001.png")
checks.append(("single: 16-bit PNG written", os.path.isfile(png) and "uint16" in info(png)))
checks.append(("single: HDR path used", any("16-bit HDR" in m for m in logs)))

rng = run("test_anim.ma", 1, 3, "range")
checks.append(("range: no error", rng.get("e") is None))
exrs = sorted(glob.glob(os.path.join(OUT, "range", "test_anim_dlss5.*.exr")))
prev = sorted(glob.glob(os.path.join(OUT, "range", "test_anim_dlss5_preview.*.png")))
checks.append(("range: 3 half-float EXRs", len(exrs) == 3 and all("half" in info(e) for e in exrs)))
checks.append(("range: 3 8-bit previews", len(prev) == 3 and all("uint8" in info(p) for p in prev)))
checks.append(("range: stabilised", any("Stabilised 3 frame" in m for m in logs)))
checks.append(("range: motion vectors uploaded",
               any(re.search(r"uploads into texMotionVectors: [1-9]", m) for m in logs)))
if exrs:
    stats = subprocess.run([OIIO, "--stats", exrs[0]], capture_output=True, text=True).stdout
    mx = [float(v) for v in re.findall(r"Max:\s+([-\d.e]+)", stats)[:1]]
    print("EXR stats line:", [l for l in stats.splitlines() if "Max" in l][:1])

for n, ok in checks:
    print("%s  %s" % ("PASS" if ok else "FAIL", n))
maya.standalone.uninitialize()
