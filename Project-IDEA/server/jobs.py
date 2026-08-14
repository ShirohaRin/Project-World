"""后台作业调度器（对齐 dsh jobs/schedule）。

定时扫描到期作业（scheduled_jobs 表），用创建者（Owner）上下文执行工具调用并回写结果。
高危工具仍走审批/授权链：需要审批的作业会在结果中记录 failed 而非越权执行。
"""

import asyncio
import json
import logging

from platform_auth import Principal, RequestContext
from tool_runtime.permissions import ExecutionContext

logger = logging.getLogger("idea.jobs")


class JobScheduler:
    """后台调度循环：到期作业 → 工具执行 → 结果回写 + 下次运行时间推进。"""

    def __init__(self, platform_store, tool_registry, scan_interval: float = 10.0):
        self.store = platform_store
        self.registry = tool_registry
        self.scan_interval = scan_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="idea-job-scheduler")
        logger.info("job scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("job scheduler stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("job scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.scan_interval)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        for job in self.store.due_scheduled_jobs():
            await self._run_job(job)

    async def _run_job(self, job: dict) -> None:
        job_id, tool_name = job["job_id"], job["tool_name"]
        try:
            args = json.loads(job["args_json"] or "{}")
            context = ExecutionContext(
                request_context=RequestContext(
                    f"job-{job_id}",
                    Principal(job["account_id"], job["account_id"], "owner", "token-job"),
                    None,
                    job["space_id"],
                ),
                agent_id=job["agent_id"] or "idea",
                is_owner=True,
            )
            result = await self.registry.execute(tool_name, args, context)
            status = "success" if result.success else "failed"
            output = result.output[:1000] if result.output else ""
            if not result.success:
                output = f"[{result.metadata.get('decision', 'deny')}] {output}"
            self.store.update_scheduled_job_run(job_id, status, output, job["next_run_at"] + float(job["interval_seconds"]))
            logger.info("scheduled job %s (%s) -> %s", job_id, tool_name, status)
        except Exception as error:
            logger.warning("scheduled job %s failed: %s", job_id, error)
            self.store.update_scheduled_job_run(job_id, "error", str(error)[:1000], job["next_run_at"] + float(job["interval_seconds"]))
