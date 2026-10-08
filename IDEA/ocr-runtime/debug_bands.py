"""调试高行子带切分，打印所有检测到的子带。"""
import cv2
import json
import numpy as np
from pathlib import Path

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
meta = json.loads((cache / "2-76-visual-lines.json").read_text(encoding="utf-8"))
img = cv2.imdecode(np.fromfile(cache / "2-76 归途（中）.png", dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
ink = gray < 220
cw = meta["character_width"]
baseline = meta["baseline_x"]

def split_bands(densities):
    sub_y = []
    in_low = False
    low_start = 0
    seg_start = None
    for y in range(len(densities)):
        low = densities[y] < 25
        if low and not in_low:
            in_low = True
            low_start = y
        elif not low and in_low:
            in_low = False
            if y - low_start >= 2:
                if seg_start is not None:
                    sub_y.append([seg_start, low_start - 1])
                seg_start = y
        if low:
            continue
        if seg_start is None:
            seg_start = y
    if seg_start is not None and not in_low:
        sub_y.append([seg_start, len(densities) - 1])
    elif seg_start is not None and in_low:
        sub_y.append([seg_start, low_start - 1])
    return sub_y

for line_no, top, bottom in [(46, 1793, 1903), (68, 2705, 2816), (90, 3617, 3726)]:
    densities = ink[top:bottom].sum(axis=1)
    bands = split_bands(densities)
    print(f"=== 行{line_no} 子带 ===")
    for y0, y1 in bands:
        if y1 - y0 + 1 < 8:
            continue
        region = ink[top + y0:top + y1 + 1]
        cols = np.where(region.any(axis=0))[0]
        first_x = int(cols[0])
        last_x = int(cols[-1])
        indent = max(0, round((first_x - baseline) / cw))
        print(f"  y{top+y0}-{top+y1} first_x={first_x} indent={indent} 宽~{round((last_x-first_x)/cw,1)}")
