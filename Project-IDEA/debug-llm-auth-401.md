# 调试记录：云端 LLM 401

- 会话：`llm-auth-401`
- 状态：`[OPEN]`
- 现象：SRH 端已进入在线聊天流程，但服务端返回“LLM 调用失败 (401)”。
- 期望：Owner/SRH 端调用云端 IDEA 时获得模型回复，而非鉴权失败。

## 待验证假设

1. 运行环境未加载 `DOUBAO_API_KEY` 或 `GLM_API_KEY`。
2. 已加载的密钥无效、过期，或不适配当前模型供应商。
3. 服务重启后仍在使用旧的环境变量快照。
4. 当前请求被路由到没有有效凭据的模型提供方。

## 已收集证据

- `A / LLMClient.chat.config`：当前提供方为 `custom`，模型为 `gpt-5.6-terra`，基础 URL 与 `CUSTOM_API_KEY` 均存在。
- `B / LLMClient.chat.response`：上游响应 `401`，响应特征包含 `invalid`，不包含 `expired` 或 `rate_limit`。
- `C / LLMClient.chat.non_200`：确认非成功状态为 `401`。
- 服务器本机最小固定消息复现同一结果，因此与 SRH 客户端、Owner 身份、会话或用户消息无关。

## 结论

假设 1、3、4 被排除。假设 2 确认：当前 `CUSTOM_API_KEY` 对 `CUSTOM_API_BASE_URL` 无效、已失效，或不属于该端点。需要在私有运行环境中更新有效的上游模型凭据，或切换到拥有有效凭据的受支持提供方后重启并复测。
