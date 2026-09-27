# Runs inside the user's live Maya (tools/maya_port.py): current frame with 1, 2 and 3 DLSS 5 passes.
import sys
p = r"C:\Users\admin\Documents\maya\2024\scripts\claudeScripts\dlss5_arnold\maya"
if p not in sys.path:
    sys.path.insert(0, p)
import importlib
import dlss5_enhance as de
importlib.reload(de)
import maya.cmds as cmds

f = int(cmds.currentTime(q=True))
out = []
for n in (1, 2, 3):
    res = {}
    de.process_scene_frames(f, f, r"C:/Users/admin/Documents/maya/2024/scripts/claudeScripts/dlss5_arnold/test/face_passes/p%d" % n,
                            intensity=0.98, structure=2.0, fmt="png16", passes=n, log=print,
                            done=lambda r, e: res.update(r=r, e=e), block=True)
    out.append((n, res.get("e"), (res.get("r") or [None])[0]))
RESULT = out
