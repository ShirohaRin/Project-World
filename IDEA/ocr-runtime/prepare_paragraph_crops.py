"""通用：visual-lines -> 缩进段落裁剪（含段尾换行标点补丁）。

用法: python prepare_paragraph_crops.py <章号> <章节名>
- 段落边界 = 缩进 >= 2 字符的视觉行
- 补丁：识别"段尾换行标点行"（低缩进 + 宽度不足1字符），
  标记 trailing_punct 并记录其归属段落，供全文检查溯源。
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
metadata_path = cache / f"2-{chapter_no}-visual-lines.json"
output_dir = cache / f"2-{chapter_no}-indent-paragraphs"
manifest_path = cache / f"2-{chapter_no}-indent-paragraphs.json"

image = cv2.imdecode(np.fromfile(source_path, dtype=np.uint8), cv2.IMREAD_COLOR)
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
lines = metadata["records"]
char_width = metadata["character_width"]

starts = [index for index, line in enumerate(lines) if line["indent_chars"] is not None and line["indent_chars"] >= 2]

output_dir.mkdir(parents=True, exist_ok=True)
for old in output_dir.glob("*.png"):
    old.unlink()

records = []
for number, start_index in enumerate(starts, start=1):
    end_index = starts[number] if number < len(starts) else len(lines)
    span = lines[start_index:end_index]
    # 补丁：段尾换行标点行 = 非段首、宽度不足1字符的孤立标点
    trailing = []
    for line in span:
        if line["indent_chars"] is None or line["indent_chars"] >= 2:
            continue
        width = (line["last_x"] - line["first_x"]) if line["first_x"] is not None else None
        if width is not None and width < char_width:
            trailing.append(line["line"])

    start_y = max(0, lines[start_index]["source_y"][0] - 5)
    end_y = min(image.shape[0], lines[end_index - 1]["source_y"][1] + 6)
    crop = image[start_y:end_y, :]
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
        "trailing_punct_lines": trailing,
    })

manifest_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
flagged = [(r["paragraph"], r["trailing_punct_lines"]) for r in records if r["trailing_punct_lines"]]
print(json.dumps({
    "paragraphs": len(records),
    "trailing_punct_paragraphs": flagged,
}, ensure_ascii=False, indent=2))
