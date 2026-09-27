"""Build dlss5_arnold_github.zip: source, docs, test scenes, config templates and the vendored
ReShade API headers. Excludes every proprietary / third-party binary and shader (NVIDIA NGX
DLLs, ReShade dxgi.dll, the DLSS5 feed / RenoDX add-ons, feed and Lumenite shaders), build and
run output.

Run:  python make_release_zip.py
"""
import fnmatch
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(ROOT), "dlss5_arnold_github.zip")
INCLUDE = ["README.md", ".gitignore", "Docs/*.md", "Docs/images/*.png", "host/dlss5_host.cpp", "host/build.bat",
           "host/third_party/reshade/*", "host/third_party/reshade/include/*",
           "maya/dlss5_enhance.py", "maya/install_shelf.py", "tools/*.py", "tools/*.ps1", "test/*.ma"]
FORBID = ("*.dll", "*.addon64", "*.addon32", "*.exe", "*.fx", "*.fxh", "*.log", "*.pfm", "*.exr")
TEMPLATES = {"runtime/ReShade.ini": "runtime/ReShade.ini.template",
             "runtime/ReShadePreset.ini": "runtime/ReShadePreset.ini.template"}

names = []
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, _, files in os.walk(ROOT):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), ROOT).replace("\\", "/")
            if any(fnmatch.fnmatch(rel, pat) for pat in INCLUDE) and not any(fnmatch.fnmatch(f, x) for x in FORBID):
                z.write(os.path.join(ROOT, rel), "dlss5_arnold/" + rel)
                names.append(rel)
    for src, dst in TEMPLATES.items():
        z.write(os.path.join(ROOT, src), "dlss5_arnold/" + dst)
        names.append(dst)
    z.writestr("dlss5_arnold/runtime/PUT_YOUR_DLSS5_FILES_HERE.txt",
               "Copy your own DLSS 5 runtime here (see README.md > Setup):\n"
               "dxgi.dll (ReShade 6.8), dlss5-feed.addon64 + dlss5-feed.cfg, renodx-dlss5.addon64,\n"
               "nvngx_dlss.dll, nvngx_dlssnr.dll, reshade-shaders/Shaders/{DLSS5_Feed.fx, ReShade.fxh, ReShadeUI.fxh}.\n"
               "Then build host/build.bat (writes dlss5_host.exe here) and rename the .template files.\n")

print("wrote %s (%.1f KB), %d files:" % (OUT, os.path.getsize(OUT) / 1024, len(names) + 1))
for n in sorted(names):
    print("  " + n)
