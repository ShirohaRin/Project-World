"""对高行内部按已知子行边界计算 first_x/indent。"""
import cv2
import json
import numpy as np
from pathlib import Path

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
meta = json.loads((cache / "2-76-visual-lines.json").read_text(encoding="utf-8"))
img = cv2.imdecode(np.fromfile(cache / "2-76 归途（中）.png", dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
ink = gray < 220
cw = meta["character_width"]
baseline = meta["baseline_x"]

# (视觉行号, [(子行y0, y1, 备注), ...])
plan = {
    46: [(1797, 1821, "子行1"), (1835, 1859, "子行2"), (1873, 1897, "子行3")],
    68: [(2709, 2733, "子行1"), (2747, 2771, "子行2"), (2785, 2809, "子行3")],
    90: [(3631, 3646, "子行1"), (3659, 3683, "子行2"), (3697, 3722, "子行3")],
}
for line_no, subs in plan.items():
    print(f"=== 视觉行{line_no} ===")
    for y0, y1, note in subs:
        region = ink[y0:y1 + 1]
        cols = np.where(region.any(axis=0))[0]
        if len(cols) == 0:
            print(f"  {note} y{y0}-{y1}: 无墨迹")
            continue
        first_x = int(cols[0])
        last_x = int(cols[-1])
        indent = max(0, round((first_x - baseline) / cw))
        print(f"  {note} y{y0}-{y1}: first_x={first_x} last_x={last_x} indent={indent} 宽~{round((last_x-first_x)/cw,1)}")
