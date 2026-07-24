"""网络模块：数据源接口。"""

from src.network.base import BaseSource
from src.network.factory import get_source, register_source

__all__ = [
    "BaseSource",
    "get_source",
    "register_source",
]
