"""裁剪连续视觉行合并图并放大，输出给 VL 精确判断标点。"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
meta = json.loads((cache / "2-76-visual-lines.json").read_text(encoding="utf-8"))
img = cv2.imdecode(np.fromfile(cache / "2-76 归途（中）.png", dtype=np.uint8), cv2.IMREAD_COLOR)
lines = {r["line"]: r for r in meta["records"]}

a, b = int(sys.argv[1]), int(sys.argv[2])
scale = int(sys.argv[3]) if len(sys.argv) > 3 else 5
top = lines[a]["source_y"][0] - 6
bottom = lines[b]["source_y"][1] + 6
left = 0
right = max(lines[a]["last_x"], lines[b]["last_x"]) + 10
crop = img[top:bottom, left:right]
crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
out = cache / "ch76-probe" / f"span-{a:03d}-{b:03d}-full-x{scale}.png"
ok, enc = cv2.imencode(".png", crop)
enc.tofile(out)
print(out)
