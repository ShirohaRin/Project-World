# Debug Session: work-neko-unavailable
- **Status**: [OPEN]
- **Issue**: Work 模式中的内嵌 N.E.K.O 未进入就绪状态，云端运行时快照请求返回 Not Found。
- **Debug Server**: http://127.0.0.1:7777/event
- **Log File**: .dbg/trae-debug-log-work-neko-unavailable.ndjson

## Reproduction Steps
1. 启动 Owner 客户端并登录。
2. 打开 Work 页面。
3. 观察内嵌 N.E.K.O 面板与底部状态栏。

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | `neko:runtime` IPC 未进入主进程处理器 | Med | Low | Pending |
| B | 启动资源解析或子进程创建前发生异常 | High | Low | Pending |
| C | 子进程启动后立即退出且未记录完整输出 | Med | Low | Pending |
| D | 启动器选定端口未被解析，健康检查探测错误端口 | Med | Low | Pending |
| E | 云端 Runtime 快照接口与当前服务版本不匹配 | High | Low | Pending |

## Log Evidence
等待复现日志。

## Verification Conclusion
等待 pre-fix 运行证据。
