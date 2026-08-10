"""数据库状态枚举：status / user_mark / remark 的单一事实来源。

SQLite 存 TEXT，代码层用 StrEnum 常量引用，消灭散落字符串。
"""

from enum import StrEnum


class PaperStatus(StrEnum):
    """论文生命周期状态（papers.status）。"""

    SUMMARIZED = "summarized"  # 已 LLM 评分，待审阅
    IGNORED = "ignored"  # 已忽略
    REVIEWED = "reviewed"  # 已处理（标记为延后/导入）


class UserMark(StrEnum):
    """用户审阅标记（papers.user_mark；NULL 表示待审阅）。"""

    IGNORE = "ignore"
    LURK = "lurk"
    IMPORTED = "imported"
    PENDING = "pending"  # 移回待审阅（update_mark 动作值；存储时 user_mark 置 NULL）


class FetchLogStatus(StrEnum):
    """抓取日志状态（fetch_logs.status）。"""

    SUCCESS = "success"
    FAILED = "failed"


# 待审阅 WHERE 条件（get_pending 与 get_stats 共用，消灭重复 SQL）
PENDING_WHERE = (
    f"status = '{PaperStatus.SUMMARIZED.value}' AND (user_mark IS NULL OR user_mark = '')"
)


def fetch_date_clause(fetch_date_from: str | None) -> tuple[str, list]:
    """可选的 fetch_date 范围 WHERE 片段（None 表示不限）。

    论文查询（papers 表）与统计共用，消灭两处逐字重复的 SQL 片段。
    """
    if fetch_date_from:
        return " AND fetch_date >= ?", [fetch_date_from]
    return "", []
