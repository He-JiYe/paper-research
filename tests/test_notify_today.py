"""notify.report 今日过滤测试：只通知今日抓取结果，不通知历史累积。

「今日」口径统一来自 ``config.settings.today_str``（系统本地时间）。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.config.settings import today_str
from src.notify.report import send_fetch_report


def _settings(**kw):
    base = {
        "notification": SimpleNamespace(enabled=True),
        "server": SimpleNamespace(host="127.0.0.1", port=8899),
        "keywords": [],
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_today_str_format():
    d = today_str()
    assert len(d) == 10
    assert d.count("-") == 2  # YYYY-MM-DD


def test_report_filters_by_today():
    settings = _settings()
    db = MagicMock()
    db.get_pending.return_value = [{"title": "T"}]
    db.get_stats.return_value = {"total": 1, "by_remark": {}}
    with patch("src.notify.sender.EmailNotifier") as ne:
        ne.return_value.send_fetch_report.return_value = True
        result = send_fetch_report(settings, db)
    assert result == {"sent": True, "reason": None}
    # 关键：get_pending 传了 fetch_date_from=today（与 today_str 同一口径）
    assert db.get_pending.call_args.kwargs["fetch_date_from"] == today_str()
    assert db.get_stats.call_args.kwargs["fetch_date_from"] == today_str()


def test_report_skips_when_no_today_pending():
    settings = _settings()
    db = MagicMock()
    db.get_pending.return_value = []  # 今日无新论文
    with patch("src.notify.sender.EmailNotifier") as ne:
        result = send_fetch_report(settings, db)
    assert result == {"sent": False, "reason": "no pending papers"}
    ne.assert_not_called()
