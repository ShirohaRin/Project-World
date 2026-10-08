"""统一 P38 省略号为半角 ...（溯源行级识别确认）。"""
import json
from pathlib import Path

p = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76-indent-paragraph-ocr.json")
items = json.loads(p.read_text(encoding="utf-8"))
for x in items:
    if x["paragraph"] == 38:
        x["text"] = x["text"].replace("……", "...")
        x["fixed_from_trace"] = "省略号统一为半角...(溯源确认)"
        print("P38:", x["text"])
p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print("saved")
