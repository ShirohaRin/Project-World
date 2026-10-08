---
name: "novel-image-ocr"
description: "使用浏览器登录态对《来自深渊的我今天也要拯救人类》图片章节执行可恢复OCR。用户要求处理、续跑或审核该小说图片章节时调用。"
---

# 小说图片章节 OCR

## 任务目标

将《来自深渊的我今天也要拯救人类》的 SF 轻小说图片章节转为可人工审核的 Markdown。最终正文必须忠实于原图：原图决定段落边界和文字事实，模型不得凭上下文补写。

固定流程规范在：[小说图片OCR固定流程.md](../../../IDEA/ocr-runtime/小说图片OCR固定流程.md)。执行前必须完整阅读并遵守；本文件只提供调度规则和不可省略的验收门槛。

## 调用条件

在用户要求以下事项时调用本 Skill：

- OCR、续跑、审核该小说的图片章节。
- 从 SF 轻小说已登录网页提取章节图片。
- 修复本小说既有 OCR 中的段落、标点、引号、空输出或疑似错误。
- 生成最终章节 Markdown。

不适用于普通 PDF、网页文本、其他作品，或用户只询问某个既有 OCR 文件的内容。

## 先决条件

1. 用户提供或当前上下文中可确认章节号、章节名、章节 URL。
2. 浏览器处于 SF 轻小说登录态；需要登录、验证码或付费确认时暂停并请用户操作，绝不输入凭据或绕过限制。
3. `SILICONFLOW_API_KEY` 已存在于环境变量。
4. 所有项目产物仅存放在：
   - `IDEA/ocr-runtime/`
   - `IDEA/文风素材/来自深渊/ocr-cache/`
   - `IDEA/文风素材/来自深渊/`

## 强制工作流

严格按下列顺序执行，任何阶段的验收未通过都不得进入下一阶段：

1. **取图**：用浏览器登录态打开章节，优先直接读取正文图片元素的完整 `getChapPic` URL，并在登录态下保存完整原图；读取 `naturalWidth`、`naturalHeight` 进行验收。只有完整 URL 无法在当前登录态保存时，才退回分段截图拼接，并记录原因。
2. **布局**：顺序运行 `make_line_groups.py`、`prepare_lines.py`、`rebuild_paragraphs.py`。段首由原图左缩进判断；高度大于 45px 的视觉行必须检测内部子带段首。
3. **OCR**：运行 `ocr_indent_paragraphs.py`，只对完整自然段图片做 OCR；并发不超过 3；保留断点续跑与原子写入。
4. **审核**：运行 `audit_indent_ocr.py`。必须检查引号、段尾不完整标点、段首异常、超短/空输出和跨行标点。
5. **溯源修复**：仅对 issue 段打开原始段落切片，必要时用 `peek_image.py` 细识别。只可修改能由切片或像素确认的文字，并记录 `fixed_from_trace`。
6. **交付**：审核输出为 `issues: 0` 后，运行 `build_indent_md.py` 生成 `2-<CHAPTER> <TITLE>.md`，再运行 `cleanup_chapter.py` 清除该章全部中间产物。

## 不可违反的规则

- 不得用无认证 `getChapPic` 下载结果作为最终原图；它可能是订阅占位图。
- 取图时必须先找正文图片元素的完整 `getChapPic` URL；不得把“分段截图拼接”当成默认取图方式。
- 只有完整 URL 在浏览器登录态下确实无法保存时，才允许截图备用方案，并记录失败原因。
- 每章都必须重新测量图片实际尺寸；只有使用备用截图方案时才测量页面边界和滚动位置，禁止复用上一章参数。
- 不得以逐字、逐格、逐视觉行 OCR，或 `difflib`/重叠拼接生成最终正文。
- 不得根据剧情、语义或相邻段落补写或润色原文。
- 已有非空 OCR 结果优先续跑；不得为处理当前章节覆盖或删除已确认章节。
- 若无法从原图确认可疑文字，列为待核阻塞项，不能宣称完成。
- 不提交 Git、不推送、不改动无关文件。
- 每章完成后只保留 `IDEA/文风素材/来自深渊/2-<CHAPTER> <TITLE>.md`，清除该章缓存、截图、JSON、切片图和审核清单。

## 固定命令

在项目根目录使用隔离 Python，按顺序执行：

```powershell
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\make_line_groups.py" <CHAPTER> "<TITLE>"
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\prepare_lines.py" <CHAPTER> "<TITLE>"
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\rebuild_paragraphs.py" <CHAPTER> "<TITLE>"
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\ocr_indent_paragraphs.py" <CHAPTER> "<TITLE>"
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\audit_indent_ocr.py" <CHAPTER> "<TITLE>"
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\build_indent_md.py" <CHAPTER> "<TITLE>" "<URL>"
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\cleanup_chapter.py" <CHAPTER> "<TITLE>"
```

## 交付格式

仅报告可核验事实：章节名与编号、自然段数、OCR 字符数、审核问题数、最终 Markdown 路径。中间 JSON、原图、截图、切片图和审核清单已清理，不在最终报告中列出。审核问题不为 0 时，说明阻塞段和原图溯源路径，不得称为最终成品。
