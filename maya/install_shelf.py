"""Create the DLSS5 shelf (Panel / Render / Seq / Out) in Maya 2024.

Paste into the Script Editor (Python tab) once, or call create_dlss5_shelf() from userSetup.py
(e.g. cmds.evalDeferred(create_dlss5_shelf, lowestPriority=True)).
Edit TOOL_DIR if this repository lives somewhere else.
"""
import os

import maya.cmds as cmds
import maya.mel as mel

TOOL_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else \
    r"C:\path\to\dlss5_arnold\maya"


def create_dlss5_shelf():
    shelf = "DLSS5"
    if cmds.shelfLayout(shelf, exists=True):
        for child in cmds.shelfLayout(shelf, query=True, childArray=True) or []:
            cmds.deleteUI(child)
    else:
        mel.eval('addNewShelfTab "%s"' % shelf)
    imp = ("import sys; sys.path.insert(0, r'{0}') if r'{0}' not in sys.path else None; "
           "import importlib, dlss5_enhance; importlib.reload(dlss5_enhance); ").format(TOOL_DIR)
    for label, tip, call in (
            ("Panel", "DLSS 5 panel - strength, style, output, frame range, EXR sequence", "dlss5_enhance.show()"),
            ("Render", "DLSS 5 Render - current frame: Arnold -> DLSS 5 -> Render View", "dlss5_enhance.quick_frame()"),
            ("Seq", "DLSS 5 Sequence - DLSS 5 an existing EXR sequence (needs a Z AOV)", "dlss5_enhance.quick_sequence()"),
            ("Out", "Open the DLSS 5 output folder (project images/dlss5)", "dlss5_enhance.open_output()")):
        cmds.shelfButton(label=label, annotation=tip, command=imp + call, sourceType="python",
                         imageOverlayLabel=label, image="commandButton.png", parent=shelf)


create_dlss5_shelf()
