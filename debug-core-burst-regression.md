# Core 不爆发运行时调试记录

状态：[OPEN]
时间：2026-08-26
现象：用户反馈 Core 又不爆发。

## 可验证假设

1. `triggerCorePulse()` 定时器没有注册或被页面生命周期打断。
2. Core 动画循环在爆发前出现运行时异常。
3. 新外核视觉代码影响了 Core 状态更新。
4. 爆发周期过长，观察窗口内尚未到达释放阶段。
5. 当前页面不是首页，页面中不存在 Core。

## 证据记录

- 2026-08-26：线上首页控制台在 `updateCorePoint()` 报错：`ReferenceError: coreNextShell is not defined`，位置为 `script.js:257`。该异常发生在初始化调用期间，阻断了后续 Core 爆发定时器注册。

## 修复记录

- 已补回 `coreNextShell` 的 DOM 引用，保持现有独立新外核逻辑不变。
