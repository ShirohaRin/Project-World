import json
import math
from pathlib import Path

import cv2
import numpy as np

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
source = cache / "2-75 归途（上）.png"
metadata_path = cache / "2-75-visual-lines.json"
output_dir = cache / "2-75-cell-groups-tight"
manifest_path = cache / "2-75-cell-groups-tight.json"

image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
meta = json.loads(metadata_path.read_text(encoding="utf-8"))
char_width = meta["character_width"]
chars_per_group = 5
output_dir.mkdir(parents=True, exist_ok=True)
for path in output_dir.glob("*.png"):
    path.unlink()

records = []
for line in meta["records"]:
    top, bottom = line["hanzi_source_y"]
    line_width = line["last_x"] - line["first_x"] + 1
    char_count = max(1, round(line_width / char_width))
    for group_index, start_char in enumerate(range(0, char_count, chars_per_group), start=1):
        group_chars = min(chars_per_group, char_count - start_char)
        left = max(0, line["first_x"] + start_char * char_width)
        right = min(image.shape[1], line["first_x"] + (start_char + group_chars) * char_width)
        crop = image[top:bottom, left:right]
        crop = cv2.resize(crop, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
        # Qwen's vision encoder is unstable on extremely short horizontal images.
        # Add a neutral white canvas without changing the printed pixels.
        padded_height = max(480, crop.shape[0] + 80)
        canvas = np.full((padded_height, crop.shape[1], crop.shape[2]), 255, dtype=np.uint8)
        y = (padded_height - crop.shape[0]) // 2
        canvas[y:y + crop.shape[0], :] = crop
        crop = canvas
        name = f"line-{line['line']:03d}-group-{group_index}.png"
        ok, encoded = cv2.imencode(".png", crop)
        if not ok:
            raise RuntimeError(f"Unable to encode {name}")
        encoded.tofile(output_dir / name)
        records.append({
            "line": line["line"],
            "group": group_index,
            "start_char": start_char,
            "expected_chars": group_chars,
            "line_expected_chars": char_count,
            "indent_chars": line["indent_chars"],
            "image": name,
        })

manifest_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"groups": len(records), "lines": len(meta['records'])}, ensure_ascii=False))
