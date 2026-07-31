# 元数据优先的学术文献观察器

该项目现在是一个可运行的多来源元数据编排器，而不是论文下载器。它优先将公开索引返回的书目信息、摘要、DOI/arXiv ID、来源落地页、开放获取（OA）指示和引用数保存到本地 SQLite；不会下载 PDF、抓取正文、处理付费墙，或尝试绕过访问控制。

## 来源策略

默认启用的采集顺序是：

1. **OpenAlex**：主来源。按出版日期首次回溯，之后依据本地 checkpoint 做增量采集。可填 `mailto` 以遵循 polite pool 约定。
2. **Crossref**：按 `from-update-date` 补充 DOI 登记与更新记录。Crossref 仅能可靠提供元数据，OA 状态默认标为 `unknown`。
3. **arXiv**：补充预印本元数据，记录 arXiv 落地页并标示为开放获取，不再下载 PDF。

可选来源默认关闭：

- **Semantic Scholar**：基于关键词搜索；可选填写 API Key。
- **CORE**：基于关键词搜索；需要 API Key。
- **Europe PMC**：适合生命科学文献增强；公开检索 API，无需 Key。

对来源返回的闭源、OA 未知或需要订阅的记录，程序只保存其合法元数据和落地页，以及 `oa_status=closed_or_unknown` 或 `unknown`。`oa_url` 只是来源公开声明的开放链接，程序不会访问或下载该链接。

## 配置

编辑 [config.json](config.json)。每个来源均有 `enabled`、`limit` 和来源特有的查询参数。`initial_lookback_days` 是一个来源首次运行时的回溯窗口；成功后，`source_checkpoints` 表保存该来源上次成功采集的日期，用作后续增量起点。

为避免意外全量抓取，配置中的每个 `limit` 都是有界值。`openalex.cursor` 暂保留为 `*`，适用于小批量初次或按日期采集；若要有状态地遍历超大结果集，应将每页游标与来源响应中的 `meta.next_cursor` 作为独立、受控的运行状态实现，而不是一次性抓取全库。

不要将真实 API Key 提交到版本库；建议通过未跟踪的本地配置副本保存。

## 运行

在本目录执行：

```powershell
python .\paper_watcher.py --help
python .\paper_watcher.py sync
python .\paper_watcher.py sync --source openalex --source crossref
python .\paper_watcher.py sync --source openalex --full
```

运行结果写入 `latest_report.json`。其内容按来源列出采集起点、收到的记录数、新增的规范化文献数，或单个来源的错误；一个来源失败不会阻塞其他来源。

`--full` 只对 OpenAlex 生效：它将采集起点设置到 `1800-01-01`，但仍严格受 `limit` 约束。因此它仅用于验证或启动受控的历史元数据采集，不会在单次运行中抓取全库。通常运行不带该参数的 `sync`，会从每个来源的 checkpoint 增量更新。

## 数据模型与去重

`papers.sqlite3` 保留历史表，同时新增以下表：

- `metadata_works`：规范化文献主记录，优先以 DOI 作为 `work_id`，其次使用 arXiv ID，最后回退到来源稳定 ID 的哈希。
- `metadata_source_records`：来源记录到规范化文献的映射与原始标准化 JSON，保留审计线索。
- `source_checkpoints`：每个来源的上次成功采集时间和增量起点。

同一 DOI 或 arXiv ID 再次出现时更新已有元数据记录。不同来源没有这两个标识时会先保守地作为独立记录保存，避免用模糊标题匹配造成错误合并。

## 扩展来源

来源适配协议定义在 [sources.py](sources.py) 的 `SourceAdapter`：实现 `name` 和 `collect(HarvestContext)`，返回统一字段即可由编排器保存。适配器只能使用其官方或明确公开的 API，且必须返回元数据和落地页/OA 指示，不能在适配器中实现全文下载或访问限制规避。
