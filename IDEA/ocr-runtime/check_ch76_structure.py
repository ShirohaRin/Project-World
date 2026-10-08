"""检查第76章拼接图的行带结构，确认拼接质量。"""
import json
from pathlib import Path

import cv2
import numpy as np

src = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76 归途（中）.png")
img = cv2.imdecode(np.fromfile(src, dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
ink = gray < 220
rows = ink.any(axis=1)

# 连续墨迹行带
groups = []
in_ink = False
start = 0
for y, has in enumerate(rows):
    if has and not in_ink:
        start = y; in_ink = True
    elif not has and in_ink:
        groups.append([start, y - 1]); in_ink = False
if in_ink:
    groups.append([start, len(rows) - 1])

# 合并 ≤5px 间隔的行带为视觉行
visual = []
cur_s, cur_e = groups[0]
for gs, ge in groups[1:]:
    if gs - cur_e <= 5:
        cur_e = ge
    else:
        visual.append([cur_s, cur_e]); cur_s, cur_e = gs, ge
visual.append([cur_s, cur_e])

print("ink_groups:", len(groups), "visual_lines:", len(visual))
print("first:", visual[0], "last:", visual[-1])
# 视觉行高度分布
hs = [e - s for s, e in visual]
print("heights: min", min(hs), "max", max(hs), "median", sorted(hs)[len(hs)//2])
