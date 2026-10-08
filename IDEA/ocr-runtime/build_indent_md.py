"""通用：OCR 结果 -> 最终章节 Markdown。

用法: python build_indent_md.py <章号> <章节名> [来源URL]
"""
import json
import sys
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"

chapter_no = sys.argv[1]
chapter_title = sys.argv[2]
source_url = sys.argv[3] if len(sys.argv) > 3 else f"https://book.sfacg.com/vip/c/{chapter_no}/"

ocr_path = cache / f"2-{chapter_no}-indent-paragraph-ocr.json"
output = root / f"2-{chapter_no} {chapter_title}.md"

items = json.loads(ocr_path.read_text(encoding="utf-8"))
items.sort(key=lambda item: item["paragraph"])
paragraphs = [item["text"].strip() for item in items if item.get("text")]

notes = (
    "> OCR 段落审核稿说明：原图先按实际左侧 2/4 字符缩进检测出自然段，"
    "再将每个完整自然段作为单张长图识别。段尾换行的句号/引号由补丁并入本段并合并语序，"
    "Markdown 段落边界直接来自原图，未使用逐字拼接或事后断句。"
)
content = f"# {chapter_title}\n\n- 作品：来自深渊的我今天也要拯救人类\n- 章节：第 {chapter_no} 章 {chapter_title}\n- 来源：{source_url}\n\n---\n\n{notes}\n\n---\n\n" + "\n\n".join(paragraphs) + "\n"
output.write_text(content, encoding="utf-8")
print(json.dumps({
    "paragraphs": len(paragraphs),
    "chars": sum(len(t) for t in paragraphs),
    "output": str(output),
}, ensure_ascii=False, indent=2))
