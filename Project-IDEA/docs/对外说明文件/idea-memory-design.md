# Project IDEA Memory 设计与迁移方案

> 状态：第一版设计基线
> 目标：将 NEKO 的分层记忆结构与核心算法迁移为云端、可自维护的 IDEA Memory 功能模块。

## 1. 定位与目标

Memory 是 Project IDEA 五大核心模块之一，负责完整的短期记忆、长期记忆、记忆学习、主动回忆和自维护能力。它不负责 Token 解析、用户权限识别、项目管理、Agent 管理或知识库管理。

最终体验目标是：当前上下文只承载眼前任务和任务情景；超出上下文的历史、偏好、关系、经验与长期脉络由 Memory 按需召回，使 IDEA 能够持续理解用户、陪伴用户并在长期使用中成长。

## 2. 职责边界

### Memory 负责

- Recent 短期记忆和上下文压缩
- Journal 原始对话时间索引
- Facts 原子事实形成、去重和更新
- Reflections 跨事实反思合成
- Persona 稳定人格和关系知识
- Evidence 强化、质疑和纠正
- BM25 + embedding + RRF 混合召回
- 记忆生命周期、归档和恢复
- 后台异步学习与自维护
- 云端权威存储
- 本地读取缓存和断联上传暂存协议

### 外部后端负责

- Token 解析与认证
- 用户类型和授权决策
- 项目、Workspace、Agent、知识库管理
- 服务开放策略
- 向 Memory 传递已授权的调用上下文

Memory 只执行请求中给定的授权范围，不推导或扩大权限；同时提供请求格式、资源归属、版本、幂等和错误隔离等基础安全边界。

## 3. 记忆分层

```text
Journal       原始消息与时间索引
Recent        当前会话及近期会话、摘要备忘录
Fact          从对话中提取的原子事实
Reflection    从多条事实合成的高层判断
Persona       稳定的用户、IDEA、关系和协作知识
Evidence      强化、质疑、确认、否定等证据
Archive       不再活跃但可追溯的历史记忆
```

角色是记忆主体/容器，而不是权限主体。未来世界模拟可为多个独立角色创建独立记忆容器；第一阶段只迁移容器能力，模拟运行时暂时空置。

## 4. 记忆生命周期

```text
candidate -> confirmed -> promoted
                         -> merged / superseded
candidate/confirmed -> disputed -> archived
任何状态 -> deleted（逻辑删除，保留审计）
```

NEKO 的负面关键词反驳和按年龄晋升机制第一阶段照搬。它们属于可替换的策略层，后续可调整权重、阈值和模型，不重置底层记忆。

## 5. 核心流水线

### 写入与学习

```text
对话完成
  -> Journal
  -> Recent
  -> 异步任务
  -> Fact 提取与去重
  -> Reflection 合成
  -> Evidence 更新
  -> Persona 晋升/合并
  -> 索引更新
```

前台写入必须快速返回；摘要、事实提取、反思、证据审阅、人格晋升、归档和 embedding 均由后台任务处理。任务至少一次投递，处理器必须幂等，单个任务失败不得导致全局重置。

### 自动上下文

```text
当前任务
  + Recent
  + 相关 Persona
  + 相关 Reflection
  + 必要的长期脉络
```

不会将全部历史或事实库倾倒进模型上下文，所有注入统一受 token 预算限制。

### 主动召回

```text
已授权范围
  -> 生命周期过滤
  -> 时间过滤
  -> BM25
  -> embedding
  -> RRF
  -> 重要性/新鲜度/置信度调整
  -> token 截断
```

在线召回不额外调用 LLM 重排；LLM 重排只用于后台证据和学习任务。

## 6. 云端数据原则

云端是唯一权威源，Memory 的真实状态、生命周期、证据、学习结果和索引均在云端维护。数据库、对象存储和任务队列替代 NEKO 的本地 JSON、SQLite、文件锁和 loopback server。

资源标识用于定位，不用于承担身份和权限。任何单一 ID、索引、任务或派生视图损坏，只能影响对应资源或重建过程，禁止触发全局清空或记忆重置。

## 7. 本地故障缓存

本地仅允许存在两类数据：

1. **读取缓存**：保存近期高频使用的云端记忆，只读、不参与学习、不改变生命周期，按云端 revision 替换，长期未使用自动淘汰。
2. **上传暂存**：断联时保存尚未被云端确认的新增记忆请求，使用 idempotency key 重试；收到云端明确确认后才清除。

本地不执行事实提取、反思、人格晋升、证据衰减、权限判断或向量索引维护。暂存内容必须加密并与普通召回隔离。

## 8. 接口方向

Memory 需要提供以下功能接口，具体协议后续确定：

- `recent.append / recent.context / recent.compress`
- `journal.append / journal.range`
- `facts.create / facts.list / facts.update`
- `reflections.synthesize / reflections.list`
- `persona.list / persona.update`
- `evidence.append / evidence.recalculate`
- `recall.context / recall.hybrid / recall.temporal`
- `maintenance.run / maintenance.status`
- `sync.read / sync.write / sync.ack`

每次调用由外部后端附带已授权上下文、目标记忆容器、请求 ID 和幂等键。Memory 返回结构化结果、版本信息和可重试错误，不返回权限推导结果。

## 9. 第一阶段开发范围

### 实现

- 云端 Memory 核心存储抽象
- Journal、Recent、Fact、Reflection、Persona 的基础模型
- 记忆生命周期与 Evidence 事件
- 幂等写入和 revision 更新
- 统一 recall 接口骨架
- 后台任务接口骨架
- 本地读取缓存和上传暂存接口骨架
- 单元测试与故障恢复测试

### 暂不实现

- 世界模拟运行时
- 角色交互和情景编排
- 传统后端权限系统
- 知识库功能
- Agent 管理
- 跨用户原始记忆共享

## 10. 验收原则

- 当前上下文不再承担长期记忆保存职责
- 记忆可以跨会话形成连续理解
- 事实、反思、人格彼此分层且可追溯
- 召回先遵守外部授权范围，再执行检索
- 后台学习失败不阻塞对话
- 单个资源故障不造成全局重置
- 断联新记忆不因网络或接口失败丢失
- 本地缓存不发展为第二套记忆系统
- 算法策略可替换，底层记忆无需重置
