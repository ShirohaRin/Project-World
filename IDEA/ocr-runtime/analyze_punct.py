"""分析区域内墨迹连通组件，判断标点形态。用法: python analyze_punct.py <x0> <y0> <x1> <y1>"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
img = cv2.imdecode(np.fromfile(cache / "2-76 归途（中）.png", dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
ink = (gray < 220).astype(np.uint8)

x0, y0, x1, y1 = [int(v) for v in sys.argv[1:5]]
region = ink[y0:y1, x0:x1]
num, labels, stats, centroids = cv2.connectedComponentsWithStats(region, connectivity=8)
out = []
for i in range(1, num):
    x, y, w, h, area = stats[i]
    out.append({"x": int(x + x0), "y": int(y + y0), "w": int(w), "h": int(h), "area": int(area)})
out.sort(key=lambda c: (c["x"], c["y"]))
print(json.dumps(out, ensure_ascii=False, indent=1))
