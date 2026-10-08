"""检查高视觉行内部的子行结构与缩进，判断是否隐藏段首。"""
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
    # 找出该区域的"文字行带"：按每行是否有墨迹分组（gap>5 断开）
    rows = region.any(axis=1)
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
    # 合并拼音+汉字对（gap<=5）为子行
    sublines = []
    cur_s, cur_e = groups[0]
    for gs, ge in groups[1:]:
        if gs - cur_e <= 5:
            cur_e = ge
        else:
            sublines.append([cur_s, cur_e]); cur_s, cur_e = gs, ge
    sublines.append([cur_s, cur_e])
    print(f"=== 视觉行{line_no} y范围{top}-{bottom}，子行数={len(sublines)} ===")
    for i, (s, e) in enumerate(sublines):
        row_ink = region[s:e + 1]
        cols = np.where(row_ink.any(axis=0))[0]
        if len(cols) == 0:
            print(f"  子行{i+1}: y{s}-{e} 无墨迹")
            continue
        first_x = int(cols[0])
        last_x = int(cols[-1])
        indent = max(0, round((first_x - baseline) / cw))
        print(f"  子行{i+1}: y{s}-{e} first_x={first_x} last_x={last_x} indent={indent} 宽~{round((last_x-first_x)/cw,1)}字符")
