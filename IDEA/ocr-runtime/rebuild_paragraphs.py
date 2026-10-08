"""重建段落清单：常规缩进段首 + 高行内部段首子带。

用法: python rebuild_paragraphs.py <章号> <章节名>
输出：新 2-<章号>-indent-paragraphs.json（段首按 y 坐标排序），
以及 2-<章号>-para-breaks.json（段首来源说明）。
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
chapter_no = sys.argv[1]
chapter_title = sys.argv[2]
source_path = cache / f"2-{chapter_no} {chapter_title}.png"
meta_path = cache / f"2-{chapter_no}-visual-lines.json"
output_dir = cache / f"2-{chapter_no}-indent-paragraphs"
manifest_path = cache / f"2-{chapter_no}-indent-paragraphs.json"
break_path = cache / f"2-{chapter_no}-para-breaks.json"

image = cv2.imdecode(np.fromfile(source_path, dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
ink = gray < 220
meta = json.loads(meta_path.read_text(encoding="utf-8"))
recs = meta["records"]
cw = meta["character_width"]
baseline = meta["baseline_x"]

breaks = []  # (y, 说明)

# 1) 常规段首：视觉行缩进 >= 2
for r in recs:
    if r["indent_chars"] is not None and r["indent_chars"] >= 2:
        breaks.append([r["source_y"][0] - 5, f"视觉行{r['line']}"])

# 2) 高行（>45px）内部段首子带：密度切分子行，indent>=2
tall = [r for r in recs if r["source_y"][1] - r["source_y"][0] > 45]
for r in tall:
    top, bottom = r["source_y"]
    densities = ink[top:bottom].sum(axis=1)
    # 切分子行：低密度(<25)连续>=2px 为分隔
    sub_y = []  # (y0, y1) 各子行的汉字层
    in_low = False
    low_start = 0
    seg_start = None
    for y in range(len(densities)):
        low = densities[y] < 25
        if low and not in_low:
            in_low = True
            low_start = y
        elif not low and in_low:
            in_low = False
            if y - low_start >= 2:
                # 分隔：结束当前子行
                if seg_start is not None:
                    sub_y.append([seg_start, low_start - 1])
                seg_start = y
        if low:
            continue
        if seg_start is None:
            seg_start = y
    if seg_start is not None and not in_low:
        sub_y.append([seg_start, len(densities) - 1])
    elif seg_start is not None and in_low:
        sub_y.append([seg_start, low_start - 1])

    # 合并拼音层子带（高 <13px 且与下一子带间隔 <6px）到同一文字行
    merged_bands = []
    for (y0, y1) in sub_y:
        if y1 - y0 + 1 < 8:
            continue
        if (merged_bands and y0 - merged_bands[-1][1] < 6
                and merged_bands[-1][1] - merged_bands[-1][0] + 1 < 13):
            merged_bands[-1][1] = y1
        else:
            merged_bands.append([y0, y1])

    for (y0, y1) in merged_bands:
        region = ink[top + y0:top + y1 + 1]
        cols = np.where(region.any(axis=0))[0]
        first_x = int(cols[0])
        indent = max(0, round((first_x - baseline) / cw))
        # 高行内部的段首子带：缩进 >= 2 即是新段落（不跳过第一个子带，
        # 因为高行可能直接从段首开始，如行90 的"我闻言皱起眉头"）
        if indent >= 2:
            breaks.append([top + y0 - 5, f"视觉行{r['line']}子带(y{top+y0}-{top+y1})"])

# 排序去重
breaks.sort(key=lambda b: int(b[0]))
merged = []
for y, note in breaks:
    y = int(y)
    if merged and y - merged[-1][0] < 8:
        merged[-1][1] += f"；{note}"
    else:
        merged.append([y, note])
print(json.dumps({"breaks": merged}, ensure_ascii=False, indent=1))
break_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")

# 3) 生成段落
output_dir.mkdir(parents=True, exist_ok=True)
for old in output_dir.glob("*.png"):
    old.unlink()

records = []
boundaries = [b[0] for b in merged]
for number, start_y in enumerate(boundaries, start=1):
    end_y = boundaries[number] if number < len(boundaries) else image.shape[0]
    crop = image[start_y:end_y, :]
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    filename = f"paragraph-{number:03d}.png"
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise RuntimeError(f"Unable to encode {filename}")
    encoded.tofile(output_dir / filename)
    records.append({
        "paragraph": number,
        "source_y": [start_y, end_y],
        "image": filename,
        "break_note": merged[number - 1][1],
    })
manifest_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"paragraphs": len(records)}, ensure_ascii=False))
