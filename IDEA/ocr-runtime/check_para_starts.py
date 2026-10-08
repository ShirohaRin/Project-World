"""检查每个段落的起始视觉行缩进，找出段首缩进异常的段落。"""
import json
from pathlib import Path

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
meta = json.loads((cache / "2-76-visual-lines.json").read_text(encoding="utf-8"))
recs = {r["line"]: r for r in meta["records"]}
items = json.loads((cache / "2-76-indent-paragraph-ocr.json").read_text(encoding="utf-8"))
items.sort(key=lambda x: x["paragraph"])
print("段落 | first_line indent | 段落开头文本")
for it in items:
    fl = it["first_line"]
    r = recs.get(fl)
    indent = r["indent_chars"] if r else None
    flag = " <<< 异常" if (indent is None or indent < 2) else ""
    print(f"P{it['paragraph']:02d} | first_line={fl} indent={indent}{flag} | {it['text'][:26]}")
