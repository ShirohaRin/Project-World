"""P28 手动写入（源自切片细识别）并统一省略号。"""
import json
from pathlib import Path

p = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76-indent-paragraph-ocr.json")
items = json.loads(p.read_text(encoding="utf-8"))
text = ("“而第二个阶段，叫做复苏。这个阶段的业火一旦燃起，不仅温度要高了不止一个层次，"
        "火焰能轻易融化钢铁，还能恢复伤势...连致命伤都可以瞬间愈合，哪怕是砍断他们的手脚，"
        "划开他们的喉咙...你已经见识过了吧？”")
for x in items:
    if x["paragraph"] == 28:
        x["text"] = text
        x["char_count"] = len(text)
        x["error"] = None
        x["fixed_from_trace"] = "切片细识别写入；省略号拆行统一为..."
p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print([(x["paragraph"], x["text"]) for x in items if x["paragraph"] == 28])
