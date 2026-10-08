"""清空 P24/P27/P28 的 text 以便重新 OCR。"""
import json
from pathlib import Path

p = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76-indent-paragraph-ocr.json")
items = json.loads(p.read_text(encoding="utf-8"))
for x in items:
    if x["paragraph"] in (24, 27, 28):
        x["text"] = ""
        x["char_count"] = 0
        x["error"] = "retry"
p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print("cleared 24,27,28")
