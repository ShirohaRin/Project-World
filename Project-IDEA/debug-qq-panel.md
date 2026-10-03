# Debug QQ 入站与网页面板

状态：[OPEN]
时间：2026-09-03（Asia/Shanghai）

## 症状
- QQ 发送课程查询后没有收到回复。
- 网页面板不可见。

## 可证伪假设
1. NapCat 未将入站事件转发到 IDEA Server。
2. OneBot secret 或回调地址不一致，请求被拒绝。
3. Agent 或大模型调用失败，入站请求到达但未生成回复。
4. IDEA Server 的静态目录或面板挂载路径异常。
5. 服务健康检查正常，但 OneBot endpoint 仍有运行时异常。

## 证据记录
- 待收集：idea-server journal、NapCat 容器状态与日志、health、静态面板入口、OneBot 回调结果。

## 修复记录
- 尚未修改业务逻辑。
