"""清理单章 OCR 中间产物，只保留最终章节 Markdown。"""
import shutil
import sys
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
shot_dir = Path(r"C:\Users\SRH-R\AppData\Local\Temp\trae\screenshots")
chapter_no = sys.argv[1]
chapter_title = sys.argv[2]
final_path = root / f"2-{chapter_no} {chapter_title}.md"

if not final_path.exists():
    raise FileNotFoundError(f"最终 Markdown 不存在，拒绝清理: {final_path}")

patterns = [
    f"2-{chapter_no}*.png",
    f"2-{chapter_no}-*.json",
    f"2-{chapter_no}-*.md",
    f"ch{chapter_no}-seg-*.png",
]
removed = []
for pattern in patterns[:3]:
    for path in cache.glob(pattern):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path))
for path in shot_dir.glob(f"ch{chapter_no}-seg-*.png"):
    path.unlink()
    removed.append(str(path))

print({"final": str(final_path), "removed": removed})
