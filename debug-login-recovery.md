# Debug Session: login-recovery
- **Status**: [OPEN]
- **Issue**: 退出登录后界面停留在“正在恢复登录会话…”，无法进入登录表单。
- **Debug Server**: http://127.0.0.1:7777/event
- **Log File**: .dbg/trae-debug-log-login-recovery.ndjson

## Hypotheses & Verification
| ID | Hypothesis | Evidence |
|----|------------|----------|
| A | 退出后的初始化仍尝试恢复旧会话 | Pending |
| B | 会话恢复请求无超时，网络层一直等待 | Pending |
| C | 未登录状态已写入，但 `authReady`/恢复状态没有结束 | Pending |
| D | 登录表单被恢复遮罩条件错误隐藏 | Pending |

## Log Evidence
等待复现日志。
