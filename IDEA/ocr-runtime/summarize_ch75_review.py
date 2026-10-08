import json
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
reconstruction = json.loads((cache / "2-75-line-reconstruction-tight.json").read_text(encoding="utf-8"))
report_path = cache / "2-75-review-issues.md"

missing = [line for line in reconstruction["lines"] if line["missing_groups"]]
anomalies = reconstruction["anomalies"]

content = [
    "# 第75章逐行 OCR 审核清单",
    "",
    "- 原图视觉行：131",
    "- 按原图缩进重建段落：59",
    f"- 缺失 OCR 小图组：{sum(len(line['missing_groups']) for line in missing)}",
    f"- 非段首续行字符数异常：{len(anomalies)}",
    "",
    "## 缺失小图组",
    "",
]
for line in missing:
    content.append(f"- 第 {line['line']} 行，缺失组：{', '.join(map(str, line['missing_groups']))}；当前重建：{line['text']}")
content.extend(["", "## 字符数异常续行", ""])
for line in anomalies:
    content.append(f"- 第 {line['line']} 行，{line['char_count']} 字：{line['text']}")
report_path.write_text("\n".join(content) + "\n", encoding="utf-8")
print(f"missing_groups={sum(len(line['missing_groups']) for line in missing)} anomalies={len(anomalies)}")
