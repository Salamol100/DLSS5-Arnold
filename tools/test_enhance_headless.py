"""Headless check of dlss5_enhance: scene frame -> kick -> DLSS 5 -> PNG, and scene settings restored.

Run:  mayapy test_enhance_headless.py
"""
import os
import sys

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
import dlss5_enhance as de

cmds.file(os.path.join(ROOT, "test", "test_scene.ma"), open=True, force=True)
cmds.loadPlugin("mtoa", quiet=True)

# Scene must come back unchanged, so start WITHOUT the Z AOV the builder added.
for n in cmds.ls("aiAOV_Z"):
    cmds.delete(n)
before = (cmds.getAttr("defaultArnoldDriver.aiTranslator"), cmds.getAttr("defaultArnoldDriver.mergeAOVs"))

import tempfile
_tmp = tempfile.mkdtemp()
_jobs, _near = de.export_scene_frames(_tmp, 1, 1)
with open(_jobs[0][1], encoding="utf-8", errors="replace") as _f:
    _ass = _f.read()
denoiser_kept = ('input "defaultArnoldDenoiser"' in _ass and
                 bool(__import__("re").search(r'\n\s*name\s+"?defaultArnoldDenoiser"?\s*\n', _ass)))

out = os.path.join(ROOT, "test", "headless_out").replace("\\", "/")
result = {}
logs = []
de.process_scene_frames(1, 1, out, intensity=0.5, structure=2.0, fmt="png", keep_original=True,
                        log=lambda m: (logs.append(m), print(m)),
                        done=lambda r, e: result.update(r=r, e=e), block=True)

after = (cmds.getAttr("defaultArnoldDriver.aiTranslator"), cmds.getAttr("defaultArnoldDriver.mergeAOVs"))


def banner_region_diff():
    """Mean abs difference in the top-left 640x40 box where ReShade's startup banner draws."""
    import re
    import subprocess
    oiio = os.path.join(de.arnold_bin(), "oiiotool.exe")
    a = os.path.join(out, "test_scene_original.0001.png")
    b = os.path.join(out, "test_scene_dlss5.0001.png")
    txt = subprocess.run([oiio, a, "--cut", "640x40+0+0", b, "--cut", "640x40+0+0", "--diff"],
                         capture_output=True, text=True).stdout
    m = re.search(r"Mean error = ([0-9.e-]+)", txt)
    return float(m.group(1)) if m else 1.0
checks = [
    ("denoiser imager exported and still linked to the driver", denoiser_kept),
    ("scene view transform (ACES) used for colour", any("scene view transform" in m for m in logs)),
    ("pipeline finished without error", result.get("e") is None),
    ("one output written", len(result.get("r") or []) == 1 and os.path.isfile(result["r"][0])),
    ("original PNG written", os.path.isfile(os.path.join(out, "test_scene_original.0001.png"))),
    ("Z AOV removed again", not cmds.ls("aiAOV_Z")),
    ("driver settings restored", before == after),
    ("no ReShade banner in output (top-left diff < 0.05)", banner_region_diff() < 0.05),
]
for name, ok in checks:
    print("%s  %s" % ("PASS" if ok else "FAIL", name))
print("RESULT", result)
maya.standalone.uninitialize()
