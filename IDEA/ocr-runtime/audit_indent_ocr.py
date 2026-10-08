"""通用：全文标点完整性/语义检查，溯源到切片并生成待核清单。

用法: python audit_indent_ocr.py <章号> <章节名>
检查项：
1. 引号未闭合（“ 与 ” 数量不一致）
2. 段尾不完整标点（以逗号/冒号/分号/顿号结尾，或结尾为孤立引号）
3. 段首异常（非引号/书名号的标点开头）
4. 超短段（可能漏字或只有标点）
5. 换行标点段落标记（来自裁剪补丁）核对
输出：<章号>-indent-paragraph-review.md（可点击溯源切片图）
"""
import json
import re
import sys
from pathlib import Path

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"

chapter_no = sys.argv[1]
chapter_title = sys.argv[2]
ocr_path = cache / f"2-{chapter_no}-indent-paragraph-ocr.json"
crop_dir = cache / f"2-{chapter_no}-indent-paragraphs"
output = cache / f"2-{chapter_no}-indent-paragraph-review.md"

items = json.loads(ocr_path.read_text(encoding="utf-8"))
items.sort(key=lambda item: item["paragraph"])

TERMINATORS = "。！？…”"  # 段尾允许的终止符（含中文闭引号）
BAD_TAIL = "，、；：,;:"
OPENERS = "“『（（「"
HALF_PUNCT = ".,!?;:、，。！？；："
ELLIPSIS_RE = re.compile(r"…{1,}")

issues = []
for item in items:
    text = item["text"].strip()
    para = item["paragraph"]
    reasons = []

    # 1. 引号闭合
    if text.count("“") != text.count("”"):
        reasons.append(f"引号未闭合（开{text.count('“')} 闭{text.count('”')}）")

    # 2. 段尾不完整
    if text and text[-1] in BAD_TAIL:
        reasons.append(f"段尾为不完整标点『{text[-1]}』")
    if text and text[-1] in "“『（":
        reasons.append(f"段尾为孤立开引号『{text[-1]}』")

    # 3. 段首异常
    if text and text[0] in "。！？…、，；：" and text not in {'！！！！'}:
        reasons.append(f"段首为异常标点『{text[0]}』")

    # 4. 超短段
    if len(text) <= 3 and text not in {'“……”', '……”', '……', '“嗯。”', '“好。”', '“嗯？”', '“哦。”', '哐。', '哐当！', '啪！', '啪嗒。', '月步。', '唰——', '！！！！', '等等。', '不行。', '好吃！', '咔嚓。', '“不。”', '是啊。'}:
        reasons.append(f"超短输出（{len(text)}字符）需核对")

    # 5. 换行标点段核对：来自裁剪补丁标记
    trailing = item.get("trailing_punct_lines") or []
    if trailing:
        reasons.append(f"含换行标点行（视觉行 {trailing}），已按补丁并入本段，请确认语序")

    if reasons:
        issues.append((item, reasons))

lines_out = [
    f"# 第{chapter_no}章段落 OCR 待核清单",
    "",
    f"- 作品：《来自深渊的我今天也要拯救人类》",
    f"- 章节：{chapter_title}（第 {chapter_no} 章）",
    f"- 共识别自然段：{len(items)}",
    f"- 自动标出待核段：{len(issues)}",
    "",
]
for item, reasons in issues:
    crop = crop_dir / item["image"]
    link = crop.as_posix() if crop.exists() else "(切片缺失)"
    loc = f"原图第 {item['first_line']}–{item['last_line']} 行" if "first_line" in item else item.get("break_note", "")
    lines_out.extend([
        f"## 段落 {item['paragraph']}（{loc}）",
        "",
        f"- 原因：{'；'.join(reasons)}",
        f"- OCR：{item['text']}",
        f"- 切片图：[{item['image']}](file:///{link})",
        "",
    ])

output.write_text("\n".join(lines_out), encoding="utf-8")
print(json.dumps({"paragraphs": len(items), "issues": len(issues)}, ensure_ascii=False, indent=2))
for item, reasons in issues:
    print(f"  [P{item['paragraph']}] {'；'.join(reasons)} | {item['text'][:60]}")
