"""Build the Phase 6 Arnold test scene headless and export it to .ass for kick.

Run:  mayapy make_test_scene.py <out_dir>
Produces <out_dir>/test_scene.ma and <out_dir>/test_scene.ass (renders test_render.exr,
1280x720, beauty RGBA + Z merged into one EXR).
"""
import os
import sys

import maya.standalone
maya.standalone.initialize(name="python")

import maya.cmds as cmds

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
os.makedirs(OUT, exist_ok=True)

cmds.loadPlugin("mtoa", quiet=True)
import mtoa.aovs as aovs
import mtoa.core as core
core.createOptions()


def std_surface(name, **attrs):
    sh = cmds.shadingNode("aiStandardSurface", asShader=True, name=name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
    cmds.connectAttr(sh + ".outColor", sg + ".surfaceShader")
    for k, v in attrs.items():
        if isinstance(v, (tuple, list)):
            cmds.setAttr("%s.%s" % (sh, k), *v, type="double3")
        else:
            cmds.setAttr("%s.%s" % (sh, k), v)
    return sh, sg


def assign(sg, *objs):
    cmds.sets(*objs, forceElement=sg)


# --- ground: high-frequency checker texture -------------------------------------------------
ground = cmds.polyPlane(name="ground", w=30, h=30, sx=1, sy=1)[0]
g_sh, g_sg = std_surface("groundMtl", specularRoughness=0.6)
checker = cmds.shadingNode("checker", asTexture=True, name="fineChecker")
place = cmds.shadingNode("place2dTexture", asUtility=True)
cmds.connectAttr(place + ".outUV", checker + ".uv")
cmds.connectAttr(place + ".outUvFilterSize", checker + ".uvFilterSize")
cmds.setAttr(place + ".repeatU", 60)
cmds.setAttr(place + ".repeatV", 60)
cmds.setAttr(checker + ".color1", 0.55, 0.52, 0.48, type="double3")
cmds.setAttr(checker + ".color2", 0.18, 0.17, 0.16, type="double3")
cmds.connectAttr(checker + ".outColor", g_sh + ".baseColor")
assign(g_sg, ground)

# --- organic sphere: SSS skin + fine noise bump ---------------------------------------------
skin = cmds.polySphere(name="skinSphere", r=1.6, sx=128, sy=96)[0]
cmds.move(-2.2, 1.6, 0, skin)
s_sh, s_sg = std_surface("skinMtl", base=1.0, baseColor=(0.8, 0.55, 0.45),
                         subsurface=0.6, subsurfaceColor=(0.9, 0.5, 0.4),
                         subsurfaceRadius=(1.0, 0.35, 0.2), subsurfaceScale=0.15,
                         specular=0.6, specularRoughness=0.35)
noise = cmds.shadingNode("aiNoise", asTexture=True, name="poreNoise")
cmds.setAttr(noise + ".scale", 40, 40, 40, type="double3")
cmds.setAttr(noise + ".octaves", 4)
bump = cmds.shadingNode("bump2d", asUtility=True, name="poreBump")
cmds.connectAttr(noise + ".outColorR", bump + ".bumpValue")
cmds.setAttr(bump + ".bumpDepth", 0.02)
cmds.connectAttr(bump + ".outNormal", s_sh + ".normalCamera")
assign(s_sg, skin)

# --- glossy metal torus: tight specular highlights ------------------------------------------
torus = cmds.polyTorus(name="metalTorus", r=1.1, sr=0.35, sx=96, sy=48)[0]
cmds.move(2.4, 1.2, 0.5, torus)
cmds.rotate(70, 20, 0, torus)
m_sh, m_sg = std_surface("metalMtl", base=1.0, baseColor=(0.95, 0.8, 0.55),
                         metalness=1.0, specularRoughness=0.12)
assign(m_sg, torus)

# --- thin geometry: row of wires ------------------------------------------------------------
wires = []
for i in range(24):
    w = cmds.polyCylinder(name="wire%02d" % i, r=0.012, h=3.0, sx=8)[0]
    cmds.move(-5.0 + i * 0.43, 1.5, -2.5, w)
    cmds.rotate(0, 0, (i % 5 - 2) * 3.0, w)
    wires.append(w)
w_sh, w_sg = std_surface("wireMtl", baseColor=(0.1, 0.1, 0.1), specularRoughness=0.3)
assign(w_sg, *wires)

# --- lights ---------------------------------------------------------------------------------
import mtoa.utils as mutils
sun = cmds.directionalLight(name="sun", intensity=2.5)
sun_t = cmds.listRelatives(sun, parent=True)[0]
cmds.rotate(-40, 35, 0, sun_t)
cmds.setAttr(sun + ".aiAngle", 1.5)
sky_shape, sky_t = mutils.createLocator("aiSkyDomeLight", asLight=True)
cmds.setAttr(sky_shape + ".intensity", 0.6)
cmds.setAttr(sky_shape + ".color", 0.55, 0.65, 0.8, type="double3")

# --- camera ---------------------------------------------------------------------------------
cam, cam_shape = cmds.camera(name="renderCam", focalLength=40, nearClipPlane=0.1)
cmds.move(0, 3.2, 11.5, cam)
cmds.rotate(-11, 0, 0, cam)
for c in cmds.ls(type="camera"):
    cmds.setAttr(c + ".renderable", c == cam_shape)

# --- render settings ------------------------------------------------------------------------
cmds.setAttr("defaultRenderGlobals.currentRenderer", "arnold", type="string")
cmds.setAttr("defaultResolution.width", 1280)
cmds.setAttr("defaultResolution.height", 720)
cmds.setAttr("defaultResolution.deviceAspectRatio", 1280.0 / 720.0)
cmds.setAttr("defaultRenderGlobals.imageFilePrefix", os.path.join(OUT, "test_render").replace("\\", "/"), type="string")
cmds.setAttr("defaultArnoldRenderOptions.AASamples", 5)
cmds.setAttr("defaultArnoldRenderOptions.GIDiffuseSamples", 3)
cmds.setAttr("defaultArnoldDriver.aiTranslator", "exr", type="string")
cmds.setAttr("defaultArnoldDriver.mergeAOVs", 1)
cmds.setAttr("defaultArnoldDriver.halfPrecision", 0)
aovs.AOVInterface().addAOV("Z", aovType="float")

cmds.file(rename=os.path.join(OUT, "test_scene.ma"))
cmds.file(save=True, type="mayaAscii")
ass = os.path.join(OUT, "test_scene.ass").replace("\\", "/")
cmds.arnoldExportAss(f=ass, cam=cam_shape, mask=255, lightLinks=True, shadowLinks=True)
print("EXPORTED", ass)
maya.standalone.uninitialize()
