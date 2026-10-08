"""将区域二值化后打印 ASCII 形态图，肉眼看标点形状。用法: python ascii_region.py <x0> <y0> <x1> <y1> [字符宽度px]"""
import sys
from pathlib import Path

import cv2
import numpy as np

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
img = cv2.imdecode(np.fromfile(cache / "2-76 归途（中）.png", dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
ink = gray < 220
x0, y0, x1, y1 = [int(v) for v in sys.argv[1:5]]
step = int(sys.argv[5]) if len(sys.argv) > 5 else 2
region = ink[y0:y1, x0:x1]
for ry in range(0, region.shape[0]):
    row = ""
    for rx in range(0, region.shape[1], step):
        block = region[ry, rx:rx + step]
        row += "#" if block.any() else "."
    print(f"{y0+ry:4d} {row}")
