import json
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
items = json.loads((cache / "2-75-indent-paragraph-ocr.json").read_text(encoding="utf-8"))
output = root / "2-75 归途（上） OCR段落审核稿.md"

items.sort(key=lambda item: item["paragraph"])
paragraphs = [item["text"].strip() for item in items]
notes = (
    "> OCR 段落审核稿说明：原图先按实际左侧 2/4 字符缩进检测出自然段，"
    "再将每个完整自然段作为单张长图识别。文字识别保留段内上下文，Markdown 段落边界直接来自原图，"
    "未使用逐字拼接或事后断句。"
)
content = "# 归途（上）\n\n- 作品：来自深渊的我今天也要拯救人类\n- 来源：https://book.sfacg.com/vip/c/3139472/\n\n---\n\n" + notes + "\n\n---\n\n" + "\n\n".join(paragraphs) + "\n"
output.write_text(content, encoding="utf-8")
print(json.dumps({"paragraphs": len(paragraphs), "chars": sum(len(text) for text in paragraphs)}, ensure_ascii=False))
