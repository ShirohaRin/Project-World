# IDEA Harness 基准评测（BENCHMARK）

对齐 DeepSeek Harness 的基准评测体系：用可复现的本地指标跟踪 Harness 服务端性能，
为每次架构改动提供回归基准。

## 运行

```powershell
cd Project-IDEA/server
python benchmark.py            # Markdown 报告
python benchmark.py --json     # JSON 输出
```

所有指标均本地测量、**无网络依赖**（不调用 LLM），可在 CI 或开发机直接运行。

## 指标说明

| 指标 | 含义 |
|---|---|
| `tool_read_file` | read_file 单次执行延迟（含磁盘 IO + 策略决策 + 审计） |
| `tool_edit_file` | edit_file 单次执行延迟（Owner 上下文，含备份 + diff） |
| `approval_resolve_reuse` | 高危工具审批解析 + 指纹复用延迟 |
| `policy_decision` | ToolPolicy 单次决策延迟 |
| `sandbox_wrap` | 沙箱命令包装单次延迟 |
| `sandbox_detect_once` | 沙箱后端探测单次延迟（bwrap/受限令牌） |
| `derive_messages_tail_10_of_100` | 事件溯源下从 100 条消息派生最近 10 条延迟 |
| `snippets_batch_query` | 跨会话摘要批量窗口查询延迟 |
| `snippets_old_n_plus_1` | 跨会话摘要旧实现（逐会话查询）延迟——优化基线对比 |

## 参考基线（2026-08，本机 Windows）

```
tool_read_file                    12.91 ms
tool_edit_file                    31.24 ms
approval_resolve_reuse            21.30 ms
policy_decision                    0.001 ms
sandbox_wrap                       0.0003 ms
sandbox_detect_once                0.03 ms
derive_messages_tail_10_of_100     2.13 ms
snippets_batch_query               1.25 ms
snippets_old_n_plus_1              9.60 ms   ← 批量窗口查询较旧 N+1 提升约 7.7 倍
```

## 新增指标指南

1. 在 `server/benchmark.py` 的 `main()` 中新增测量段，返回 dict 键。
2. 使用 `_measure(fn, rounds)` 得到 p50/mean/p95；`rounds` 足够大以保证稳定。
3. 指标必须可离线复现；涉及外部服务的指标放到 `tests/e2e` 而非基准。
4. 提交架构/性能改动时更新本文件的参考基线。
