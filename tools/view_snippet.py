# Runs inside the user's live Maya (tools/maya_port.py): apply the scene's colour-managed view
# (ACES 1.0 SDR-video) to ACEScg EXRs via the Render View, writing view-transformed PNGs.
import maya.cmds as cmds

D = r"C:/Users/admin/Documents/maya/2024/scripts/claudeScripts/dlss5_arnold/test/head_hdr/hdr/"
done = []
for name in ("original_acescg", "hdr_dlss5_acescg"):
    cmds.renderWindowEditor("renderView", e=True, loadImage=D + name + ".exr")
    cmds.renderWindowEditor("renderView", e=True, colorManage=True, writeImage=D + name + "_view.png")
    done.append(name)
RESULT = (done, cmds.colorManagementPrefs(q=True, viewTransformName=True))
