"""检测高视觉行内部的所有汉字层带（密度高区段）及其 first_x/indent。

用于找出被高行合并吞掉的段首子行。
用法: python find_para_breaks.py <章号> <章节名>
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
chapter_no = sys.argv[1]
chapter_title = sys.argv[2]
meta = json.loads((cache / f"2-{chapter_no}-visual-lines.json").read_text(encoding="utf-8"))
img = cv2.imdecode(np.fromfile(cache / f"2-{chapter_no} {chapter_title}.png", dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
ink = gray < 220
recs = meta["records"]
cw = meta["character_width"]
baseline = meta["baseline_x"]

# 高行 = 高度 > 45px
tall = [r for r in recs if r["source_y"][1] - r["source_y"][0] > 45]
result = {}
for r in tall:
    line_no = r["line"]
    top, bottom = r["source_y"]
    region = ink[top:bottom]
    densities = region.sum(axis=1)  # 每行墨迹像素数
    # 汉字层带：密度 >= 60 且连续（gap <= 3）
    bands = []
    in_band = False
    st = 0
    last = 0
    for y in range(len(densities)):
        if densities[y] >= 60:
            if not in_band:
                st = y
                in_band = True
            last = y
        else:
            if in_band and (y - last) > 3:
                bands.append([st, last])
                in_band = False
    if in_band:
        bands.append([st, last])
    info = []
    for b0, b1 in bands:
        if b1 - b0 + 1 < 8:
            continue
        rows_ink = region[b0:b1 + 1]
        cols = np.where(rows_ink.any(axis=0))[0]
        first_x = int(cols[0])
        last_x = int(cols[-1])
        indent = max(0, round((first_x - baseline) / cw))
        info.append({
            "band_y": [int(top + b0), int(top + b1)],
            "first_x": first_x,
            "last_x": last_x,
            "indent": indent,
            "is_para_start": indent >= 2,
        })
    result[line_no] = info
    print(f"视觉行{line_no} (y {top}-{bottom}) 汉字层带 {len(info)} 个:")
    for b in info:
        mark = " ★段首" if b["is_para_start"] else ""
        print(f"  y{b['band_y']} first_x={b['first_x']} indent={b['indent']} 宽~{round((b['last_x']-b['first_x'])/cw,1)}字符{mark}")
