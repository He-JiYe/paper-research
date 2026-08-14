"""数据库状态枚举：user_mark / remark / fetch_logs.status 的单一事实来源。

SQLite 存 TEXT，代码层用 StrEnum 常量引用，消灭散落字符串。
（papers.status 冗余列已删除：状态由 user_mark 单一推导，见 papers.update_mark。）
"""

from enum import StrEnum


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
# 待审阅 = 未标记：所有标记路径（ignore/lurk/imported/pending 复位）都会改写 user_mark
PENDING_WHERE = "user_mark IS NULL"


def fetch_date_clause(fetch_date_from: str | None) -> tuple[str, list[str]]:
    """可选的 fetch_date 范围 WHERE 片段（None 表示不限）。

    论文查询（papers 表）与统计共用，消灭两处逐字重复的 SQL 片段。
    """
    if fetch_date_from:
        return " AND fetch_date >= ?", [fetch_date_from]
    return "", []
