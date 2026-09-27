"""Headless check of the debug images: frames 1-3 of test_anim.ma with debug=True.

Run:  mayapy test_debug.py
"""
import glob
import os
import shutil
import sys

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
import dlss5_enhance as de

OUT = os.path.join(ROOT, "test", "debug").replace("\\", "/")
shutil.rmtree(OUT, ignore_errors=True)
cmds.file(os.path.join(ROOT, "test", "test_anim.ma"), open=True, force=True)
res = {}
de.process_scene_frames(1, 3, OUT, intensity=0.98, structure=2.0, debug=True, log=print,
                        done=lambda r, e: res.update(r=r, e=e), block=True)
d = os.path.join(OUT, "debug")
checks = [("no error", res.get("e") is None)]
for kind in ("compare", "diff", "inputs"):
    n = len(glob.glob(os.path.join(d, "test_anim_%s.*.png" % kind)))
    checks.append(("3 %s images" % kind, n == 3))
for n, ok in checks:
    print("%s  %s" % ("PASS" if ok else "FAIL", n))
maya.standalone.uninitialize()
