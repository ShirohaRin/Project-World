"""全文快速语义抽检：定位可疑标点序列与半角引号的段落。用法: python scan_issues.py <章号>"""
import json
import re
import sys
from pathlib import Path

cache = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache")
ch = sys.argv[1]
items = json.loads((cache / f"2-{ch}-indent-paragraph-ocr.json").read_text(encoding="utf-8"))
items.sort(key=lambda x: x["paragraph"])

patterns = [
    (r"[，。！？]{2,}", "连续双标点"),
    (r"\.\s*,|\.{2,3}\s*[,，]", "点号后紧跟逗号"),
    (r'"', "半角引号"),
    (r"\.{1}", "半角点"),
]
for it in items:
    t = it["text"]
    hits = []
    for pat, name in patterns:
        ms = re.findall(pat, t)
        if ms:
            hits.append(f"{name}:{len(ms)}")
    if hits:
        print(f"P{it['paragraph']:02d} (行{it['first_line']}-{it['last_line']}) {'; '.join(hits)}")
        print(f"   {t[:120]}")
