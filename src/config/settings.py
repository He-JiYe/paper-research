"""配置派生助手：从 AppConfig 派生运行所需的简单查询。

「今日」唯一口径：抓取 fetch_date、邮件今日过滤、Web ?range=today、调度器判重
统一使用系统本地时间（不依赖配置时区）。
"""

import datetime

from src.config.loader import load_settings
from src.core.config import AppConfig
from src.core.models import KeywordItem


def _local_now() -> datetime.datetime:
    """取当前系统本地时刻（统一"今日"口径）。"""
    return datetime.datetime.now()


def today_str() -> str:
    """取今日日期（YYYY-MM-DD，系统本地时间）。"""
    return _local_now().date().isoformat()


def now_str() -> str:
    """取当前时间戳（YYYY-MM-DD HH:MM:SS，fetch_logs.run_time 用，系统本地时间）。"""
    return _local_now().strftime("%Y-%m-%d %H:%M:%S")


def get_active_keywords(settings: AppConfig | None = None) -> list[KeywordItem]:
    """获取所有活跃的关键词（全源共享）。"""
    if settings is None:
        settings = load_settings()
    return [kw for kw in settings.keywords if kw.active]
