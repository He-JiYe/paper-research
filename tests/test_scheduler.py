"""FetchScheduler 调度与邮件解耦测试。"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from src.serve.scheduler import FetchScheduler


@pytest.fixture
def scheduler(mock_settings) -> FetchScheduler:
    mock_settings.scheduler.enabled = True
    return FetchScheduler(mock_settings)


def _patch_today(scheduler, day: datetime):
    """让 scheduler._now() 返回固定时间"""
    return patch.object(scheduler, "_now", return_value=day)


class TestFetchedSinceFetchTime:
    """_fetched_since_fetch_time：拿 run_time 与「今日 fetch_time」比较（补抓判定）。"""

    @pytest.mark.asyncio
    async def test_true_when_success_after_fetch_time(self, scheduler):
        with patch("src.db.PaperDB") as mock_db_cls:
            mock_db_cls.return_value.has_successful_since.return_value = True
            with _patch_today(scheduler, datetime(2026, 7, 24, 10, 0)):
                assert await scheduler._fetched_since_fetch_time() is True
            # fetch_time=09:00 → since 取「今日 09:00:00」
            mock_db_cls.return_value.has_successful_since.assert_called_once_with(
                "2026-07-24 09:00:00"
            )

    @pytest.mark.asyncio
    async def test_false_when_no_success_log(self, scheduler):
        with patch("src.db.PaperDB") as mock_db_cls:
            mock_db_cls.return_value.has_successful_since.return_value = False
            with _patch_today(scheduler, datetime(2026, 7, 24, 10, 0)):
                assert await scheduler._fetched_since_fetch_time() is False

    @pytest.mark.asyncio
    async def test_false_on_db_error(self, scheduler):
        with patch("src.db.PaperDB", side_effect=RuntimeError("db locked")):
            assert await scheduler._fetched_since_fetch_time() is False


class TestRunFetchNotify:
    """调度抓取（定时/补抓统一）有新论文时发送今日邮件。"""

    @pytest.mark.asyncio
    async def test_run_fetch_sends_email_when_new_papers(self, scheduler):
        mock_pipeline = AsyncMock(return_value={"fetched": 5, "new": 3, "summarized": 3})
        with (
            patch("src.pipeline.fetch.run_fetch_pipeline", mock_pipeline),
            patch("src.db.PaperDB"),
            patch("src.notify.report.send_fetch_report") as send,
        ):
            await scheduler._run_fetch()
            send.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_new_papers_no_email(self, scheduler):
        mock_pipeline = AsyncMock(return_value={"fetched": 0, "new": 0, "summarized": 0})
        with (
            patch("src.pipeline.fetch.run_fetch_pipeline", mock_pipeline),
            patch("src.db.PaperDB"),
            patch("src.notify.report.send_fetch_report") as send,
        ):
            await scheduler._run_fetch()
            send.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_error_no_email(self, scheduler):
        mock_pipeline = AsyncMock(side_effect=RuntimeError("fetch failed"))
        with (
            patch("src.pipeline.fetch.run_fetch_pipeline", mock_pipeline),
            patch("src.db.PaperDB"),
            patch("src.notify.report.send_fetch_report") as send,
        ):
            await scheduler._run_fetch()  # 异常被捕获，不抛出
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_fetch_publishes_fetch_done(self, scheduler):
        """抓取完成后发布 fetch-done SSE 事件（前端据此自动刷新，取代轮询）"""
        events = []
        scheduler._on_fetch_done = events.append
        mock_pipeline = AsyncMock(return_value={"fetched": 5, "new": 3, "summarized": 3})
        with (
            patch("src.pipeline.fetch.run_fetch_pipeline", mock_pipeline),
            patch("src.db.PaperDB"),
            patch("src.notify.report.send_fetch_report"),
        ):
            await scheduler._run_fetch()
        assert len(events) == 1
        assert events[0]["type"] == "fetch-done"
        assert events[0]["status"] == "success"
        assert events[0]["papers_new"] == 3
        assert events[0]["last_success"]  # 成功时带上最近成功时间

    @pytest.mark.asyncio
    async def test_run_fetch_publishes_failed(self, scheduler):
        """抓取失败也发布 fetch-done（status=failed），前端仍可感知"""
        events = []
        scheduler._on_fetch_done = events.append
        mock_pipeline = AsyncMock(side_effect=RuntimeError("fetch failed"))
        with (
            patch("src.pipeline.fetch.run_fetch_pipeline", mock_pipeline),
            patch("src.db.PaperDB"),
            patch("src.notify.report.send_fetch_report"),
        ):
            await scheduler._run_fetch()
        assert len(events) == 1
        assert events[0]["type"] == "fetch-done"
        assert events[0]["status"] == "failed"
        assert events[0]["last_success"] is None  # 失败不把失败时刻当"最近成功"


class TestScheduledSkip:
    """定时循环：今日 fetch_time 之后已成功抓取（含手动抓取）则跳过，不再抓取也不发邮件"""

    @pytest.mark.asyncio
    async def test_start_skips_when_already_fetched(self, scheduler):
        mock_run_fetch = AsyncMock()
        with (
            patch.object(scheduler, "_should_catch_up", new_callable=AsyncMock, return_value=False),
            patch.object(
                scheduler, "_fetched_since_fetch_time", new_callable=AsyncMock, return_value=True
            ),
            patch.object(scheduler, "_run_fetch", mock_run_fetch),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            # 第二轮循环前抛异常跳出 while True
            mock_sleep.side_effect = [None, KeyboardInterrupt]
            with pytest.raises(KeyboardInterrupt):
                await scheduler.start()
            mock_run_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_catch_up_runs_fetch(self, scheduler):
        """启动补抓：应执行一次 _run_fetch（补今日抓取）。"""
        mock_run_fetch = AsyncMock()
        with (
            patch.object(scheduler, "_should_catch_up", new_callable=AsyncMock, return_value=True),
            patch.object(scheduler, "_run_fetch", mock_run_fetch),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = [KeyboardInterrupt]
            with pytest.raises(KeyboardInterrupt):
                await scheduler.start()
            mock_run_fetch.assert_awaited_once()


def test_start_disabled_returns_immediately(scheduler):
    """调度器未启用时 start 直接返回，不抓取。"""
    scheduler.enabled = False
    with patch.object(scheduler, "_run_fetch", AsyncMock()) as mock_run:
        asyncio.run(scheduler.start())
    mock_run.assert_not_called()


def test_should_catch_up_before_time(scheduler, monkeypatch):
    """今天还没到抓取时间 → 不补抓。"""
    from datetime import datetime

    monkeypatch.setattr(scheduler, "_now", lambda: datetime(2026, 8, 12, 6, 0))  # 08:30 前
    assert asyncio.run(scheduler._should_catch_up()) is False
