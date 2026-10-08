"""裁剪指定视觉行放大保存，用于确认内容。用法: python crop_lines.py <行号...>"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
meta = json.loads((cache / "2-76-visual-lines.json").read_text(encoding="utf-8"))
img = cv2.imdecode(np.fromfile(cache / "2-76 归途（中）.png", dtype=np.uint8), cv2.IMREAD_COLOR)
lines = {r["line"]: r for r in meta["records"]}
out_dir = cache / "ch76-probe"
out_dir.mkdir(exist_ok=True)

targets = [int(x) for x in sys.argv[1:]]
for ln in targets:
    r = lines[ln]
    top, bottom = r["source_y"]
    left = max(0, r["first_x"] - 10)
    right = min(img.shape[1], r["last_x"] + 10)
    crop = img[top:bottom, left:right]
    crop = cv2.resize(crop, None, fx=8, fy=8, interpolation=cv2.INTER_CUBIC)
    name = f"line-{ln:03d}.png"
    ok, enc = cv2.imencode(".png", crop)
    enc.tofile(out_dir / name)
    print(out_dir / name)
