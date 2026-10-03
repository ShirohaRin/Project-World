# Project IDEA Memory 最新使用方法

> 更新日期：2026-09-14
>
> 本文面向开发、联调和跨设备测试。它区分两套当前存在的能力：
>
> 1. **生产跨设备记忆 API**：当前由 `server/platform_auth.py` 的 `PlatformStore` 和云端 `platform.db` 提供，是周末跨设备测试应使用的正式链路。
> 2. **新 `Memory/` 模块**：已经具备分层记忆、Recall、Evidence、Recent、缓存和上传暂存等本地模块能力，但尚未替换生产 API 的权威存储，不能直接把它当作跨设备云同步客户端使用。

## 1. 当前架构和数据边界

```text
客户端设备 A / 设备 B
        |
        | Authorization + X-Device-ID + X-Space-ID
        v
Project IDEA server
        |
        +-- PlatformStore
        |     +-- long_term_memories
        |     +-- sync_events
        |     +-- revision 乐观锁
        |     +-- 软删除
        |
        +-- 当前聊天长期记忆召回

Memory/ 目录
        +-- Recent / Facts / Reflections / Persona
        +-- BM25 + embedding + RRF Recall
        +-- LocalCache / UploadStaging
        +-- 本地开发与后续渐进式接入
```

权限边界保持不变：Token、账户、设备、空间和 namespace 权限由后端判断；Memory 只处理已授权的记忆请求，不自行扩大访问范围。

## 2. 生产 API 的通用请求头

所有需要身份的请求都应携带：

```http
Authorization: Bearer <access_token>
X-Device-ID: <stable-device-id>
X-Space-ID: <space-id>
X-Request-ID: <unique-request-id>
```

其中：

- `Authorization`：当前账户的访问令牌。
- `X-Device-ID`：每台设备使用稳定且不同的设备标识；不要在不同设备之间复用。
- `X-Space-ID`：访问的项目空间。未指定时使用服务端默认空间策略。
- `X-Request-ID`：建议每次请求生成新的值，便于排查和重试。

## 3. 生产云端记忆 API

### 3.1 读取记忆

```http
GET /api/memories?query=<可选关键词>&limit=50
```

PowerShell 示例：

```powershell
$headers = @{
  Authorization = "Bearer $token"
  "X-Device-ID" = "weekend-laptop"
  "X-Space-ID" = "space-project-world"
}

Invoke-RestMethod `
  -Method Get `
  -Uri "$baseUrl/api/memories?limit=50" `
  -Headers $headers
