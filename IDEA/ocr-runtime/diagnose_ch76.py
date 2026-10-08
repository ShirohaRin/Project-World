"""按行打印墨迹密度，判断文字排列方式。"""
import json
from pathlib import Path

import cv2
import numpy as np

source_path = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76 归途（中）.png")
image = cv2.imdecode(np.fromfile(source_path, dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
ink = gray < 220
row_counts = ink.sum(axis=1)

# 每20行打印一次墨迹量
for y in range(0, image.shape[0], 20):
    end = min(y + 20, image.shape[0])
    seg = row_counts[y:end]
    bar = "#" * min(80, int(seg.sum() / 10))
    print(f"y {y:3d}-{end:3d}: ink={int(seg.sum()):6d} {bar}")
