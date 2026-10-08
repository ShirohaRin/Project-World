"""读取指定段落的OCR记录与切片信息。用法: python show_para.py <章号> <段落号...>"""
import json
import sys
from pathlib import Path

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
ch = sys.argv[1]
items = json.loads((cache / f"2-{ch}-indent-paragraph-ocr.json").read_text(encoding="utf-8"))
for p in sys.argv[2:]:
    it = next((x for x in items if x["paragraph"] == int(p)), None)
    if it:
        print(json.dumps(it, ensure_ascii=False, indent=2))
