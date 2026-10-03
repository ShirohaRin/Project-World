# Debug Session: logout-recovery
- **Status**: [OPEN]
- **Issue**: IDEA Assistant SRH 的退出登录入口失效，过期会话无法回到可重新登录状态。
- **Debug Server**: http://127.0.0.1:7777/event
- **Log File**: .dbg/trae-debug-log-logout-recovery.ndjson

## Reproduction Steps
1. 启动已登录但会话失效的 Owner 客户端。
2. 在界面中执行退出登录。
3. 观察是否回到登录入口，以及本地会话是否清除。

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | 渲染进程的退出登录事件未触发 | Med | Low | Pending |
| B | `service:logout` IPC 被拒绝或执行异常 | Med | Low | Pending |
| C | 本地安全存储会话文件未被正确重写 | High | Low | Pending |
| D | 本地会话已清除，但界面未刷新认证状态 | High | Low | Pending |
| E | 远端登出失败影响了本地登出完成 | Med | Low | Pending |

## Log Evidence
等待复现日志。

## Verification Conclusion
等待 pre-fix 运行证据。
