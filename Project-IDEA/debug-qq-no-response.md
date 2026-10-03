# Debug QQ no response

状态：[OPEN]
时间：2026-09-03（Asia/Shanghai）

## 症状
- NapCat 已重新登录。
- 用户发送 QQ 消息后没有收到回复。

## 可证伪假设
1. NapCat 未将消息转发到 IDEA。
2. OneBot 回调地址或密钥不一致。
3. IDEA Agent 或大模型执行失败。
4. IDEA 调用 NapCat 发送回复失败。
5. 入站 QQ 白名单不匹配。

## 证据记录
- 待收集：NapCat 入站/上报日志、IDEA Server OneBot 请求日志、Agent 异常、发送接口结果。

## 修复记录
- 尚未修改业务逻辑。
