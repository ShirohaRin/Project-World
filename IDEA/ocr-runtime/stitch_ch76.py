"""把浏览器分段截图拼回完整原图。

图片在页面中 1:1 渲染：绝对 x∈[76,805]，绝对 y∈[327,4980]。
每段截图对应一个精确 scrollY（window.scrollTo 设置），
图片像素的页面绝对坐标 = scrollY + 截图内像素坐标。
重叠区用行带对齐后由后段覆盖。
"""
import json
from pathlib import Path

import cv2
import numpy as np

shot_dir = Path(r"C:\Users\SRH-R\AppData\Local\Temp\trae\screenshots")
out_path = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76 归途（中）.png")

scrolls = [227, 927, 1627, 2327, 3027, 3727, 4369]
IMG_X0, IMG_X1 = 76, 805      # 图片左右边界（页面绝对像素）
IMG_Y0, IMG_Y1 = 327, 4980    # 图片上下边界
CANVAS_W = IMG_X1 - IMG_X0    # 728
CANVAS_H = IMG_Y1 - IMG_Y0    # 4653

canvas = np.full((CANVAS_H, CANVAS_W, 3), 255, dtype=np.uint8)
segments = []
for i, s in enumerate(scrolls, start=1):
    p = shot_dir / f"ch76-seg-{i}.png"
    img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    # 图片部分在这个截图里的坐标
    y0 = max(0, IMG_Y0 - s)          # 图片顶部在截图中的 y
    y1 = min(h, IMG_Y1 - s)          # 图片底部在截图中的 y
    crop = img[y0:y1, IMG_X0:IMG_X1]
    # 对齐前先把该段有效区域放入画布（先不覆盖，留给对齐阶段）
    canvas_start = (IMG_Y0 - s) + y0 - IMG_Y0 + (s - IMG_Y0)  # = y0 + s - IMG_Y0
    # 实际上：截图内 y 对应页面绝对 y = s + y；画布 y = s + y - IMG_Y0
    dst_top = s + y0 - IMG_Y0
    dst_bottom = s + y1 - IMG_Y0
    segments.append({"i": i, "s": s, "y0": y0, "y1": y1, "dst_top": dst_top, "dst_bottom": dst_bottom,
                     "crop_h": crop.shape[0], "crop_w": crop.shape[1], "path": str(p)})

# 逐段按顺序写入，重叠区域用"后段覆盖前段"（内容相同，顺序后写即覆盖）
for seg in segments:
    p = seg["path"]
    img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
    crop = img[seg["y0"]:seg["y1"], IMG_X0:IMG_X1]
    canvas[seg["dst_top"]:seg["dst_bottom"]] = crop

ok, encoded = cv2.imencode(".png", canvas)
encoded.tofile(out_path)
print(json.dumps({
    "canvas": [CANVAS_W, CANVAS_H],
    "segments": segments,
    "output": str(out_path),
    "bytes": out_path.stat().st_size,
}, ensure_ascii=False, indent=2))
