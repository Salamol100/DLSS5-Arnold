"""Delete dlss5_arnold work folders and bulky test data. Keeps test scenes (.ma) and the
comparison images referenced in Docs/TEST_RESULTS.md. Touches only dlss5_arnold/work and /test.

Run:  python clean_test_data.py
"""
import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
TEST = os.path.join(ROOT, "test")
KEEP = re.compile(r"compare|sheet|strip|face|zoom|debug_mv|check|viewcheck|nos_crop|fitview", re.I)
free0 = shutil.disk_usage(ROOT).free

for d in os.listdir(WORK) if os.path.isdir(WORK) else []:
    shutil.rmtree(os.path.join(WORK, d), ignore_errors=True)

kept = 0
for dirpath, _, files in os.walk(TEST):
    for f in files:
        p = os.path.join(dirpath, f)
        ext = os.path.splitext(f)[1].lower()
        if ext in (".ma", ".md") or (ext == ".png" and KEEP.search(f)):
            kept += 1
            continue
        os.remove(p)
for dirpath, dirs, files in sorted(os.walk(TEST), key=lambda t: -len(t[0])):
    if dirpath != TEST and not os.listdir(dirpath):
        os.rmdir(dirpath)

print("freed %.2f GB, C: free now %.1f GB, kept %d test files, work folders left %d" % (
    (shutil.disk_usage(ROOT).free - free0) / 1e9, shutil.disk_usage(ROOT).free / 1e9, kept,
    len(os.listdir(WORK)) if os.path.isdir(WORK) else 0))
