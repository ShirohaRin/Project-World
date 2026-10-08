import json
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
path = root / "ocr-cache" / "2-75-indent-paragraph-ocr.json"
items = json.loads(path.read_text(encoding="utf-8"))
verified = {
    41: "“……”",
    42: "又是一阵诡异的寂静之后，先开口说话的还是那什么医生。",
    43: "“呃...你们有人听懂了她在说什么吗？”",
    44: "“嗯，...略懂。”",
}
for item in items:
    if item["paragraph"] in verified:
        item["text"] = verified[item["paragraph"]]
        item["char_count"] = len(item["text"])
        item["verified_from_image"] = True
path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print("Corrected paragraphs: " + ", ".join(map(str, verified)))
