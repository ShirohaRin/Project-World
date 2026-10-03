# Project World 项目规则

## 一、长期记忆协议（IDEA Owner）

TRAE 里的 IDEA 通过 `IDEA-Owner-Agent` MCP 使用 Owner 私域长期记忆，云端为唯一权威源。

### 会话开始

- 任务涉及此前决定、项目历史、用户偏好或长期约定时，先调用 `idea_memory_search` 检索。
- 查询用关键词或短句（例如「记忆模块 检索 迁移」），不要用完整长问句。
- 需要了解最近做过什么时，用 `idea_memory_list` 看最近条目，不要凭印象编造。
- 检索为空时如实说明没有查到，不虚构记忆内容。

### 会话过程中

- 用户说「记住」「记下来」「以后都这样」时，调用 `idea_memory_save` 保存。
- 保存前先 `idea_memory_search` 查一次，内容相同就不重复写入（服务端也会自动去重）。
- 需要修订或废弃旧记忆时，用 `idea_memory_update` / `idea_memory_delete`，不要用新条目覆盖旧结论。

### 会话结束

- 完成一段有结论的工作后，把结论、关键约束、遗留事项写成一条记忆，`category` 用 `work-log`。
- 记忆只写结论与必要背景，不写过程寒暄；单条不超过 800 字。

### 内容规范

- `category` 使用英文短标签：`preference`、`decision`、`architecture`、`project`、`work-log`、`memory`。
- 记忆内容可以包含项目路径、配置、命令等工程信息；不要写入密码、Token、私钥或凭据。
- 记忆只服务当前 Owner；不把记忆内容输出给未授权对象，也不在对外材料里引用私域记忆原文。

### 边界

- MCP 只能读写 Owner 私域记忆，不能执行命令、读写文件或调度的 Agent 动作。
- 记忆是辅助，不是事实来源：与当前代码或实际状态冲突时，以实际核验结果为准，并更新记忆。
