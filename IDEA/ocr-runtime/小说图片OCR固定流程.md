# 小说图片 OCR 固定流程

## 目的与边界

本流程只用于《来自深渊的我今天也要拯救人类》的图片章节 OCR。目标是得到可供人工审核的 Markdown，不是凭上下文改写小说。

- **原图是唯一事实来源。** OCR、模型推断和语义连贯性都不能替代原图。
- **段落边界来自原图布局。** 不可让 OCR 文本自行断段，也不可逐字拼接重建全文。
- **文本来自完整自然段 OCR。** 不使用逐字、逐格或逐视觉行 OCR 作为最终正文。
- 所有运行时文件只允许位于 `IDEA/ocr-runtime/` 和 `IDEA/文风素材/来自深渊/ocr-cache/`，不得使用系统缓存保存工作产物。
- 每章完成并确认 `issues: 0` 后，清除该章全部中间产物，只保留最终章节 Markdown。
- 不提交 Git、不改动其他已完成章节、不改动无关脚本。

## 固定路径与前置条件

```text
项目根目录：G:\Project-World\Project-World
Python：IDEA\ocr-runtime\paddle-venv\Scripts\python.exe
运行脚本：IDEA\ocr-runtime\
缓存目录：IDEA\文风素材\来自深渊\ocr-cache\
最终审核稿：IDEA\文风素材\来自深渊\
```

必须满足：

1. 浏览器已登录 SF 轻小说。
2. 环境变量 `SILICONFLOW_API_KEY` 可用。
3. 已知章节号、章节名与章节 URL。
4. 开始前检查本章是否已有产物；已有有效原图或 OCR 结果时优先续跑，不能无故覆盖。

以下示例变量仅作说明：

```text
CHAPTER=77
TITLE=归途（下）
URL=https://book.sfacg.com/vip/c/3139477/
```

## 流程总览

```text
浏览器登录态打开章节
→ 读取真实图片元素尺寸和页面坐标
→ 绝对滚动分段截图
→ 按页面坐标拼接原图
→ 行分组
→ 视觉行与缩进检测
→ 高视觉行内部子带检测并重建自然段
→ 对完整自然段做并发 OCR
→ 审核全文
→ 对异常项回溯原图切片细识别
→ 重新审核
→ 生成最终章节 Markdown
→ 清理本章全部中间产物
```

每一步都必须成功并检查输出后，才能进入下一步；发现异常时只处理异常段，不重跑或重写已确认段。

---

## 阶段 A：浏览器获取真实原图

### A1. 打开章节并确认登录态

1. 使用浏览器打开章节 URL；不得直接以无认证 HTTP 请求作为最终图源。
2. **优先直接找完整图片 URL**：在页面 DOM 中定位正文图片元素，读取其完整 `getChapPic` `src/currentSrc`，并尝试在浏览器登录态下保存这张完整原图。不能一开始就把整图拆成多张截图。
3. 只有完整 URL 在当前登录态下确实无法保存，才允许进入 A2 的分段截图备用方案，并记录失败原因。
4. 在页面中定位正文图片元素，并读取以下实际值：
   - 图片 `src`（用于记录来源；不等于可直接下载权限）
   - `naturalWidth`、`naturalHeight`
   - 元素页面绝对坐标：`x0`、`y0`、`x1`、`y1`
   - 浏览器视口实际宽高与页面总高
3. 图片必须不是订阅占位图。占位图常见特征是尺寸约 `556×591`、文件约 42KB、带“本章节不存在或者需要订阅才能查看”文字。

### A2. 分段截图备用方案

仅当 A1 已证明完整 URL 无法在浏览器登录态下保存时使用。不得跳过完整 URL 检查。

1. 使用 `window.scrollTo(0, y)` 设置**绝对**滚动位置；禁止累计增量滚动。
2. 截图区间须覆盖 `[y0, y1]`，相邻截图保留约 100～200px 重叠。
3. 每次绝对滚动后等待页面稳定，再截图。
4. 截图文件按本章编号保存，例如：`ch77-seg-1.png`、`ch77-seg-2.png`。
5. 截图只作为拼接输入，截图坐标、滚动值、图片边界必须一并记录。

