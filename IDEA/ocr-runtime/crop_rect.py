"""裁剪原图指定矩形区域放大。用法: python crop_rect.py <x0> <y0> <x1> <y1> <scale> <名称>"""
import sys
from pathlib import Path

import cv2

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
img = cv2.imdecode(__import__("numpy").fromfile(cache / "2-76 归途（中）.png", dtype=__import__("numpy").uint8), cv2.IMREAD_COLOR)
x0, y0, x1, y1, scale, name = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
crop = img[y0:y1, x0:x1]
crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
out = cache / "ch76-probe" / name
ok, enc = cv2.imencode(".png", crop)
enc.tofile(out)
print(out)
