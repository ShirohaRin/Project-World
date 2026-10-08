import json
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
items = json.loads((cache / "2-75-raw-ocr.json").read_text(encoding="utf-8-sig"))

parts = []
for item in items:
    text = (item.get("Text") or "").strip()
    if text:
        parts.append(text)

body = "\n\n".join(parts)
review_notes = """
> OCR 审核稿说明：本章来自 VIP 图片正文。以下文字由分块 OCR 按原顺序合并，尚未作为原文归档定稿。
>
> 已知高风险：图片包含汉字上方的拼音注音层，可能造成同音字或称谓误识别；相邻切片存在重叠，当前保留重复处以便核对。请以原图核对后再确认是否替换为正式归档。
""".strip()

content = f"""# 归途（上）

- 作品：来自深渊的我今天也要拯救人类
- 来源：https://book.sfacg.com/vip/c/3139472/

---

{review_notes}

---

{body}
"""
(root / "2-75 归途（上） OCR审核稿.md").write_text(content, encoding="utf-8")
