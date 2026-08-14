"""IDEA Harness 基准评测脚本（对齐 dsh BENCHMARK 体系）。

测本地、无网络依赖的性能指标：
- 工具执行延迟（read_file / edit_file / 审批决策 / 授权复用）
- 会话消息派生延迟（事件溯源）
- 跨会话摘要：批量窗口查询 vs 逐会话 N+1
- 沙箱命令包装耗时

运行：
    python benchmark.py                 # 打印 markdown 报告
    python benchmark.py --json          # 输出 JSON
"""

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path


def _percentile(samples: list[float], ratio: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(len(ordered) * ratio))
    return ordered[index]


def _measure(fn, rounds: int) -> dict:
    samples = []
    for _ in range(rounds):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)  # ms
    return {
        "rounds": rounds,
        "mean_ms": round(statistics.mean(samples), 3),
        "p50_ms": round(_percentile(samples, 0.5), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
    }


def main() -> dict:
    from platform_auth import PlatformStore, Principal, RequestContext
    from tool_runtime.permissions import ExecutionContext
    from tool_runtime.registry import ToolRegistry

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "bench.txt").write_text("line one\nline two\nline three\n", encoding="utf-8")
    store = PlatformStore(str(root / "bench.db"))
    registry = ToolRegistry(workspace=str(root), allowed_dirs=[str(root)], audit_store=store)
    owner = ExecutionContext(
        RequestContext("req-bench", Principal("p-owner", "acct-owner", "owner", "t-owner"), "dev", "space-1"),
        "idea",
        is_owner=True,
    )

    results: dict = {}

    async def _read() -> None:
        for _ in range(30):
            await registry.execute("read_file", {"file_path": str(root / "bench.txt")}, None)

    async def _edit() -> None:
        for _ in range(30):
            await registry.execute("edit_file", {"file_path": str(root / "bench.txt"), "old_str": "line one", "new_str": "line one"}, owner)

    async def _approval_path() -> None:
        for _ in range(30):
            await registry.execute("run_command", {"command": "echo bench", "working_dir": "."}, owner)

    def _policy_decision() -> None:
        for _ in range(2000):
            registry.policy.decide("run_command", owner)

    def _sandbox_wrap() -> None:
        policy = registry.sandbox.resolve()
        for _ in range(2000):
            registry.sandbox.wrap_command(["bash", "-c", "echo x"], policy)

    def _sandbox_detect() -> None:
        for _ in range(20):
            from tool_runtime.sandbox import detect_backend

            detect_backend(str(root))

    results["tool_read_file"] = _measure(lambda: asyncio.run(_read()), 1)["p50_ms"] / 30
    results["tool_edit_file"] = _measure(lambda: asyncio.run(_edit()), 1)["p50_ms"] / 30
    results["approval_resolve_reuse"] = _measure(lambda: asyncio.run(_approval_path()), 1)["p50_ms"] / 30
    results["policy_decision"] = _measure(_policy_decision, 3)["p50_ms"] / 2000
    results["sandbox_wrap"] = _measure(_sandbox_wrap, 3)["p50_ms"] / 2000
    results["sandbox_detect_once"] = _measure(_sandbox_detect, 3)["p50_ms"] / 20

    # 事件溯源：追加 100 条后派生最近 10 条
    cid = store.create_conversation("acct-owner", "space-1", "idea")["conversation_id"]
    for index in range(100):
        store.append_message("acct-owner", "space-1", cid, "user" if index % 2 == 0 else "assistant", f"msg-{index}")

    def _derive_tail() -> None:
        store.list_messages("acct-owner", "space-1", cid, limit=10)

    results["derive_messages_tail_10_of_100"] = _measure(_derive_tail, 50)["p50_ms"]

    # 跨会话摘要：批量 vs N+1
    for index in range(3):
        other = store.create_conversation("acct-owner", "space-1", "idea")["conversation_id"]
        for j in range(6):
            store.append_message("acct-owner", "space-1", other, "user", f"other-{index}-{j}")

    def _batch_snippets() -> None:
        store.recent_message_snippets("acct-owner", "space-1")

    def _n_plus_one_snippets() -> None:
        for conversation in store.list_conversations("acct-owner", "space-1"):
            store.list_messages("acct-owner", "space-1", conversation["conversation_id"], limit=2)

    results["snippets_batch_query"] = _measure(_batch_snippets, 50)["p50_ms"]
    results["snippets_old_n_plus_1"] = _measure(_n_plus_one_snippets, 50)["p50_ms"]

    tmp.cleanup()
    return results


def render_markdown(results: dict) -> str:
    lines = ["# IDEA Harness 基准报告", "", "| 指标 | 耗时 (ms) |", "|---|---|"]
    for name, value in results.items():
        lines.append(f"| {name} | {value:.4f} |")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IDEA Harness benchmark")
    parser.add_argument("--json", action="store_true", help="output JSON")
    args = parser.parse_args()
    report = main()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
