"""按第79章实测页面坐标拼接浏览器登录态截图。"""
import json
from pathlib import Path

import cv2
import numpy as np

shot_dir = Path(r"C:\Users\SRH-R\AppData\Local\Temp\trae\screenshots")
out_path = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-79 第十三把月刀.png")
scrolls = [227, 927, 1627, 2327, 3027, 3727, 4427, 5127, 5427]
img_x0, img_x1 = 76, 804
img_y0, img_y1 = 327, 6120
canvas = np.full((img_y1 - img_y0, img_x1 - img_x0, 3), 255, dtype=np.uint8)
segments = []

for index, scroll_y in enumerate(scrolls, start=1):
    path = shot_dir / f"ch79-seg-{index}.png"
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    height = image.shape[0]
    source_top = max(0, img_y0 - scroll_y)
    source_bottom = min(height, img_y1 - scroll_y)
    target_top = scroll_y + source_top - img_y0
    target_bottom = scroll_y + source_bottom - img_y0
    canvas[target_top:target_bottom] = image[source_top:source_bottom, img_x0:img_x1]
    segments.append({"scroll_y": scroll_y, "source": [source_top, source_bottom], "target": [target_top, target_bottom]})

ok, encoded = cv2.imencode(".png", canvas)
if not ok:
    raise RuntimeError("PNG 编码失败")
encoded.tofile(out_path)
print(json.dumps({"size": [img_x1 - img_x0, img_y1 - img_y0], "output": str(out_path), "segments": segments}, ensure_ascii=False))
