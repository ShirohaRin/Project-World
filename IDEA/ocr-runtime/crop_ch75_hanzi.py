import json
from pathlib import Path

import cv2
import numpy as np

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
source = cache / "2-75 归途（上）.png"
metadata_path = cache / "2-75-visual-lines.json"
output_dir = cache / "2-75-hanzi-lines"

image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
output_dir.mkdir(parents=True, exist_ok=True)
for path in output_dir.glob("*.png"):
    path.unlink()

for record in metadata["records"]:
    top, bottom = record["source_y"]
    # The source uses a fixed lower Hanzi baseline and a pinyin layer above it.
    # Keep the bottom 21px plus a small lower margin; this excludes pinyin even
    # when its descenders visually merge with the Hanzi layer.
    hanzi_top = max(top, bottom - 21)
    hanzi_bottom = min(image.shape[0], bottom + 3)

    crop = image[hanzi_top:hanzi_bottom, 0:image.shape[1]]
    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ink_columns = np.where((crop_gray < 220).any(axis=0))[0]
    if len(ink_columns):
        left = max(0, int(ink_columns[0]) - 4)
        right = min(crop.shape[1], int(ink_columns[-1]) + 5)
        crop = crop[:, left:right]
    crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    name = f"line-{record['line']:03d}.png"
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise RuntimeError(f"Could not encode {name}")
    encoded.tofile(output_dir / name)
    record["hanzi_source_y"] = [int(hanzi_top), int(hanzi_bottom)]
    record["hanzi_image"] = name

metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Prepared {len(metadata['records'])} Hanzi line crops")
