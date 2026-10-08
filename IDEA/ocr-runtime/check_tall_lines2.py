"""高视觉行内部：按墨迹段（gap<=2 合并）检测各文字行的 first_x，判断缩进。"""
import json
from pathlib import Path

import cv2
import numpy as np

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
meta = json.loads((cache / "2-76-visual-lines.json").read_text(encoding="utf-8"))
img = cv2.imdecode(np.fromfile(cache / "2-76 归途（中）.png", dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
ink = gray < 220
recs = meta["records"]
cw = meta["character_width"]
baseline = meta["baseline_x"]

for line_no in (24, 46, 68, 90):
    r = next(x for x in recs if x["line"] == line_no)
    top, bottom = r["source_y"]
    region = ink[top:bottom]
    rows = region.any(axis=1)
    # 用 gap<=2 合并墨迹行，分离拼音层与汉字层
    groups = []
    in_k = False
    st = 0
    for y, has in enumerate(rows):
        if has and not in_k:
            st = y; in_k = True
        elif not has and in_k:
            groups.append([st, y - 1]); in_k = False
    if in_k:
        groups.append([st, len(rows) - 1])
    # 拼音层与汉字层间 gap 通常 3-5px，行间 gap 更大；这里按组直接输出供判断
    print(f"=== 视觉行{line_no} (y {top}-{bottom}) ===")
    prev_end = None
    for gs, ge in groups:
        gap = (gs - prev_end) if prev_end is not None else 0
        row_ink = region[gs:ge + 1]
        cols = np.where(row_ink.any(axis=0))[0]
        first_x = int(cols[0]) if len(cols) else None
        last_x = int(cols[-1]) if len(cols) else None
        indent = max(0, round((first_x - baseline) / cw)) if first_x is not None else None
        print(f"  段 y={top+gs}-{top+ge} 高={ge-gs+1} gap_before={gap} first_x={first_x} last_x={last_x} indent={indent}")
        prev_end = ge
