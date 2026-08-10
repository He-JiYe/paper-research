"""API 载荷构建：从 PaperDB 组装 /api/papers 的响应 JSON（纯函数，可单测）。

支持按日期范围（range=all|today|7d|30d）过滤论文与统计；
前端只保留筛选/标记/导入，不再需要 fetch_config。
"""

import datetime
from typing import Any

from src.core.text import suggest_short_title

# 范围 → 起始日偏移（含当天，故 N 天范围取 N-1 天前）；all → None
_RANGE_DAYS = {"today": 0, "7d": 6, "30d": 29}


def range_start_date(range_: str, today: datetime.date | None = None) -> str | None:
    """把 range 名换算成 fetch_date 起始日（YYYY-MM-DD）；all/未知 → None。"""
    if range_ in (None, "", "all"):
        return None
    today = today or datetime.date.today()
    days = _RANGE_DAYS.get(range_)
    if days is None:
        return None  # 未知 range → 不限（与 all 同语义；路由层已对非法值 400）
    return (today - datetime.timedelta(days=days)).isoformat()


def build_papers_payload(
    db, range_: str = "all", today: datetime.date | None = None
) -> dict[str, Any]:
    """构建 /api/papers 响应。

    Args:
        db: PaperDB 实例（可传 mock 以便单测）。
        range_: 日期范围（all/today/7d/30d）。
        today: "今日"参考日期（由路由按系统本地时间计算传入，保证与邮件/调度口径一致；
            缺省用系统本地日期）。

    Returns:
        {
            "update_time", "range", "stats",
            "sections": {"unmarked": [...], "marked": [...], "lurk": [...]}
        }
    """
    fetch_date_from = range_start_date(range_, today)
    unmarked = db.get_pending(fetch_date_from)
    marked = db.get_marked(fetch_date_from)
    lurk = db.get_lurk(fetch_date_from)

    # 为无 short_title 的论文补建议短标题（前端短标题输入框回填用）
    for group in (unmarked, marked, lurk):
        for p in group:
            if not p.get("short_title"):
                p["suggested_short_title"] = suggest_short_title(p)

    stats = db.get_stats(fetch_date_from)
    by_remark = stats.get("by_remark", {}) if isinstance(stats.get("by_remark"), dict) else {}
    stats_payload = {
        "total": stats.get("total", 0),
        "important": by_remark.get("important", 0),
        "useful": by_remark.get("useful", 0),
        "browse": by_remark.get("browse", 0),
        "unmarked": len(unmarked),
    }

    return {
        "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "range": range_,
        "stats": stats_payload,
        "sections": {"unmarked": unmarked, "marked": marked, "lurk": lurk},
    }
