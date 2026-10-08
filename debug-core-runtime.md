# Debug Session: core-runtime
- **Status**: [OPEN]
- **Issue**: Core 不再爆发、矩形不转动、原点颜色变为天蓝色
- **Debug Server**: http://127.0.0.1:7777/event（已启动并用于本地证据采集）
- **Log File**: .dbg/trae-debug-log-core-runtime.ndjson

## Reproduction Steps
1. 打开 Project World 首页。
2. 等待 Core 的随机爆发周期。
3. 观察 Core 原点、爆发实体和外围矩形旋转。

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | 新增喷发运动逻辑存在运行时异常，阻断后续 Core 状态机 | High | Low | Pending |
| B | 动画帧循环初始化或函数引用顺序导致脚本中断 | High | Low | Pending |
| C | 新样式覆盖原点的红色光晕 | High | Low | Pending |
| D | 实体生成/清理逻辑使爆发不可见 | Medium | Medium | Pending |
| E | 线上 HTML、CSS、JS 版本不一致 | Medium | Low | Pending |

## Log Evidence
- 线上浏览器控制台确认：`ReferenceError: particleVectors is not defined`，位置为 `script.js:161` 的旧固定粒子循环。
- 同一异常发生在 `updateCorePoint()` 的爆发 tick 内，因此会中断后续 Core 脉冲状态机；矩形旋转和爆发都因此停止。
- 修复后重新加载线上页面：`typeof particleVectors === "undefined"`，`typeof updateBurstBodies === "function"`。
- 修复后运行时状态确认：矩形 `transform` 持续为旋转角度，Core 外壳恢复动态 polygon。

## Verification Conclusion
根因确认是旧的 `particleVectors` 引用未随随机喷发系统迁移而删除。已移除旧循环，恢复新的 `updateBurstBodies()` 路径，并将原点基础色改回暖红粉色。
