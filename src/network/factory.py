"""数据源工厂：按 settings.source 路由到对应模块，解耦抓取管道与具体数据源。

当前支持的数据源:
  - "arxiv" (默认): 从 arxiv.org 抓取

扩展方式:
    1. 继承 ``base.BaseSource`` 实现子类（例如 ``PubmedSource``）
    2. 通过 ``register_source()`` 注册到工厂
    3. 在 settings.json 中设置 ``"source": "pubmed"``
"""

from __future__ import annotations

from typing import Any

from src.network.base import BaseSource



# ─── Arxiv 数据源适配器 ─────────────────────────────────────


class ArxivSource(BaseSource):
    """Arxiv 数据源 —— 委托给 ``src.network.arxiv`` 模块函数。"""

    async def fetch_all(self, keywords, settings, max_results=50, historical=False, skip_ids=None):
        from src.network.arxiv import fetch_all as _fetch_all

        return await _fetch_all(keywords, settings, max_results, historical, skip_ids)

    async def fetch_by_ids(self, ids):
        from src.network.arxiv import fetch_by_ids as _fetch_by_ids

        return await _fetch_by_ids(ids)

    async def search(self, query, max_results=20, categories=None):
        from src.network.arxiv import search_arxiv as _search

        return await _search(query, max_results, categories)

    async def download_pdf(self, paper_id, output_dir):
        from src.network.arxiv import download_pdf as _download

        return await _download(paper_id, output_dir)


# ─── 注册表 ─────────────────────────────────────────────────


_SOURCES: dict[str, type] = {
    "arxiv": ArxivSource,
}


def register_source(name: str, cls: type[BaseSource]) -> None:
    """注册新的数据源类型（供扩展使用）"""
    _SOURCES[name] = cls


def get_source(settings: Any) -> BaseSource:
    """根据配置获取数据源实例。

    Args:
        settings: 配置对象（需含 ``source`` 字段，通常为 ``AppConfig``）

    Returns:
        实现了 ``BaseSource`` 抽象基类的数据源实例

    Raises:
        ValueError: 未知的数据源名称
    """
    source_name = getattr(settings, "source", "arxiv")
    cls = _SOURCES.get(source_name)
    if cls is None:
        raise ValueError(
            f"Unknown data source: {source_name!r}. "
            f"Available: {list(_SOURCES.keys())}"
        )
    return cls()
