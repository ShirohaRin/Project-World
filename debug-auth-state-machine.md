# Debug Session: auth-state-machine
- **Status**: [OPEN]
- **Issue**: 登录、恢复与退出使用多个相互覆盖的状态，导致会话表现不一致。
- **Scope**: Electron 主进程会话、preload IPC、React 认证界面和平台注销 API。

## Hypotheses & Verification
| ID | Hypothesis | Evidence |
|---|---|---|
| A | React 的多个认证状态会产生无法到达或自相矛盾的界面状态 | Pending |
| B | 自动刷新与退出共用请求路径，导致会话撤销对象失效 | Pending |
| C | 并发 401 刷新使用已轮换的 refresh token，造成随机失效 | Pending |
| D | 退出后的异步请求可以回写旧会话状态 | Pending |

## Log Evidence
等待现有链路盘点和运行验证。
