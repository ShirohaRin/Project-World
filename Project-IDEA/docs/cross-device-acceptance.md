# IDEA 跨设备验收记录

## 自动化验收

服务端测试使用两个独立的 `X-Device-ID` 模拟设备 A 和设备 B，数据库使用每次测试新建的临时 SQLite 文件，不接触生产记忆或生产数据库。

执行全部服务端回归：

```powershell
cd "D:\Project World\Project-IDEA\server"
python -m unittest -v test_platform_auth.py
```

只执行双设备验收：

```powershell
cd "D:\Project World\Project-IDEA\server"
python -m unittest -v test_platform_auth.PlatformApiTests.test_two_approved_owner_devices_sync_memory_and_enforce_revocation
```

双设备自动化验收必须验证以下结果：

1. 设备 A 登录后由 Owner 引导身份批准，并重新登录进入 `owner_idea`。
2. 设备 B 首次登录只能进入 `idea_assistant`，不能直接使用 `idea`。
3. 设备 A 批准设备 B，设备 B 重新登录后进入 `owner_idea`。
4. 设备 A 创建 Owner 记忆，设备 B 能通过同步事件游标收到 `memory.created`，并能查询到该记忆。
5. 设备 B 将记忆从 revision 1 更新到 revision 2。
6. 设备 A 使用过期 revision 1 更新时收到 `409`，响应头 `X-Memory-Revision` 为 `2`。
7. 设备 A 撤销设备 B 后，设备 B 的 Access Token 请求立即返回 `401`。
8. 设备 B 的 Refresh Token 请求也立即返回 `401`。

测试文件：

- [test_platform_auth.py](../server/test_platform_auth.py)

## 人工验收

人工验收需要两个不同的客户端设备或两个独立客户端数据目录。设备标识必须不同，不能复制同一份 Electron `userData` 目录。

### 1. 首次登录与批准

1. 在设备 A 使用私有版 IDEA 登录 Owner 账号。
2. 在设备 B 使用同一 Owner 账号登录。
3. 确认设备 B 首次登录只显示普通 Assistant 路由，不显示 Owner 私域功能。
4. 在设备 A 的私有设备控制面板确认设备 B 为 `pending`。
5. 在设备 A 批准设备 B。
6. 在设备 B 登出并重新登录。
7. 确认设备 B 重新登录后进入私有 IDEA 路由。

### 2. 记忆同步与冲突

1. 在设备 A 显式保存一条带确认的 Owner 记忆，例如“跨设备验收标记 A”。
2. 在设备 B 刷新同步或重新打开会话。
3. 确认设备 B 能查询到该记忆，且访客版不显示这条 Owner 记忆。
4. 在设备 B 修改这条记忆并保存。
5. 在设备 A 保持修改前的旧版本，再尝试保存同一条记忆。
6. 确认设备 A 收到冲突提示，而不是静默覆盖设备 B 的内容。

### 3. 撤销与即时失效

1. 在设备 A 撤销设备 B。
2. 在设备 B 刷新私有记忆、读取个人信息或发起新的私有请求。
3. 确认设备 B 的请求被拒绝并回到登录状态。
4. 确认设备 B 即使未等待 Access Token 自然过期，也无法使用原登录会话刷新出新令牌。
5. 设备 B 重新登录后，应重新回到 `pending`，不能因历史批准记录自动恢复私域权限。

## 边界

- 该验收覆盖服务端权限、记忆命名空间、同步事件、并发 revision 和令牌撤销。
- 该验收不声称完成工作区文件同步、离线队列、屏幕控制、OCR 或正式手机号认证。
- 测试验证码只在开发模式使用，不能作为生产认证方案。
- Owner 记忆不进入 GitHub，也不应通过复制生产数据库参与测试。
