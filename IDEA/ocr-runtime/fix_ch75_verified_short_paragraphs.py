import json
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
path = root / "ocr-cache" / "2-75-indent-paragraph-ocr.json"
items = json.loads(path.read_text(encoding="utf-8"))
for item in items:
    if item["paragraph"] in {37, 38, 40}:
        item["text"] = "“……”"
        item["char_count"] = len(item["text"])
        item["verified_from_image"] = True
path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print("Corrected paragraphs: 37, 38, 40")
