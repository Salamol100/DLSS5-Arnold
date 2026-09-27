"""Detail energy of a series of captures vs the source, ignoring the top 150 rows (ReShade banner).

Run:  mayapy detail_over_time.py source.png capture_01.ppm capture_02.ppm ...
"""
import sys

import numpy as np

from measure_detail import box_blur, load, luma


def energy(img):
    l = luma(img)[150:]
    return np.sqrt(np.mean((l - box_blur(l)) ** 2))


src = energy(load(sys.argv[1]))
print("source detail %.5f" % src)
for p in sys.argv[2:]:
    e = energy(load(p))
    print("%-12s %.5f  %+6.1f%%" % (p.replace("\\", "/").split("/")[-1], e, 100 * (e / src - 1)))
