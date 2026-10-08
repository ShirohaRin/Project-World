import json
from pathlib import Path

import cv2
import numpy as np

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
source_path = cache / "2-75 归途（上）.png"
metadata_path = cache / "2-75-visual-lines.json"
output_dir = cache / "2-75-indent-paragraphs"
manifest_path = cache / "2-75-indent-paragraphs.json"

image = cv2.imdecode(np.fromfile(source_path, dtype=np.uint8), cv2.IMREAD_COLOR)
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
lines = metadata["records"]
starts = [index for index, line in enumerate(lines) if line["indent_chars"] >= 2]
output_dir.mkdir(parents=True, exist_ok=True)
for old in output_dir.glob("*.png"):
    old.unlink()

records = []
for number, start_index in enumerate(starts, start=1):
    end_index = starts[number] if number < len(starts) else len(lines)
    start_y = max(0, lines[start_index]["source_y"][0] - 5)
    end_y = min(image.shape[0], lines[end_index - 1]["source_y"][1] + 6)
    crop = image[start_y:end_y, :]
    # Keep enough context for paragraph OCR while increasing small glyph clarity.
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    filename = f"paragraph-{number:03d}.png"
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise RuntimeError(f"Unable to encode {filename}")
    encoded.tofile(output_dir / filename)
    records.append({
        "paragraph": number,
        "first_line": lines[start_index]["line"],
        "last_line": lines[end_index - 1]["line"],
        "source_y": [start_y, end_y],
        "image": filename,
    })

manifest_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"paragraphs": len(records)}, ensure_ascii=False))