```

响应示例：

```json
{
  "count": 1,
  "memories": [
    {
      "id": "memory-id",
      "namespace": "user/account-id",
      "category": "preference",
      "content": "用户偏好先给结论，再给解释",
      "status": "active",
      "revision": 1,
      "created_at": 1750000000.0,
      "updated_at": 1750000000.0,
      "metadata": {}
    }
  ]
}
```

`query` 当前用于云端内容匹配；它不是新 `Memory/` 模块的 BM25、embedding 和 RRF 混合召回接口。

### 3.2 创建记忆

创建长期记忆必须明确确认：

```http
POST /api/memories
Content-Type: application/json
```

```json
{
  "confirmed": true,
  "scope": "personal",
  "category": "preference",
  "content": "用户偏好先给结论，再给解释"
}
```

可用 scope 由当前 Token 决定。常见值：

- `personal`：当前账户个人记忆。
- `shared`：当前空间共享记忆；实际写入需要空间写权限。
- `project`：项目/空间记忆。
- `owner`：仅 Owner 授权请求可用。

不要把 `confirmed` 省略或设置为 `false`。服务端会拒绝未明确确认的长期记忆写入。

### 3.3 更新记忆

更新必须携带最后一次读取到的 `revision`：

```http
PUT /api/memories/<memory_id>
Content-Type: application/json
```

```json
{
  "category": "preference",
  "content": "用户偏好使用简体中文，先给结论，再给解释",
  "expected_revision": 1
}
```

成功后 revision 加 1。若返回 `409`，说明另一台设备已经修改过该记忆：

1. 读取 `X-Memory-Revision`。
2. 重新 `GET /api/memories` 获取最新内容。
3. 根据最新内容重新决定是否合并。
4. 使用新的 `expected_revision` 重试。

不要在不读取最新内容的情况下强行覆盖。

### 3.4 删除记忆

删除是软删除，不会立即物理清除审计和同步记录：

```http
DELETE /api/memories/<memory_id>
Content-Type: application/json
```

```json
{
  "expected_revision": 2
}
```

成功响应：

```json
{
  "id": "memory-id",
  "status": "deleted",
  "revision": 3
}
```

### 3.5 读取同步事件

设备需要为每个账户/空间保存自己的游标：

```http
GET /api/sync/events?after=0&limit=200
```

响应：

```json
{
  "events": [
    {
      "event_id": 121,
      "aggregate_type": "memory",
      "aggregate_id": "memory-id",
      "event_type": "memory.updated",
      "payload_json": "{\"revision\":2}",
      "payload": {"revision": 2},
      "created_at": 1750000000.0
    }
  ],
  "next_cursor": 121
}
```

处理规则：

1. 请求时传入本地保存的 `after` 游标。
2. 收到事件后先保存或准备保存 `next_cursor`。
3. 事件只用于发现变化；事件 payload 不保证包含完整记忆正文。
4. 发现 `memory.created`、`memory.updated` 或 `memory.deleted` 后，重新请求 `/api/memories` 获取当前授权范围的完整快照。
5. 完整快照成功落地后，再提交新的游标。
6. 请求失败时不要推进游标，下一次从旧游标继续。

因此，设备 B 的基本同步循环是：

```text
读取本地 cursor
  -> GET /api/sync/events?after=cursor
  -> 发现变化
  -> GET /api/memories
  -> 更新本地 UI/只读缓存
  -> 完整快照成功后保存 next_cursor
```

## 4. 周末跨设备测试方案

### 4.1 设备身份

准备两个不同的设备 ID，例如：

```text
weekend-laptop
weekend-phone
```

两个设备可以使用同一账户测试个人记忆，也可以使用不同账户测试共享空间记忆。不要复制设备凭据或复用 `X-Device-ID`。

### 4.2 同账户跨设备测试

1. 设备 A 创建一条 `personal` 记忆。
2. 设备 B 使用同一账户读取 `/api/memories`。
3. 设备 B 修改该记忆。
4. 设备 A 从原 revision 发起更新，确认得到 `409`。
5. 设备 A 重新读取后，以新 revision 更新。
6. 两台设备分别读取 `/api/sync/events`，确认游标可以独立推进。

验收重点：

- 记忆内容最终一致。
- revision 单调递增。
- 旧 revision 不能覆盖新内容。
- 一台设备推进游标不影响另一台设备。

### 4.3 共享空间测试

1. Owner 或有写权限的成员创建 `shared` 记忆。
2. 另一名空间成员读取 `/api/memories`。
3. 另一名成员读取自己的 `/api/sync/events`。
4. 确认能看到对应事件和记忆。
5. Viewer 尝试更新，确认得到 `403`。
6. Owner 更新后，成员通过事件游标发现变化并重新拉取快照。

### 4.4 权限隔离测试

至少验证：

- 普通成员不能写入 `owner` scope。
- 普通成员的 `/api/memories` 不返回 Owner 私域原文。
- 不同空间的记忆不会互相出现在列表或同步事件中。
- 删除后普通列表不再返回该记忆，但历史事件仍可审计。
- Token 撤销后，旧 Token 不能继续读取或写入。

## 5. 聊天中的记忆开关

非流式聊天和流式聊天现在都支持：

```json
{
  "message": "继续上次的项目讨论",
  "use_memory": true
}
```

行为：

- 缺省 `use_memory`：按 `true` 处理，兼容旧客户端。
- `use_memory: true`：允许当前授权范围内的长期记忆进入本次请求上下文。
- `use_memory: false`：本次请求不召回长期记忆；不会影响云端已有记忆，也不会删除记忆。
- 非布尔值：返回 `400`。

Owner MCP 的 `idea_chat` 也遵循相同规则。

## 6. TRAE 长期记忆 MCP 工具

TRAE 里的 IDEA 通过 `IDEA-Owner-Agent` MCP 读写 Owner 私域长期记忆，云端为唯一权威源。

接入地址与凭据：

```text
MCP 地址：https://shiroha-rin.world/mcp/idea/mcp
请求头：  Authorization: Bearer <该设备专属 MCP 凭据>
```

凭据创建（`capability=idea` 才有写入与聊天工具；`capability=memory` 只有只读的 `memory_search` / `memory_get`）：

```powershell
Invoke-RestMethod -Method Post `
  -Uri 'https://shiroha-rin.world/api/platform/owner/mcp-credentials' `
  -Headers @{ Authorization = "Bearer $ownerToken" } `
  -ContentType 'application/json' `
  -Body '{"device_label":"TRAE-主力台式机","capability":"idea"}'
