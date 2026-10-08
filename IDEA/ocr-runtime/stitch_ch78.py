"""把第78章浏览器分段截图拼接为完整原图。"""
from pathlib import Path
import cv2
import numpy as np

shot_dir = Path(r"C:\Users\SRH-R\AppData\Local\Temp\trae\screenshots")
out_path = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-78 间章 暗流涌动.png")
scrolls = [227, 927, 1627, 2327, 3027, 3727, 4427, 5127, 5827]
IMG_X0, IMG_X1 = 76, 805
IMG_Y0, IMG_Y1 = 327, 6500
CANVAS_W, CANVAS_H = IMG_X1 - IMG_X0, IMG_Y1 - IMG_Y0
canvas = np.full((CANVAS_H, CANVAS_W, 3), 255, dtype=np.uint8)
for i, scroll_y in enumerate(scrolls, start=1):
    path = shot_dir / f"ch78-seg-{i}.png"
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"无法读取截图: {path}")
    y0 = max(0, IMG_Y0 - scroll_y)
    y1 = min(img.shape[0], IMG_Y1 - scroll_y)
    dst0 = scroll_y + y0 - IMG_Y0
    dst1 = scroll_y + y1 - IMG_Y0
    canvas[dst0:dst1] = img[y0:y1, IMG_X0:IMG_X1]
ok, encoded = cv2.imencode(".png", canvas)
if not ok:
    raise RuntimeError("无法编码拼接原图")
encoded.tofile(out_path)
print({"output": str(out_path), "size": [CANVAS_W, CANVAS_H], "bytes": out_path.stat().st_size})
