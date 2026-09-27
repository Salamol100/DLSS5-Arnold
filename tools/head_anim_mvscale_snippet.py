# Runs inside the user's live Maya (tools/maya_port.py). Same temporary orbit as head_anim_snippet.py,
# rendered with RenoDX's NRMVecScaleX/Y at 1.0 instead of the game's 2.0. BLOCKING (the ini must
# stay set until the DLSS host has read it); work files are kept for the stabilisation test.
import re
import sys
p = r"C:\Users\admin\Documents\maya\2024\scripts\claudeScripts\dlss5_arnold\maya"
if p not in sys.path:
    sys.path.insert(0, p)
import importlib
import dlss5_enhance as de
importlib.reload(de)
import maya.cmds as cmds

meshes = [cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in cmds.ls(type="mesh", noIntermediate=True)]
bb = cmds.exactWorldBoundingBox(*meshes)
center = [(bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2]
renderable = {c: cmds.getAttr(c + ".renderable") for c in cmds.ls(type="camera")}
ini_orig = open(de.INI).read()
pivot = cmds.spaceLocator(name="dlss5_orbitPivot")[0]
cmds.xform(pivot, worldSpace=True, translation=center)
cam = cmds.duplicate("persp", name="dlss5_testCam")[0]
cmds.parent(cam, pivot)
cam_shape = cmds.listRelatives(cam, shapes=True, fullPath=True)[0]
cmds.setKeyframe(pivot, attribute="rotateY", time=1, value=-8)
cmds.setKeyframe(pivot, attribute="rotateY", time=10, value=8)
cmds.keyTangent(pivot, inTangentType="linear", outTangentType="linear")
res = {}
try:
    for c in renderable:
        cmds.setAttr(c + ".renderable", False)
    cmds.setAttr(cam_shape + ".renderable", True)
    de.write_settings(0.98, 2.0)
    txt = open(de.INI).read()
    txt = re.sub(r"(?m)^NRMVecScaleX=.*$", "NRMVecScaleX=1", txt)
    txt = re.sub(r"(?m)^NRMVecScaleY=.*$", "NRMVecScaleY=1", txt)
    open(de.INI, "w").write(txt)
    de.KEEP_WORK = True
    de.process_scene_frames(1, 10, r"C:/Users/admin/Documents/maya/2024/scripts/claudeScripts/dlss5_arnold/test/head_anim_mv1",
                            intensity=0.98, structure=2.0, motion_vectors=True, log=print,
                            done=lambda r, e: res.update(r=r, e=e), block=True)
finally:
    de.KEEP_WORK = False
    open(de.INI, "w").write(ini_orig)
    for c, v in renderable.items():
        if cmds.objExists(c):
            cmds.setAttr(c + ".renderable", v)
    cmds.delete(pivot)
RESULT = (res.get("e"), len(res.get("r") or []))
