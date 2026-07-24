"""调度服务：内置于 serve 的定时抓取调度器

特性：
- 按 config.yaml 的 scheduler.fetch_time 每日定时抓取
- 时区感知（scheduler.timezone），Windows 无 tzdata 时回退为系统本地时间
- catch_up_on_start：serve 启动时若今天已过抓取时间但尚未成功抓取，立即补抓一次
"""

import asyncio
import logging
from datetime import datetime, timedelta

from src.zotero import ZoteroClient

logger = logging.getLogger(__name__)


def _local_now(timezone: str) -> datetime:
    """获取指定时区的当前时间。

    Windows Python 默认不带时区数据库（需要 tzdata 包），
    加载失败时回退为系统本地时间（对本地部署场景通常即为目标时区）。
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(timezone))
    except Exception:
        return datetime.now()


class FetchScheduler:
    """内置于 serve 的定时抓取调度器。"""

    def __init__(self, settings, zotero: ZoteroClient | None = None):
        self.enabled = settings.scheduler.enabled
        self.fetch_time = settings.scheduler.fetch_time
        self.timezone = settings.scheduler.timezone
        self.catch_up_on_start = getattr(settings.scheduler, "catch_up_on_start", True)
        self._settings = settings
        self._zotero = zotero
        self._task: asyncio.Task | None = None
        self._last_run: datetime | None = None
        self._next_run_time: datetime | None = None

    def _now(self) -> datetime:
        return _local_now(self.timezone)

    async def start(self):
        if not self.enabled:
            logger.info("调度器未启用")
            return

        logger.info(f"调度器已启动，抓取时间: {self.fetch_time} ({self.timezone})")

        # 启动补抓：笔记本/台式机场景下，开机时间可能晚于 fetch_time
        if self.catch_up_on_start and self._should_catch_up():
            logger.info("今日抓取尚未执行且已过计划时间，启动补抓")
            await self._run_fetch()

        while True:
            now = self._now()
            target = self._next_run(now)
            self._next_run_time = target
            wait_seconds = max((target - now).total_seconds(), 1)
            logger.info(f"距离下次抓取还有 {wait_seconds:.0f} 秒")
            await asyncio.sleep(wait_seconds)
            # 今日已成功抓取过（如用户在页面手动抓取）则跳过，
            # 避免重复抓取以及由此带来的重复邮件
            if self._already_fetched_today():
                logger.info("今日已有成功抓取记录，跳过本次定时抓取")
                continue
            await self._run_fetch()

    def _already_fetched_today(self) -> bool:
        """今天是否已有成功的抓取记录（含手动抓取）。"""
        try:
            from src.db import PaperDB

            today = self._now().date().isoformat()
            for log in PaperDB().get_recent_logs(limit=10):
                run_time = log.get("run_time") or ""
                if run_time.startswith(today) and log.get("status") == "success":
                    return True
            return False
        except Exception as e:
            logger.warning(f"抓取记录检查失败（按未抓取处理）: {e}")
            return False

    def _should_catch_up(self) -> bool:
        """判断是否需要补抓：今天已过计划时间 且 今天没有成功抓取记录。"""
        now = self._now()
        try:
            hour, minute = map(int, self.fetch_time.split(":"))
        except ValueError:
            return False
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < target:
            return False  # 今天还没到点，正常等待即可
        return not self._already_fetched_today()

    def _next_run(self, now: datetime) -> datetime:
        hour, minute = map(int, self.fetch_time.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    async def _run_fetch(self, notify: bool = True):
        """执行一次定时抓取。

        Args:
            notify: 抓取完成后有新论文时是否自动发送邮件。
                    仅调度器（定时/补抓）使用 True；手动抓取路径不经过本方法，
                    因此手动抓取永远不会自动发邮件。
        """
        self._last_run = self._now()
        logger.info("调度触发：开始抓取")
        try:
            from src.db import PaperDB
            from src.network.fetch_pipeline import run_fetch_pipeline

            result = await run_fetch_pipeline(
                self._settings,
                [kw for kw in self._settings.keywords if kw.active],
                self._settings.fetch.max_results,
                mode="incremental",
            )
            logger.info("调度抓取完成")

            # 串行：有新论文时发送邮件（SMTP 为同步阻塞调用，放到线程中执行）
            if notify and result and result.get("new", 0) > 0:
                logger.info("调度触发：发送邮件通知")
                from src.notify import EmailNotifier

                db = PaperDB()
                pending = db.get_pending()
                if pending:
                    notifier = EmailNotifier(self._settings.notification)
                    server_cfg = self._settings.server
                    server_url = f"http://{server_cfg.host}:{server_cfg.port}"
                    await asyncio.to_thread(
                        notifier.send_fetch_report,
                        stats=db.get_stats(),
                        new_papers=pending,
                        keywords=[kw.keyword for kw in self._settings.keywords if kw.active],
                        server_url=server_url,
                    )
                    logger.info("调度邮件发送完成")
        except Exception as e:
            logger.error(f"调度抓取失败: {e}")

    @property
    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "fetch_time": self.fetch_time,
            "timezone": self.timezone,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "next_run": self._next_run_time.isoformat() if self._next_run_time else None,
        }

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
