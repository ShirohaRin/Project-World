import json
from pathlib import Path

import cv2
import numpy as np

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
source_path = cache / "2-75 归途（上）.png"
groups_path = cache / "2-75-line-groups.json"
output_dir = cache / "2-75-visual-lines"
metadata_path = cache / "2-75-visual-lines.json"

image = cv2.imdecode(np.fromfile(source_path, dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
ink = gray < 220
groups = json.loads(groups_path.read_text(encoding="utf-8-sig"))

# Only a 5px gap belongs to the same printed line's pinyin and Hanzi layers.
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
# The 728px layout uses roughly 31px per printed character, including punctuation spacing.
character_width = 31
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
print(json.dumps({"visual_line_count": len(records), "baseline_x": baseline}, ensure_ascii=False))