```

可用工具：

| 工具 | 作用 |
|---|---|
| `idea_memory_save(content, category)` | 写入一条记忆；内容完全相同则返回已有记录并带 `deduplicated: true` |
| `idea_memory_search(query, limit, category)` | 关键词检索；支持中文自然语言片段，按相关度和新鲜度排序 |
| `idea_memory_list(category, limit)` | 按更新时间列出最近记忆，用于了解近期上下文 |
| `idea_memory_update(memory_id, content, category)` | 修订已有记忆，revision 自动取当前值 |
| `idea_memory_delete(memory_id)` | 逻辑删除，保留审计与同步事件 |

检索说明：

- 支持中文 2-gram / 3-gram 与英文单词匹配，不需要精确子串。
- 排序权重：命中长度、整串命中加成、category 命中加成、180 天半衰期的新鲜度衰减。
- 检索只覆盖 Owner 私域命名空间 `owner/<owner_principal_id>`。
- 不使用 embedding，也不会调用 LLM。

会话级自动记忆由 TRAE 项目规则驱动，规则文件：

```text
d:\Project World\.trae\rules\project_rules.md
```

规则约定：任务开始先检索、用户要求时保存、会话结束归档结论。写入边界、category 规范和「不写凭据」等约束都在该文件中。

## 7. 新 `Memory/` 模块的本地使用方法

### 7.1 初始化

```python
from pathlib import Path

from Memory import MemoryService
from Memory.core import AuthorizedMemoryRequest, MemoryLayer
from Memory.storage import MemoryLayout

layout = MemoryLayout(Path(".idea-memory"))
memory = MemoryService(layout)
request = AuthorizedMemoryRequest(
    caller_id="backend-requester",
    container_id="owner-shiroha-nao",
    allowed_layers=frozenset({
        MemoryLayer.RECENT,
        MemoryLayer.FACT,
        MemoryLayer.REFLECTION,
        MemoryLayer.PERSONA,
    }),
    allow_raw_content=False,
    request_id="request-001",
    idempotency_key="memory-write-001",
)
```

`container_id` 是记忆容器，不是 Token，也不是权限主体。正式服务接入时必须由后端把已经授权的请求转换成 `AuthorizedMemoryRequest`，不能让 Memory 自己解析 Token。

### 7.2 写入 Recent

```python
memory.append_recent(request, {
    "id": "message-001",
    "role": "user",
    "content": "周末我要做跨设备记忆测试",
    "conversation_id": "conversation-001",
})
```

Recent 适合保存当前会话和近期上下文。它不等同于已经确认的长期事实。

### 7.3 写入 Fact

```python
fact_id = memory.add_fact(request, {
    "text": "用户计划在周末进行跨设备记忆测试",
    "source_message_id": "message-001",
    "source_conversation_id": "conversation-001",
    "status": "candidate",
    "metadata": {
        "source": "conversation",
    },
})
```

Fact 写入会进入本地文件视图和上传暂存。没有配置云端 backend 时，这只是本地开发行为，不代表已经写入生产云端。

### 7.4 从 Recent 学习

```python
learned_ids = memory.learn_recent(request, limit=10)
```

当前实现是基础学习流水线：从近期用户消息形成 Fact，并保留来源信息。更复杂的抽取、反思和人格晋升仍应由后续后台任务接管。

### 7.5 Recall

```python
results = memory.recall(
    request,
    "周末的记忆测试安排",
    limit=8,
)

