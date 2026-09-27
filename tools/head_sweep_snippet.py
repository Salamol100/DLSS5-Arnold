# Runs inside the user's live Maya (via tools/maya_port.py): DLSS 5 strength sweep on the current frame.
import sys
p = r"C:\Users\admin\Documents\maya\2024\scripts\claudeScripts\dlss5_arnold\maya"
if p not in sys.path:
    sys.path.insert(0, p)
import importlib
import dlss5_enhance as de
importlib.reload(de)
import maya.cmds as cmds

f = int(cmds.currentTime(q=True))
done = []
for s in (0.25, 0.5, 0.75):
    out = r"C:/Users/admin/Documents/maya/2024/scripts/claudeScripts/dlss5_arnold/test/head_sweep/i%g" % s
    res = {}
    de.process_scene_frames(f, f, out, intensity=s, structure=2.0, log=print,
                            done=lambda r, e: res.update(r=r, e=e), block=True)
    done.append((s, res.get("e"), (res.get("r") or [None])[0]))
de.write_settings(0.98, 2.0)  # leave the game's strength in place for the shelf button
RESULT = done
