import json
import re
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
items = json.loads((cache / "2-75-indent-paragraph-ocr.json").read_text(encoding="utf-8"))
output = cache / "2-75-indent-paragraph-review.md"

# A small, transparent review queue for outputs that are implausible for their
# visual structure: isolated prose in an ellipsis/dialogue region, or unclosed quotes.
issues = []
for item in items:
    text = item["text"]
    reasons = []
    if "我们的祖先初始的野蛮人" in text:
        reasons.append("与上下文主题无关，疑似模型幻写")
    if text.count("“") != text.count("”"):
        reasons.append("引号未闭合")
    if len(text) <= 3 and text not in {"“……”", "……", "“嗯。”", "“好。”", "“嗯？”"}:
        reasons.append("极短输出，需要核对原图")
    if reasons:
        issues.append((item, reasons))

content = ["# 第75章段落 OCR 待核清单", "", f"- 共识别自然段：{len(items)}", f"- 自动标出待核段：{len(issues)}", ""]
for item, reasons in issues:
    content.extend([
        f"## 段落 {item['paragraph']}（原图第 {item['first_line']}–{item['last_line']} 行）",
        "",
        f"- 原因：{'；'.join(reasons)}",
        f"- OCR：{item['text']}",
        f"- 图像：[{item['image']}](file:///{(cache / '2-75-indent-paragraphs' / item['image']).as_posix()})",
        "",
    ])
output.write_text("\n".join(content), encoding="utf-8")
print(json.dumps({"paragraphs": len(items), "issues": len(issues)}, ensure_ascii=False))
