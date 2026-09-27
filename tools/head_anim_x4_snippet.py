# Runs inside the user's live Maya (tools/maya_port.py). Same ±8° temporary orbit as
# head_anim_snippet.py but with 4x the frames (37 frames, 0.45° per frame), so DLSS 5 sees
# game-like small per-frame motion. Frames 1,5,...,37 match the 10-frame run's camera positions.
# Non-blocking: the temporary camera is removed right after the synchronous export.
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
pivot = cmds.spaceLocator(name="dlss5_orbitPivot")[0]
cmds.xform(pivot, worldSpace=True, translation=center)
cam = cmds.duplicate("persp", name="dlss5_testCam")[0]
cmds.parent(cam, pivot)
cam_shape = cmds.listRelatives(cam, shapes=True, fullPath=True)[0]
cmds.setKeyframe(pivot, attribute="rotateY", time=1, value=-8)
cmds.setKeyframe(pivot, attribute="rotateY", time=37, value=8)
cmds.keyTangent(pivot, inTangentType="linear", outTangentType="linear")
try:
    for c in renderable:
        cmds.setAttr(c + ".renderable", False)
    cmds.setAttr(cam_shape + ".renderable", True)
    de.write_settings(0.98, 2.0)
    de.process_scene_frames(1, 37, r"C:/Users/admin/Documents/maya/2024/scripts/claudeScripts/dlss5_arnold/test/head_anim_x4",
                            intensity=0.98, structure=2.0, motion_vectors=True,
                            log=de._shelf_log, done=de._shelf_done)
finally:
    for c, v in renderable.items():
        if cmds.objExists(c):
            cmds.setAttr(c + ".renderable", v)
    cmds.delete(pivot)
RESULT = "export done, rendering 37 frames in background"
