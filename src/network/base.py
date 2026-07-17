"""数据源抽象基类 —— 所有数据源必须继承此类并实现抽象方法。

扩展方式:
    1. 继承 ``BaseSource``，实现所有抽象方法
    2. 通过 ``factory.register_source("名称", 子类)`` 注册
    3. 在 ``settings.json`` 中设置 ``source`` 为注册的名称
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseSource(ABC):
    """数据源基类。

    定义数据源的标准接口，所有数据源（Arxiv、PubMed、DBLP 等）必须遵循此接口。
    通过 ``factory.get_source()`` 获取数据源实例。

    扩展示例::

        class PubmedSource(BaseSource):
            async def fetch_all(self, keywords, settings, ...):
                ...
            async def fetch_by_ids(self, ids):
                ...
            async def search(self, query, ...):
                ...
            async def download_pdf(self, paper_id, output_dir):
                ...

        register_source("pubmed", PubmedSource)
    """

    # ── 批量抓取 ────────────────────────────────────────────────

    @abstractmethod
    async def fetch_all(
        self,
        keywords: list[dict],
        settings: Any,
        max_results: int = 50,
        historical: bool = False,
        skip_ids: set[str] | None = None,
    ) -> tuple[list[dict], int, int]:
        """批量抓取论文。

        Args:
            keywords:     关键词列表，每项含 ``keyword``, ``arxiv_cats``(可选), ``active``
            settings:     全局配置对象（通常为 ``AppConfig``）
            max_results:  每个关键词最大结果数
            historical:   ``True``=全量回溯不限制时间，``False``=仅限时间窗口
            skip_ids:     需要跳过的已有 ID 集合（已入库的论文）

        Returns:
            (去重后的论文列表, 原始总篇数, 重复篇数)
        """
        ...

    # ── 按 ID 获取 ─────────────────────────────────────────────

    @abstractmethod
    async def fetch_by_ids(self, ids: list[str]) -> list[dict]:
        """通过 ID 列表精确获取论文元数据。

        Args:
            ids: 数据源专有 ID 列表（如 Arxiv ID, PMID）

        Returns:
            论文元数据列表
        """
        ...

    # ── 搜索（无日期限制）───────────────────────────────────────

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 20,
        categories: list[str] | None = None,
    ) -> list[dict]:
        """搜索论文（不限时间，用于前端预览或交互式搜索）。

        Args:
            query:        搜索关键词，可包含 ``ti:``, ``au:`` 等前缀
            max_results:  最大结果数
            categories:   可选分类过滤列表

        Returns:
            论文元数据列表
        """
        ...

    # ── PDF 下载 ────────────────────────────────────────────────

    @abstractmethod
    async def download_pdf(self, paper_id: str, output_dir: Path) -> Path:
        """下载论文 PDF 到本地。

        Args:
            paper_id:   数据源专有论文 ID
            output_dir: 输出根目录

        Returns:
            下载后的 PDF 文件路径
        """
        ...
