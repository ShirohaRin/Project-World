# Core 爆发运行时调试记录

状态：`[OPEN]`
会话：`core-burst-runtime`

## 症状
Core 不进入不稳状态，也不触发爆发。

## 可证伪假设
1. 动画帧在 `updateCorePoint` 或其后续逻辑抛出运行时异常。
2. `triggerCorePulse` 定时器未启动或 `tick` 未执行。
3. `releaseAge`、`corePulseDuration` 或状态变量被错误覆盖，导致状态机无法跨过释放阶段。
4. 线上页面加载的脚本版本不是当前工作区版本。

## 观测计划
- 记录脚本版本和初始化。
- 记录 `triggerCorePulse` 启动、首帧、预热、不稳、释放、结束。
- 记录 `updateCorePoint` 进入次数及异常。
- 记录未捕获异常。

## 结论
待收集运行时证据后填写。
