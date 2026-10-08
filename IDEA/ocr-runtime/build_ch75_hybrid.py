import difflib
import json
import re
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
raw_items = json.loads((cache / "2-75-raw-ocr.json").read_text(encoding="utf-8-sig"))
reconstruction = json.loads((cache / "2-75-line-reconstruction-tight.json").read_text(encoding="utf-8"))
output_path = root / "2-75 归途（上） OCR混合审核稿.md"
report_path = cache / "2-75-hybrid-alignment.json"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("[OCR缺失]", "")


def append_with_overlap(current: str, addition: str) -> str:
    if not current:
        return addition
    limit = min(len(current), len(addition), 100)
    for size in range(limit, 1, -1):
        if current[-size:] == addition[:size]:
            return current + addition[size:]
    return current + addition

# Long-region OCR is the preferred character source. Neighbouring images overlap.
legacy = ""
for item in raw_items:
    legacy = append_with_overlap(legacy, compact(item.get("Text") or ""))

lines = reconstruction["lines"]
structured = "".join(compact(line["text"]) for line in lines)
line_boundaries = []
pos = 0
for line in lines:
    pos += len(compact(line["text"]))
    line_boundaries.append(pos)

matcher = difflib.SequenceMatcher(a=structured, b=legacy, autojunk=False)
blocks = matcher.get_matching_blocks()


def map_position(position: int):
    """Map a structured-text offset onto the long OCR text using adjacent exact blocks."""
    before = None
    after = None
    for block in blocks:
        if block.size == 0:
            after = block
            break
        if block.a + block.size <= position:
            before = block
        elif block.a >= position:
            after = block
            break
        else:
            return block.b + (position - block.a), "exact"
    if before and after:
        a0 = before.a + before.size
        b0 = before.b + before.size
        a1 = after.a
        b1 = after.b
        if a1 > a0:
            mapped = round(b0 + (position - a0) * (b1 - b0) / (a1 - a0))
            return mapped, "interpolated"
    if before:
        return before.b + before.size, "tail"
    return 0, "head"

mapped_ends = []
for boundary in line_boundaries:
    mapped_ends.append(map_position(boundary))

paragraphs = []
current_start_structured = 0
current_start_legacy = 0
boundary_records = []
for index, line in enumerate(lines):
    end_structured = line_boundaries[index]
    end_legacy, method = mapped_ends[index]
    is_paragraph_start = line["indent_chars"] >= 2
    if is_paragraph_start and index > 0:
        paragraphs.append(legacy[current_start_legacy : mapped_ends[index - 1][0]])
        current_start_structured = line_boundaries[index - 1]
        current_start_legacy = mapped_ends[index - 1][0]
    boundary_records.append({
        "line": line["line"],
        "indent_chars": line["indent_chars"],
        "structured_end": end_structured,
        "legacy_end": end_legacy,
        "method": method,
        "paragraph_start": is_paragraph_start,
    })
paragraphs.append(legacy[current_start_legacy:])
paragraphs = [paragraph for paragraph in paragraphs if paragraph]

methods = {}
for record in boundary_records:
    methods[record["method"]] = methods.get(record["method"], 0) + 1
report = {
    "legacy_chars": len(legacy),
    "structured_chars": len(structured),
    "matching_chars": sum(block.size for block in blocks),
    "matching_ratio": matcher.ratio(),
    "paragraphs": len(paragraphs),
    "methods": methods,
    "boundaries": boundary_records,
}
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

notes = (
    "> OCR 混合审核稿说明：文字来自原先的长区域 OCR，以保留句子上下文；"
    "段落边界由原图逐行的 2/4 字符缩进检测结果确定。两套结果通过字符级对齐映射，"
    "未依据语义重新断句。对齐统计见同目录缓存报告。"
)
content = "# 归途（上）\n\n- 作品：来自深渊的我今天也要拯救人类\n- 来源：https://book.sfacg.com/vip/c/3139472/\n\n---\n\n" + notes + "\n\n---\n\n" + "\n\n".join(paragraphs) + "\n"
output_path.write_text(content, encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
