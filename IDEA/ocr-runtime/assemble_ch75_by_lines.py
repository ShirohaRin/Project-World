import json
from collections import defaultdict
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
ocr_path = cache / "2-75-cell-ocr-tight.json"
metadata_path = cache / "2-75-visual-lines.json"
report_path = cache / "2-75-line-reconstruction-tight.json"
output_path = root / "2-75 归途（上） OCR逐行审核稿.md"

items = json.loads(ocr_path.read_text(encoding="utf-8"))
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
by_line = defaultdict(list)
for item in items:
    by_line[item["line"]].append(item)

lines = []
for record in metadata["records"]:
    group_items = sorted(by_line[record["line"]], key=lambda item: item["group"])
    text = ""
    missing_groups = []
    for item in group_items:
        piece = item.get("text", "")
        if not piece:
            missing_groups.append(item["group"])
            piece = "[OCR缺失]"
        text += piece
    expected_group_count = max((item["group"] for item in group_items), default=0)
    lines.append({
        "line": record["line"],
        "indent_chars": record["indent_chars"],
        "text": text,
        "char_count": len(text),
        "groups": expected_group_count,
        "missing_groups": missing_groups,
    })

paragraphs = []
current = ""
for line in lines:
    # The original layout reserves 2 or 4 character cells for new paragraphs.
    if line["indent_chars"] >= 2:
        if current:
            paragraphs.append(current)
        current = line["text"]
    else:
        current += line["text"]
if current:
    paragraphs.append(current)

# A continuation row normally has 23 printed characters. Keep exceptions visible.
anomalies = [
    line for line in lines
    if line["indent_chars"] == 0 and line["char_count"] != 23
]
report_path.write_text(json.dumps({"lines": lines, "anomalies": anomalies}, ensure_ascii=False, indent=2), encoding="utf-8")

notes = (
    "> OCR 逐行审核稿说明：本章由原图逐视觉行切分，段首依据左侧 2/4 个字符的实际缩进重建。"
    "每行汉字层再分为最多五字的无余量字符格识别，并按原始位置直接拼接。\n>\n"
    f"> 共重建 {len(lines)} 行、{len(paragraphs)} 段；{len(anomalies)} 个非段首续行的合并字符数不等于 23，"
    "已在重建报告中列出，需以原图复核。"
)
content = "# 归途（上）\n\n- 作品：来自深渊的我今天也要拯救人类\n- 来源：https://book.sfacg.com/vip/c/3139472/\n\n---\n\n" + notes + "\n\n---\n\n" + "\n\n".join(paragraphs) + "\n"
output_path.write_text(content, encoding="utf-8")
print(json.dumps({"lines": len(lines), "paragraphs": len(paragraphs), "anomalies": len(anomalies)}, ensure_ascii=False))
