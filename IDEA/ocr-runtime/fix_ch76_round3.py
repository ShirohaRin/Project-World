"""修复：P10 段尾多余引号；P28 清空待重试。"""
import json
from pathlib import Path

p = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76-indent-paragraph-ocr.json")
items = json.loads(p.read_text(encoding="utf-8"))
for x in items:
    if x["paragraph"] == 10:
        x["text"] = x["text"].rstrip("”") if x["text"].endswith("”") else x["text"]
        x["fixed_from_trace"] = "段尾多余引号删除"
    if x["paragraph"] == 28:
        x["text"] = ""
        x["error"] = "retry"
p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print([(x["paragraph"], x["text"]) for x in items if x["paragraph"] in (10, 28)])