for result in results:
    print(result.as_dict())

context = memory.render_recall(
    request,
    "周末的记忆测试安排",
    limit=8,
)
```

Recall 会先按请求允许的层过滤，再执行当前实现中的词法/向量辅助混合召回和结果预算限制。`allow_raw_content=False` 时，标记为原始内容的条目不会进入结果。

### 7.6 Reflection 和 Persona

Reflection 必须带来源事实和合成元数据：

```python
reflection_id = memory.synthesize_reflection(
    request,
    "用户正在验证跨设备记忆的一致性和恢复能力。",
    [fact_id],
    synthesis_version="idea-memory.v1",
    source_window="conversation-001",
)
```

只有状态为 `promoted` 的 Reflection 才能晋升到 Persona：

```python
persona_id = memory.promote_reflection_to_persona(
    request,
    reflection_id,
    entity="master",
)
```

不要在业务代码中直接把一次普通对话写成 Persona；稳定人格和关系知识必须经过证据与晋升策略。

## 8. 本地缓存和断联暂存

新 `Memory/` 模块的本地文件位于 `MemoryLayout` 指定的根目录下，每个角色/容器独立保存。

主要文件：

```text
<root>/<container_id>/local_cache.json
<root>/<container_id>/upload_staging.ndjson
```

### 8.1 读取缓存

读取缓存只保存云端记忆的近期副本：

- 不负责学习。
- 不负责合并、晋升、归档或权限判断。
- 根据云端 revision 替换。
- 长期不访问的条目可以本地淘汰。
- 本地淘汰不等于云端删除。

### 8.2 上传暂存

断联时的新写入进入 `upload_staging.ndjson`：

- 使用幂等键避免重复上传。
- 只有云端明确确认后才能 ack 并删除暂存记录。
- 上传失败会重试。
- 超过最大尝试次数会进入 dead letter。
- 暂存记录不应作为普通 Recall 数据源。

当前本地 `MemoryService` 没有自动连接生产 `/api/memories`；跨设备测试不要直接把本地 JSON 目录当成云端同步目录。

## 9. 当前明确限制

以下能力仍在后续接入计划中：

- 新 `MemoryService` 作为正式 API 的统一云端 facade。
- `PlatformStore` 到 `CloudMemoryBackend` 的正式适配器。
- 正式聊天链路写入 Recent、Journal 并触发后台学习。
- 云端 Facts、Reflections、Persona 的完整独立存储和查询。
- 生产环境 BM25 + embedding + RRF 召回替换当前基础查询。
- 负面关键词反驳和按年龄晋升机制的生产流水线接入。
- 跨设备客户端对新分层 Memory 协议的直接支持。

周末测试期间，使用第 3、4、5 节的正式 HTTP API；不要以 `Memory/` 目录中的本地文件是否变化来判断云端同步是否成功。

## 10. 推荐测试命令

在 `d:\Project World\Project-IDEA` 执行：

```powershell
python -m pytest tests\unit\test_platform_store.py -q
python -m pytest tests\integration\test_platform_auth.py -q

python -m pytest `
  tests\unit\test_memory_core.py `
  tests\unit\test_memory_storage.py `
  tests\unit\test_memory_views.py `
  tests\unit\test_memory_remaining.py `
  tests\unit\test_memory_recall_index.py `
  tests\unit\test_memory_service.py `
  tests\unit\test_memory_learning_pipeline.py `
  tests\unit\test_memory_sync.py `
  tests\unit\test_memory_migration.py `
  -q
```

专项验证 `use_memory`：

```powershell
python -m pytest tests\integration\test_platform_auth.py -q -k "chat_use_memory_false_skips_memory_and_validates_type or owner_agent_mcp_is_isolated_and_sessions_continue"
```

如果仓库公共 pytest fixture 导致无关导入错误，可以对纯 Memory 单元测试使用 `--noconftest`，但平台集成测试应优先保留完整 fixture 环境并单独记录基线失败。
