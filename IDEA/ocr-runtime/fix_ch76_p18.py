"""针对溯源确认的误识别，修正第76章 OCR 结果。"""
import json
from pathlib import Path

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
path = cache / "2-76-indent-paragraph-ocr.json"
items = json.loads(path.read_text(encoding="utf-8"))
for item in items:
    if item["paragraph"] == 18:
        before = item["text"]
        # 溯源（视觉行53，8x 放大）确认原文为“，”：逗号+开引号
        item["text"] = item["text"].replace("，”接下来", "，“接下来")
        item["fixed_from_trace"] = "视觉行53细识别：，”→，“"
        print(f"P18 before: ...{before[-30:]}")
        print(f"P18 after : ...{item['text'][-30:]}")
path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print("saved")