### A3. 拼接与验收

为本章生成或复用拼接脚本。脚本必须依据本章实测参数设置：

```python
scrolls = [...]
IMG_X0, IMG_X1 = x0, x1
IMG_Y0, IMG_Y1 = y0, y1
CANVAS_W = IMG_X1 - IMG_X0
CANVAS_H = IMG_Y1 - IMG_Y0
```

拼接规则：截图内像素的页面绝对 y 坐标为 `scrollY + screenshotY`，画布 y 坐标为 `scrollY + screenshotY - IMG_Y0`；重叠区由后段覆盖。

阶段性输出命名为：

```text
ocr-cache\2-<CHAPTER> <TITLE>.png
```

该原图只用于当前章节处理，最终清理阶段删除。

验收条件：

- 画布尺寸与页面图片实测尺寸一致（允许拼接造成的 1px 宽度差，但必须记录）。
- 无整段空白、错位、重复拼接或占位图文字。
- 人工快速浏览顶部、中部、底部，确认文字连续。

失败处理：拼接不完整或错位时，重新测量本章坐标并补拍相关截图；禁止套用上一章坐标。

---

## 阶段 B：原图布局分析与段落裁剪

在项目根目录执行。PowerShell 中依次运行：

```powershell
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\make_line_groups.py" <CHAPTER> "<TITLE>"
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\prepare_lines.py" <CHAPTER> "<TITLE>"
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\rebuild_paragraphs.py" <CHAPTER> "<TITLE>"
```

### B1. 行分组

`make_line_groups.py` 按 `gray < 220` 找出连续墨迹行带，输出：

```text
2-<CHAPTER>-line-groups.json
```

拼音层与汉字层此时是独立行带，不能直接视为段落或正文行。

### B2. 视觉行与缩进

`prepare_lines.py` 将间隔不超过 5px 的行带合并为一个视觉行，并按每行最多 23 字符（含标点）估算字符宽度，输出：

```text
2-<CHAPTER>-visual-lines.json
ocr-cache\2-<CHAPTER>-visual-lines\line-*.png
```

段首判断标准：`indent_chars >= 2`。通常对应原图左侧空 2 或 4 个字符。

### B3. 段落重建

`rebuild_paragraphs.py` 必须作为唯一段落裁剪流程。它同时处理：

1. 普通视觉行的 `indent_chars >= 2` 段首。
2. 高度大于 45px 的视觉行内部子带；子带中只要 `indent >= 2`，即使是该高行第一个子带，也必须视为新段落。
3. 每段裁剪为完整自然段并 2 倍放大。

输出：

```text
2-<CHAPTER>-para-breaks.json
2-<CHAPTER>-indent-paragraphs.json
ocr-cache\2-<CHAPTER>-indent-paragraphs\paragraph-*.png
```

必须核验：

- `para-breaks.json` 的段首 y 坐标升序且无近距离重复。
- 高视觉行没有吞掉缩进子段首。
- 每张 `paragraph-*.png` 从段首开始，到下一段首前结束；不可固定高度切块。

若段首异常，只调整/修复段落检测逻辑后重建裁剪，不能通过修改 Markdown 断段来掩盖。

---

## 阶段 C：完整自然段 OCR

执行：

```powershell
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\ocr_indent_paragraphs.py" <CHAPTER> "<TITLE>"
```

固定配置：

- 接口：`https://api.siliconflow.cn/v1/chat/completions`
- 模型：`Qwen/Qwen3.6-35B-A3B`
- `temperature=0`
- 最大并发：3
- 每段最多请求 2 次
- 已有非空文本的段落跳过
- 每完成一段以 `.tmp` + replace 原子写入 JSON

固定 OCR 要求：

