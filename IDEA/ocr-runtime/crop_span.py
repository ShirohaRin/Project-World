"""裁剪连续视觉行合并图放大，确认行间连接处的标点。用法: python crop_span.py <起始行> <结束行> <放大倍率>"""
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
scale = int(sys.argv[3]) if len(sys.argv) > 3 else 4
top = lines[a]["source_y"][0] - 5
bottom = lines[b]["source_y"][1] + 5
crop = img[top:bottom, :]
crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
out = cache / "ch76-probe" / f"span-{a:03d}-{b:03d}-x{scale}.png"
ok, enc = cv2.imencode(".png", crop)
enc.tofile(out)
print(out)
