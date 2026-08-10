"""网络模块：数据源接口。

导入本包即触发 ``src.network.source.arxiv`` 的 ``REGISTRY.register`` 装饰器注册，
因此 ``REGISTRY.sources.get()`` 可路由到全部已注册数据源。
"""

from src.core.fetch import FetchOptions
from src.network.base import BaseSource
from src.network.registry import REGISTRY
from src.network.source import arxiv

__all__ = [
    "REGISTRY",
    "BaseSource",
    "FetchOptions",
]
