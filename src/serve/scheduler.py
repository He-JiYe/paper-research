"""调度服务：内置于 serve 的定时抓取调度器

特性：
- 按 config.yaml 的 scheduler.fetch_time 每日定时抓取
- 时区统一用系统本地时间（与邮件/Web 的"今日"同口径，见 config.settings.today_str）
- catch_up_on_start：serve 启动时若今天已过抓取时间但尚未成功抓取，立即补抓一次
- 补抓 = 一次正式的今日抓取，抓取后同样发今日结果邮件（不区分定时/补抓）
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class FetchScheduler:
    """内置于 serve 的定时抓取调度器。"""

    def __init__(self, settings, on_fetch_done: Callable[[dict], None] | None = None):
        self.enabled = settings.scheduler.enabled
        self.fetch_time = settings.scheduler.fetch_time
        self.catch_up_on_start = settings.scheduler.catch_up_on_start
        self._settings = settings
        self._on_fetch_done = on_fetch_done  # 抓取完成后回调（serve 注入 publish_sse）
        self._task: asyncio.Task | None = None

    def _now(self) -> datetime:
        return datetime.now()

    async def start(self):
        if not self.enabled:
            logger.info("调度器未启用")
            return

        logger.info("调度器已启动，抓取时间: %s", self.fetch_time)

        # 启动补抓：笔记本/台式机场景下，开机时间可能晚于 fetch_time。
        # 补抓即补今日抓取，抓取后同样发今日邮件。
        if self.catch_up_on_start and await self._should_catch_up():
            logger.info("今日抓取尚未执行且已过计划时间，启动补抓（发今日邮件）")
            await self._run_fetch()

        while True:
            now = self._now()
            target = self._next_run(now)
            wait_seconds = max((target - now).total_seconds(), 1)
            logger.info("距离下次抓取还有 %.0f 秒", wait_seconds)
            await asyncio.sleep(wait_seconds)
            # fetch_time 之后已成功抓取过（如用户在页面手动抓取）则跳过，
            # 避免重复抓取以及由此带来的重复邮件
            if await self._fetched_since_fetch_time():
                logger.info("今日 fetch_time 后已有成功抓取记录，跳过本次定时抓取")
                continue
            await self._run_fetch()

    async def _fetched_since_fetch_time(self) -> bool:
        """今天 fetch_time 之后（含）是否已有成功抓取记录（含手动抓取）。

        fetch_time 之前的手动抓取不算——否则会抑制 fetch_time 的定时/补抓。
        直接拿 ``run_time`` 与「今日 fetch_time」比较（fetch_time 已由配置校验为 HH:MM）。
        SQLite 读（PaperDB 构造含建表 DDL）放线程执行，不阻塞 serve 事件循环。
        """
        try:
            from src.db import PaperDB

            today = self._now().date().isoformat()
            return await asyncio.to_thread(
                PaperDB().has_successful_since, f"{today} {self.fetch_time}:00"
            )
        except Exception as e:
            logger.warning("抓取记录检查失败（按未抓取处理）: %s", e)
            return False

    def _parse_fetch_time(self) -> tuple[int, int]:
        """解析 fetch_time（HH:MM）。

        格式与范围已由 ``SchedulerConfig._validate_fetch_time`` 在配置加载时严格校验，
        此处不会遇到非法值，直接拆解返回。
        """
        hour, minute = map(int, self.fetch_time.split(":"))
        return hour, minute

    async def _should_catch_up(self) -> bool:
        """判断是否需要补抓：今天已过计划时间 且 今天 fetch_time 之后没有成功抓取记录。"""
        now = self._now()
        hour, minute = self._parse_fetch_time()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < target:
            return False  # 今天还没到点，正常等待即可
        return not await self._fetched_since_fetch_time()

    def _next_run(self, now: datetime) -> datetime:
        hour, minute = self._parse_fetch_time()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target < now:
            target += timedelta(days=1)
        return target

    async def _run_fetch(self):
        """执行一次定时抓取（补抓与定时统一路径：有新论文即发今日邮件）。

        完成后经 ``on_fetch_done`` 发布 SSE 事件（前端据此自动刷新，取代 60s 轮询）。
        """
        logger.info("调度触发：开始抓取")
        status = "failed"
        papers_new = 0
        try:
            from src.config.settings import get_active_keywords
            from src.db import PaperDB
            from src.pipeline.fetch import run_fetch_pipeline

            result = await run_fetch_pipeline(
                self._settings,
                get_active_keywords(self._settings),
                0,  # 0 = 使用各源配置的 max_results
                mode="incremental",
            )
            logger.info("调度抓取完成")
            status = "success"
            papers_new = (result or {}).get("new", 0)

            # 有新论文时发送今日邮件（统一走 notify.report，SMTP 放线程）
            if papers_new > 0:
                logger.info("调度触发：发送今日邮件通知")
                from src.notify.report import send_fetch_report

                await asyncio.to_thread(send_fetch_report, self._settings, PaperDB())
                logger.info("调度邮件发送完成")
        except Exception as e:
            logger.error("调度抓取失败: %s", e)
        finally:
            if self._on_fetch_done:
                from src.config.settings import now_str

                # 失败时 last_success 置 None：不把失败时刻当作"最近成功"，避免前端基线被污染
                self._on_fetch_done(
                    {
                        "type": "fetch-done",
                        "status": status,
                        "papers_new": papers_new,
                        "last_success": now_str() if status == "success" else None,
                    }
                )

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
