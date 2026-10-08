"""检查浏览器分段截图的尺寸与墨迹范围。"""
import json
from pathlib import Path

import cv2
import numpy as np

shot_dir = Path(r"C:\Users\SRH-R\.trae-cn\trae-browser-screenshots\6a79a553c7385b7cc55f5c59")
shots = sorted(shot_dir.glob("shot-20260819-1301*.jpg"))
print("found:", len(shots))
for p in shots:
    img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = gray < 220
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    print(p.name, "size=", img.shape[1], "x", img.shape[0], "bytes=", p.stat().st_size,
          "ink_rows=", (rows.min(), rows.max()) if len(rows) else None,
          "ink_cols=", (cols.min(), cols.max()) if len(cols) else None)
