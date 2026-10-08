"""通用：line-groups -> visual-lines.json（视觉行 + 缩进检测）。

用法: python prepare_lines.py <章号> <章节名>
字符宽度按"每行最多23字符（含标点）"从最宽视觉行估算，兼容不同宽度原图。
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
output_dir = cache / f"2-{chapter_no}-visual-lines"
metadata_path = cache / f"2-{chapter_no}-visual-lines.json"

image = cv2.imdecode(np.fromfile(source_path, dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
ink = gray < 220
groups = json.loads(groups_path.read_text(encoding="utf-8"))

# 同属一个视觉行的组（拼音层 + 汉字层）间隔不超过 5px
visual_lines = []
current_start = groups[0]["Start"]
current_end = groups[0]["End"]
for group in groups[1:]:
    if group["Start"] - current_end <= 5:
        current_end = group["End"]
    else:
        visual_lines.append((current_start, current_end))
        current_start = group["Start"]
        current_end = group["End"]
visual_lines.append((current_start, current_end))

# 合并相邻矮行：竖排字符（引号、省略号等）墨迹断档会拆出高<18px的假行，
# 与相邻行间隔<16px 时并入，避免“引号行/省略号行”被当作独立视觉行。
merged_lines = []
for start, end in visual_lines:
    if merged_lines and (start - merged_lines[-1][1]) < 16 and (
            merged_lines[-1][1] - merged_lines[-1][0] < 18 or end - start < 18):
        merged_lines[-1] = (merged_lines[-1][0], end)
    else:
        merged_lines.append((start, end))
visual_lines = merged_lines

output_dir.mkdir(parents=True, exist_ok=True)
for path in output_dir.glob("*.png"):
    path.unlink()

records = []
for index, (start_y, end_y) in enumerate(visual_lines, start=1):
    top = max(0, start_y - 3)
    bottom = min(image.shape[0], end_y + 4)
    row_ink = ink[top:bottom]
    columns = np.where(row_ink.any(axis=0))[0]
    if len(columns) == 0:
        first_x = None
        last_x = None
        crop = image[top:bottom]
    else:
        first_x = int(columns[0])
        last_x = int(columns[-1])
        left = max(0, first_x - 5)
        right = min(image.shape[1], last_x + 6)
        crop = image[top:bottom, left:right]

    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
    crop_name = f"line-{index:03d}.png"
    encoded, payload = cv2.imencode(".png", crop)
    if not encoded:
        raise RuntimeError(f"Unable to encode {crop_name}")
    payload.tofile(output_dir / crop_name)
    records.append({
        "line": index,
        "source_y": [int(top), int(bottom)],
        "first_x": first_x,
        "last_x": last_x,
        "image": crop_name,
    })

first_positions = [record["first_x"] for record in records if record["first_x"] is not None]
baseline = int(np.percentile(first_positions, 20))
max_last = max(record["last_x"] for record in records if record["last_x"] is not None)
# 每行最多23字符（含标点），字符宽度按内容区最宽行估算
character_width = max(18, round((max_last - baseline) / 23))
for record in records:
    if record["first_x"] is None:
        record["indent_chars"] = None
    else:
        record["indent_chars"] = max(0, round((record["first_x"] - baseline) / character_width))

metadata = {
    "source": str(source_path),
    "visual_line_count": len(records),
    "baseline_x": baseline,
    "character_width": character_width,
    "records": records,
}
metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
indents = sorted(set(r["indent_chars"] for r in records if r["indent_chars"] is not None))
print(json.dumps({
    "visual_line_count": len(records),
    "baseline_x": baseline,
    "character_width": character_width,
    "indent_values": indents,
    "indent_dist": {str(v): sum(1 for r in records if r["indent_chars"] == v) for v in indents},
}, ensure_ascii=False, indent=2))
