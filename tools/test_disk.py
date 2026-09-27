"""Headless check of the disk-bounded pipeline.

- a planted stale work folder is cleaned up at the start of a run
- frames 1-5 of test_anim.ma in forced chunks of 2 -> 3 DLSS sessions, 5 EXRs + previews
- scene files exported compressed (.ass.gz)
- peak temp size stays bounded (sampled while running), work folder removed at the end
- chunked output matches an unchunked run closely (stabilisation carried across chunks)

Run:  mayapy test_disk.py
"""
import glob
import os
import shutil
import sys
import threading
import time

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "maya"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dlss5_enhance as de
from measure_detail import load

OUT = os.path.join(ROOT, "test", "disk").replace("\\", "/")
shutil.rmtree(OUT, ignore_errors=True)
stale = os.path.join(de.WORK_ROOT, "19990101_000000")
os.makedirs(stale, exist_ok=True)
open(os.path.join(stale, "junk.bin"), "wb").write(b"\0" * (5 * 2 ** 20))
# a folder "owned" by another live process (this test's parent shell stands in for Maya)
live = os.path.join(de.WORK_ROOT, "19990101_000001")
os.makedirs(live, exist_ok=True)
open(os.path.join(live, "owner.pid"), "w").write(str(os.getppid()))

peak = {"bytes": 0, "gz": 0}
stop = threading.Event()


def sampler():
    while not stop.is_set():
        tot = 0
        for dp, _, fs in os.walk(de.WORK_ROOT):
            for f in fs:
                try:
                    tot += os.path.getsize(os.path.join(dp, f))
                    peak["gz"] += f.endswith(".ass.gz")
                except OSError:
                    pass
        peak["bytes"] = max(peak["bytes"], tot)
        time.sleep(0.5)


def run(sub, chunk):
    cmds.file(os.path.join(ROOT, "test", "test_anim.ma"), open=True, force=True)
    de.FORCE_CHUNK = chunk
    logs, res = [], {}
    de.process_scene_frames(1, 5, OUT + "/" + sub, intensity=0.98, structure=2.0,
                            log=lambda m: (logs.append(m), print(m)),
                            done=lambda r, e: res.update(r=r, e=e), block=True)
    de.FORCE_CHUNK = None
    return logs, res


t = threading.Thread(target=sampler, daemon=True)
t.start()
logs, res = run("chunked", 2)
stop.set()
t.join()
logs2, res2 = run("whole", None)

exrs = sorted(glob.glob(os.path.join(OUT, "chunked", "*_dlss5.*.exr")))
checks = [
    ("no error", res.get("e") is None),
    ("stale work folder cleaned", not os.path.exists(stale) and any("Cleaned up" in m for m in logs)),
    ("3 DLSS sessions (chunks of 2)", sum("DLSS 5: processing" in m for m in logs) == 3),
    ("5 EXRs + 5 previews", len(exrs) == 5 and len(glob.glob(os.path.join(OUT, "chunked", "*_preview.*.png"))) == 5),
    ("scene files exported as .ass.gz", peak["gz"] > 0),
    ("stabilised across chunks (5 frames)", any("Stabilised 5 frame" in m for m in logs)),
    ("work folder removed at end", not any(os.path.basename(p).startswith(time.strftime("%Y%m%d"))
                                           and not de._folder_in_use(p)
                                           for p in glob.glob(os.path.join(de.WORK_ROOT, "*")))),
    ("live folder of another process protected", de._folder_in_use(live) and os.path.isdir(live)),
]
a = [load(p) for p in sorted(glob.glob(os.path.join(OUT, "chunked", "*_preview.*.png")))]
b = [load(p) for p in sorted(glob.glob(os.path.join(OUT, "whole", "*_preview.*.png")))]
diff = max(np.mean(np.abs(x - y)) for x, y in zip(a, b)) if a and len(a) == len(b) else 1.0
checks.append(("chunked matches unchunked (max mean diff %.4f < 0.01)" % diff, diff < 0.01))
print("peak temp during chunked run: %.1f MB" % (peak["bytes"] / 2 ** 20))
shutil.rmtree(live, ignore_errors=True)
for n, ok in checks:
    print("%s  %s" % ("PASS" if ok else "FAIL", n))
maya.standalone.uninitialize()
