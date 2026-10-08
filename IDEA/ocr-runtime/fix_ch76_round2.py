"""第二轮溯源修复：P24 半角引号、P26 省略号拆行。"""
import json
from pathlib import Path

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
path = cache / "2-76-indent-paragraph-ocr.json"
items = json.loads(path.read_text(encoding="utf-8"))
for item in items:
    if item["paragraph"] == 24:
        # 溯源（视觉行74，8x 放大）确认原图是中文引号“嗯。”
        item["text"] = item["text"].replace('"嗯。"', "“嗯。”")
        item["fixed_from_trace"] = "视觉行74细识别：\"嗯。\"→“嗯。”"
        print(f"P24 -> {item['text']}")
    if item["paragraph"] == 26:
        # 溯源（像素分析 + 连图识别）：半角省略号换行被拆为 .. + 行首点，VL 误读为逗号
        before = item["text"]
        item["text"] = item["text"].replace("喉咙.. ,你已经", "喉咙...你已经")
        item["fixed_from_trace"] = "视觉行85/86像素溯源：省略号换行拆行，.. ,→..."
        print(f"P26 before: {before[-40:]}")
        print(f"P26 after : {item['text'][-40:]}")
path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print("saved")
