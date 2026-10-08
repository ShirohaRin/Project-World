"""检查缩进异常的视觉行及其上下文。"""
import json
from pathlib import Path

meta = json.loads(Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76-visual-lines.json").read_text(encoding="utf-8"))
recs = meta["records"]
cw = meta["character_width"]
baseline = meta["baseline_x"]

for i, r in enumerate(recs):
    if r["indent_chars"] == 1:
        print("=== indent=1 行 #", r["line"], "===")
        for j in range(max(0, i - 2), min(len(recs), i + 3)):
            rj = recs[j]
            w = (rj["last_x"] - rj["first_x"]) if rj["first_x"] is not None else None
            print(f"  line {rj['line']}: indent={rj['indent_chars']} first_x={rj['first_x']} last_x={rj['last_x']} width={w} chars~{None if w is None else round(w/cw,1)}")
