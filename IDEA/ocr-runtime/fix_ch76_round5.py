"""修正 P16（无逗号）并确认 P38 省略号形态。"""
import json
from pathlib import Path

p = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76-indent-paragraph-ocr.json")
items = json.loads(p.read_text(encoding="utf-8"))
for x in items:
    if x["paragraph"] == 16:
        before = x["text"]
        x["text"] = x["text"].replace("我听他，对异教徒说", "我听他对异教徒说")
        x["fixed_from_trace"] = "像素溯源：'他''对'间无逗号"
        print("P16:", before, "->", x["text"])
    if x["paragraph"] == 38:
        print("P38 完整文本:")
        print(x["text"])
        print("P38 中文省略号次数:", x["text"].count("……"))
p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print("saved")
