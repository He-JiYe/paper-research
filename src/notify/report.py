"""统一的通知发送入口：把 "今日 pending → 组装 → 发送" 收敛为单一函数。

只通知**今日抓取**的结果（fetch_date = 今日），不发送历史累积；
今日日期按系统本地时间计算（与调度器补抓/定时、Web 范围同一口径，
见 ``config.settings.today_str``）。
"""

import logging

from src.config.settings import today_str

logger = logging.getLogger(__name__)


def send_fetch_report(settings, db) -> dict:
    """发送今日抓取报告邮件（今日 pending 为空则跳过）。

    Args:
        settings: AppConfig（含 notification / server / scheduler 配置）
        db: PaperDB 实例

    Returns:
        {"sent": bool, "reason": str | None} —— reason 为跳过原因（如 "no pending papers"）。
    """
    from src.config.settings import get_active_keywords
    from src.notify.sender import EmailNotifier

    today = today_str()
    pending = db.get_pending(fetch_date_from=today)
    if not pending:
        logger.info("今日无待审阅论文，跳过邮件通知")
        return {"sent": False, "reason": "no pending papers"}

    server_cfg = settings.server
    # 邮件链接须用可访问地址：host=0.0.0.0/::（监听所有接口）时改用 127.0.0.1，否则收件人收到死链。
    host = server_cfg.host if server_cfg.host not in ("0.0.0.0", "::") else "127.0.0.1"
    notifier = EmailNotifier(settings.notification)
    sent = notifier.send_fetch_report(
        stats=db.get_stats(fetch_date_from=today),
        new_papers=pending,
        keywords=[kw.keyword for kw in get_active_keywords(settings)],
        server_url=f"http://{host}:{server_cfg.port}",
    )
    return {"sent": sent, "reason": None}
