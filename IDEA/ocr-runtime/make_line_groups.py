"""从原图做水平投影，把连续墨迹行分组，输出 line-groups.json。

每组是一个连续的墨迹行带（拼音层、汉字层各自独立成组），
后续 prepare_lines.py 再按 5px 间隙把同属一个视觉行的组并起来。
用法: python make_line_groups.py <章号> <章节名>
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"

chapter_no = sys.argv[1]
chapter_title = sys.argv[2]
source_path = cache / f"2-{chapter_no} {chapter_title}.png"
groups_path = cache / f"2-{chapter_no}-line-groups.json"

image = cv2.imdecode(np.fromfile(source_path, dtype=np.uint8), cv2.IMREAD_COLOR)
if image is None:
    raise RuntimeError(f"Unable to read image: {source_path}")
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
ink = gray < 220
rows = ink.any(axis=1)

groups = []
in_ink = False
start = 0
for y, has_ink in enumerate(rows):
    if has_ink and not in_ink:
        start = y
        in_ink = True
    elif not has_ink and in_ink:
        groups.append({"Start": int(start), "End": int(y - 1)})
        in_ink = False
if in_ink:
    groups.append({"Start": int(start), "End": int(rows.size - 1)})

groups_path.write_text(json.dumps(groups, ensure_ascii=False), encoding="utf-8")
print(json.dumps({
    "image": str(source_path),
    "size": [int(image.shape[1]), int(image.shape[0])],
    "groups": len(groups),
    "first_group": groups[0],
    "last_group": groups[-1],
}, ensure_ascii=False, indent=2))
