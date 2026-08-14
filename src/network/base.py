"""数据源抽象基类 —— 所有数据源必须继承此类并实现抽象方法。

扩展方式:
    1. 继承 ``BaseSource``，实现抽象方法并通过 ``REGISTRY.sources.register("名称", 子类)`` 注册
    2. 继承 ``FetchOptions``，实现抓取参数 dataclass 并通过 ``REGISTRY.options.register("名称", 子类)`` 注册
    3. 在 ``config.yaml`` 中设置 ``fetch.sources[].source`` 为注册的名称

契约方向：
- ``core.models.Record`` 是数据源与数据库共享的论文元数据契约；
    - ``FetchOptions`` 由 network 规定 ``Options`` 和 ``RawSearch`` 格式，调用方（config）遵守规定。
    - ``BaseSource._fetch()`` 根据 ``RawSearch`` 格式进行 request，并生成 ``RawResult``（network 侧规定）；
    - ``BaseSource.adapt()`` 把 ``RawResult`` 转成 ``Record``；
    - ``BaseSource.fetch()`` 是模板方法：异步遍历不同关键词 → adapt → list[Record]；

- ``source``: 规定 ``RawSearch`` 和 ``RawResult`` 的格式，并进一步规定 ``FetchOptions`` 和 ``Source`` 方法，调用方遵守规定。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from itertools import chain
from typing import Generic, TypeVar

from src.core.fetch import FetchOptions, RawSearch
from src.core.models import Record

logger = logging.getLogger(__name__)

# 数据源原生结果类型，由各子类指定
RawResult = TypeVar("RawResult")


class BaseSource(ABC, Generic[RawResult]):
    """数据源基类。

    定义数据源的标准接口，所有数据源（Arxiv、PubMed、DBLP 等）必须遵循此接口。
    通过 ``REGISTRY.sources.get("名称")`` 获取数据源类。

    扩展示例::

        @REGISTRY.sources.register("pubmed")
        class PubmedSource(BaseSource[pmapi.Result]):
            @property
            def source_name(self) -> str:
                return "pubmed"

            async def _fetch(
                self, kw, options
            ) -> list[pmapi.Result]: ...  # 单个搜索项 → 一次 request

            async def adapt(self, items: list[pmapi.Result]) -> list[Record]: ...
    """

    # ── 适配器 ────────────────────────────────────────────────

    @abstractmethod
    async def adapt(
        self,
        items: list[RawResult],
    ) -> list[Record]:
        """将数据源原生结果转化成论文元数据（Record）。

        Note:
            ``Record.keyword_match`` 由 ``_try_fetch`` 在 adapt 之后统一回填，
            各源在此无需关心关键词。
        """
        ...

    # ── 单关键词抓取 ─────────────────────────────────────────────

    @abstractmethod
    async def _fetch(
        self,
        kw: RawSearch,
        options: FetchOptions,
    ) -> list[RawResult]:
        """抓取单个搜索项的结果。

        Args:
            kw: ``to_list()`` 产出的单个搜索项。
                约定为 ``(keyword, 原生Search)`` 元组 —— 源内部解包后执行一次 request，
                keyword 供过滤/重排等按关键词逻辑使用。
            options: 按次运行参数（``FetchOptions``，由调用方构建）

        Returns:
            该搜索项的原生结果列表
        """
        ...

    # ── 单关键词抓取（容错模板）─────────────────────────────────

    async def _try_fetch(
        self,
        kw: RawSearch,
        options: FetchOptions,
    ) -> list[Record]:
        """抓取单个关键词 → adapt → 回填 keyword_match（容错包装）。

        Args:
            kw: ``(keyword, 原生Search)`` 元组
            options: 按次运行参数

        Returns:
            该关键词的 Record 列表；失败时返回空列表
        """
        keyword, _ = kw
        try:
            results = await self._fetch(kw, options)
            records = await self.adapt(results)
            for r in records:
                r.keyword_match = keyword
            logger.info("[%s]: %s 篇", keyword, len(records))
            return records
        except Exception as e:
            logger.exception("关键词 [%s] 抓取失败: %s", keyword, e)  # 带完整栈，便于定位真实 bug
            return []

    # ── 批量抓取（模板方法）────────────────────────────────────────

    async def fetch(
        self,
        options: FetchOptions,
    ) -> list[Record]:
        """异步遍历不同关键词 → adapt。

        模板方法：为每个关键词调用 ``_try_fetch``（内部含 ``_fetch`` + ``adapt``）。

        Args:
            options: 按次运行参数（``FetchOptions``，由调用方构建）

        Returns:
            论文元数据列表（不去重）
        """
        lists = await asyncio.gather(*[self._try_fetch(kw, options) for kw in options.to_list()])
        return list(chain.from_iterable(lists))

    # ── 数据源名称 ─────────────────────────────────────────────

    @property
    @abstractmethod
    def source_name(self) -> str:
        """获取数据源名称（注册名）。"""
        ...