```text
这是中文小说的一个完整自然段，文字上方有汉语拼音注音。
请只转录下方的汉字、数字和标点，忽略拼音；保持原文，不解释、不补写。
注意段落末尾若有换行的句号和引号，请按正常语序合并（如“。”）。
```

输出：

```text
2-<CHAPTER>-indent-paragraph-ocr.json
```

禁止事项：

- 不改用逐字、逐格、逐视觉行识别来生成最终正文。
- 不根据前后文补写缺字、剧情或台词。
- 不把 OCR 模型自行润色的措辞当作原文。

---

## 阶段 D：审核、回溯与最小修复

### D1. 自动审核

执行：

```powershell
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\audit_indent_ocr.py" <CHAPTER> "<TITLE>"
```

检查：

- 中文引号 `“` / `”` 数量不一致。
- 段尾为逗号、顿号、分号、冒号等不完整标点。
- 段首为异常标点。
- 超短文本或空文本。
- 段尾换行标点补丁标记。

阶段性输出：

```text
2-<CHAPTER>-indent-paragraph-review.md
```

该审核清单只用于当前章节处理，最终清理阶段删除。

**审核结果必须为 `issues: 0` 才可生成最终章节 Markdown。**

### D2. 异常段回溯

对每条 issue：

1. 打开 review 文件中链接的 `paragraph-XXX.png`。
2. 先凭图像确认是否确有缺字、缺标点、截断或错识别。
3. 使用 `peek_image.py` 对**该单一段落切片**做细识别，提示词只允许转录、忽略拼音、特别确认可疑字符或标点。
4. 若仍不确定，裁剪可疑行/标点区域并再次识别或做像素确认。
5. 只修改该段 JSON 的 `text`、`char_count`，新增 `fixed_from_trace` 说明修复依据。
6. 重跑自动审核，直到 0 问题。

修复原则：

- 图像直接确认的字符和标点可以修复。
- 不可由语义猜测补写。无法确认时保留待核，而不是编造原文。
- 例如切片中确认末尾存在闭引号时，可将缺少闭引号的 OCR 结果 `“你们俩个...` 修复为 `“你们俩个...”`；闭引号必须能在切片中看见。

### D3. 生成最终 Markdown 审核稿

仅在审核为 0 问题后执行：

```powershell
& "G:\Project-World\Project-World\IDEA\ocr-runtime\paddle-venv\Scripts\python.exe" "G:\Project-World\Project-World\IDEA\ocr-runtime\build_indent_md.py" <CHAPTER> "<TITLE>" "<URL>"
```

最终输出：

```text
IDEA\文风素材\来自深渊\2-<CHAPTER> <TITLE>.md
```

最终检查：

- Markdown 段落数等于 OCR JSON 的非空段落数。
- 字符数与 JSON 汇总一致。
- 来源 URL 正确。
- 审核脚本最终输出 `issues: 0`。
- 运行 `cleanup_chapter.py <CHAPTER> "<TITLE>"`，确认缓存目录中不再有本章中间产物。

---

## 标准交付说明

完成后只报告下列事实：

```text
章节名与章节号
自然段数
OCR 字符数
审核问题数（必须为 0）
最终 Markdown 路径：2-<CHAPTER> <TITLE>.md
```

中间 JSON、原图、截图、切片图和审核清单已在交付前清理，不在最终报告中列出。

若有未能从原图确认的内容，明确列为阻塞项，不生成“已审核完成”的结论。

## 禁止偏离清单

除非用户明确变更流程，否则不得：

1. 直接下载无认证 `getChapPic` 响应并当作原图。
2. 复用上一章的图片坐标或滚动位置。
3. 用逐字符 OCR、`difflib` 对齐或重叠文本拼接作为最终文本方案。
4. 根据上下文补写、润色、纠错未经原图确认的文字。
5. 在系统临时目录保存项目 OCR 产物。
6. 未通过自动审核就生成或宣称最终成品。
7. 重跑、覆盖或删除已确认章节，只为了处理当前章节。
