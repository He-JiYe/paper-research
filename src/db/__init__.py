"""数据库层：PaperDB = 连接 + 论文 CRUD + 统计/日志 的组合。"""

from src.db.connection import ConnectionMixin
from src.db.fetch_logs import LogCrudMixin
from src.db.papers import PaperCrudMixin


class PaperDB(PaperCrudMixin, LogCrudMixin):
    """SQLite 数据库操作封装（WAL 模式 + 线程安全写锁）。

    MRO：PaperDB → PaperCrudMixin → LogCrudMixin → ConnectionMixin。
    """


__all__ = ["PaperDB"]
