"""网络层：基于 arxiv 库的 ArxivSource

基于 arxiv 库和 BaseSource 抽象类实现 Arxiv 数据源，提供异步抓取接口。

契约实现：
    - ``ArxivOptions.to_list()``：把 keywords 合并，产出 ``(keyword, arxiv.Search)`` 元组列表；
    - ``_fetch(kw, options)``：解包 ``(keyword, Search)`` 执行一次 request，
      按 ``skip_ids`` 与 ``lookback_days`` 过滤，批内相关性重排后截断；
    - ``adapt(items)``：把 ``arxiv.Result`` 转成 ``core.models.Record``，
      arxiv 特有字段（version/primary_category/doi 等）存入 ``raw_data``。

排序/增量窗口约束（本模块 enforce，historical 判断在**下游**）：
    - 相关性排序（relevance）无法做时间窗口截断（增量分页按 published 时间判断），
      ``__post_init__`` 强制 ``lookback_days = 0``（等价历史全量搜索）；
    - 时间排序（lastUpdatedDate / submittedDate）必须携带 ``lookback_days > 0``，
      否则配置加载即报错（fail-fast）。
    - 本模块只在有效排序 ≠ Relevance 时做批内相关性重排。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import chain

from arxiv import Client, Result, Search, SortCriterion, SortOrder

from src.core.models import Record
from src.core.text import relevance_score
from src.network.base import BaseSource, FetchOptions
from src.network.registry import REGISTRY

# arXiv 论文 ID：新格式 2107.05580（2007 年后），可带可选的 vN 版本号；旧格式不再兼容
_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v(\d+))?")

_ARXIV_MAX_RESULTS = 2000  # arxiv API 单次请求上限
_ARXIV_ABS_URL = "https://arxiv.org/abs/{}"
_ARXIV_PDF_URL = "https://arxiv.org/pdf/{}"

_SORT_MAP = {
    "relevance": SortCriterion.Relevance,
    "lastupdateddate": SortCriterion.LastUpdatedDate,
    "submitteddate": SortCriterion.SubmittedDate,
}

AVG_PER_DAY = 100  # 预估每天最大数量


def _extract_id(entry_id: str) -> tuple[str, int]:
    """从 arxiv entry_id 提取短 ID 与版本号；无法识别返回 ("", 1)。

    仅支持新格式（2107.05580）；旧格式（hep-th/9901001）返回空 sid（该论文被跳过）。
    版本组可缺失（arXiv 部分条目无 vN 后缀），缺省按 1 处理。
    """
    m = _ID_RE.search(entry_id or "")
    if not m:
        return "", 1
    return m.group(1), int(m.group(2)) if m.group(2) else 1


def _is_skipped(sid: str, skip_ids) -> bool:
    """该论文是否应跳过：ID 无法解析 或 已在 DB（skip_ids 命中）。"""
    return not sid or (skip_ids and sid in skip_ids)


def _filter_page_results(
    page_results: list[Result],
    papers: list[Result],
    skip_ids,
    cutoff: str,
    sort_by: SortCriterion,
) -> bool:
    """过滤单页结果：跳过已有/无法解析 ID，按窗口截断；返回是否已越过窗口（hit_past）。

    窗口截断字段须与排序字段一致：按 lastUpdatedDate 排序时用 updated 判断，
    否则「旧发布但近期更新」的论文会误触发 hit_past，漏掉其后的新论文。
    """
    for r in page_results:
        sid, _ = _extract_id(r.entry_id)
        if _is_skipped(sid, skip_ids):
            continue
        if sort_by == SortCriterion.LastUpdatedDate:
            date_str = r.updated.strftime("%Y-%m-%d") if r.updated else ""
        else:
            date_str = r.published.strftime("%Y-%m-%d") if r.published else ""
        if date_str < cutoff:  # 时间截断
            return True
        papers.append(r)
    return False


# ─── Arxiv 抓取参数 ──────────────────────────────────────────


@REGISTRY.options.register("arxiv")
@dataclass
class ArxivOptions(FetchOptions):
    """Arxiv 数据源按次抓取参数：继承通用字段并扩展 arxiv API 特有项。"""

    # Search 相关参数（全局默认；可按 keyword 个性化覆盖）
    max_results: int = 20
    sort_by: str = "relevance"
    sort_order: str = "descending"

    # Client 初始化参数
    page_size: int = 100
    delay_seconds: float = 3
    num_retries: int = 3

    # 查询/过滤参数
    skip_ids: set[str] | None = None  # 需跳过的 source_id 集合
    lookback_days: int = 0  # 增量回溯天数；0 = 不过滤（历史全量）

    def __post_init__(self) -> None:
        """校验 sort 参数并确保 Client 单页大小覆盖抓取范围（offset 步长）。

        sort_by / sort_order 加载即报错（fail-fast，对齐 SchedulerConfig 的
        ``_validate_fetch_time``），避免运行期 ``to_list`` 才抛 KeyError/ValueError。
        arxiv API 单次请求上限 ``_ARXIV_MAX_RESULTS`` 条；page_size 放大到抓取范围。

        排序与增量窗口的强制约束：
        - 相关性排序无法做时间窗口截断（_fetch 增量分页按时间判断），
          强制 ``lookback_days = 0``（消除 relevance + lookback>0 时的窗口静默截断）；
        - 时间排序必须携带 ``lookback_days > 0``，否则"抓完整窗口"语义不成立，加载即报错；
        - 时间排序不支持 ``ascending``：升序首篇最旧、必然触发窗口截断，增量会静默返回
          0 篇（加载即报错，杜绝静默失效组合）。
        """
        if self.sort_by.lower() not in _SORT_MAP:
            raise ValueError(
                f"未知 sort_by: {self.sort_by!r}（可选: relevance/lastUpdatedDate/submittedDate）"
            )
        if self.sort_order.lower() not in ("ascending", "descending"):
            raise ValueError(f"未知 sort_order: {self.sort_order!r}（可选: ascending/descending）")
        self.sort_by = self.sort_by.lower()  # 与 sort_order 一致归一化，避免混合大小写外泄
        self.sort_order = self.sort_order.lower()  # to_list 的 SortOrder 按小写值匹配

        if self.sort_by == "relevance":
            self.lookback_days = 0  # 相关性排序强制全量（不做时间窗口）
        elif self.lookback_days <= 0:
            raise ValueError(
                f"时间排序 {self.sort_by!r} 要求 lookback_days > 0，收到 {self.lookback_days}"
            )
        elif self.sort_order == "ascending":
            raise ValueError(
                f"时间排序 {self.sort_by!r} 不支持 sort_order=ascending"
                "（增量窗口截断依赖降序结果，升序会静默返回 0 篇）"
            )

        if self.lookback_days > 0:  # 按时间排序，返回预估值
            page_size = min((self.lookback_days + 1) * AVG_PER_DAY, _ARXIV_MAX_RESULTS)
        else:
            page_size = self.max_results * 2  # 按相关性排序，直接选择 max_results * 2

        self.page_size = min(max(self.page_size, page_size), _ARXIV_MAX_RESULTS)

    def build_query(self, keyword: str, categories: list[str] | None = None) -> str:
        """由共享关键词条目（core.KeywordItem）构建 Arxiv API 查询字符串。

        categories 与 keyword 做 AND 组合（限定分类，不参与 OR 扩展）。

        Note:
            arxiv 库的 Search 要求 query 传**未编码**形式（库内部经 urlencode 编码），
            布尔算子用空格分隔——用 ``+OR+``/``+AND+`` 预编码会被二次编码成字面量 ``+``，
            分类组合查询失效。分类用 ``cat:XXX`` 前缀，关键词用 ``all:XXX`` 前缀。
        """
        cat_part = ""
        if categories:
            cat_part = "(" + " OR ".join(f"cat:{c}" for c in categories) + ") AND "
        return f"{cat_part}all:{keyword}"

    def to_list(self) -> list[tuple[str, Search]]:
        """把 keywords（core.KeywordItem）转成 ``(keyword, Search)`` 元组列表。

        查询构造使用源级参数（max_results / sort_by / sort_order）。
        """
        out: list[tuple[str, Search]] = []

        # 关键词已由 get_active_keywords 预过滤（单一来源），此处不再重复 active 判定
        for kw in self.keywords:
            out.append(
                (
                    kw.keyword,
                    Search(
                        query=self.build_query(kw.keyword, kw.categories),
                        max_results=self.page_size,
                        sort_by=_SORT_MAP[self.sort_by.lower()],
                        sort_order=SortOrder(self.sort_order),
                    ),
                )
            )
        return out


# ─── Arxiv 数据源 ──────────────────────────────────────────


@REGISTRY.sources.register("arxiv")
class ArxivSource(BaseSource[Result]):
    """Arxiv 数据源 —— 继承 BaseSource 的完整实现。"""

    # ── 适配器 ────────────────────────────────────────────────

    async def adapt(self, items: list[Result]) -> list[Record]:
        """把 arxiv.Result 转成 Record（arxiv 特有字段进 raw_data）。

        Note:
            ``keyword_match`` 由 ``BaseSource._try_fetch`` 统一回填，此处留空。
        """
        records: list[Record] = []
        for r in items:
            sid, version = _extract_id(r.entry_id)
            records.append(
                Record(
                    title=r.title.strip().replace("\n", " "),
                    authors=[a.name for a in r.authors],
                    abstract=r.summary.strip().replace("\n", " "),
                    published=r.published.isoformat() if r.published else "",
                    updated=r.updated.isoformat() if r.updated else "",
                    categories=list(r.categories),
                    url=_ARXIV_ABS_URL.format(sid) if sid else "",
                    pdf_url=r.pdf_url or (_ARXIV_PDF_URL.format(sid) if sid else ""),
                    keyword_match="",
                    source=self.source_name,
                    source_id=sid,
                    raw_data={
                        "version": version,
                        "primary_category": r.primary_category,
                        "doi": r.doi,
                        "journal_ref": r.journal_ref,
                        "comment": r.comment,
                        "citation": f"arXiv:{sid}"
                        + (f" [{r.primary_category}]" if r.primary_category else ""),
                    },
                )
            )
        return records

    # ── 单关键词抓取 ────────────────────────────────────────────

    async def _fetch(
        self,
        kw: tuple[str, Search],
        options: ArxivOptions,
    ) -> list[Result]:
        """解包 ``(keyword, Search)`` 抓取并过滤。

        - ``lookback_days > 0``（增量）：按时间序分页抓**完整个窗口**内的数据，
          批内按相关性重排后截断到 options.max_results；
        - 否则（历史/搜索）：一次抓取，过滤 skip 后截断 options.max_results。
        """
        keyword, search = kw
        skip_ids = options.skip_ids
        lookback_days = options.lookback_days
        range_size = search.max_results  # to_list 恒设 max_results=page_size

        # 多关键词经 fetch() 共享 Client（限流节流跨关键词生效）；直接调 _fetch 时兜底自建
        client = Client(
            page_size=range_size,
            delay_seconds=options.delay_seconds,
            num_retries=options.num_retries,
        )

        def _run(search_obj: Search, offset: int) -> list[Result]:
            return list(client.results(search_obj, offset=offset))

        loop = asyncio.get_running_loop()

        papers: list[Result] = []

        if lookback_days <= 0:
            # 历史/搜索：一次抓 range_size 条，过滤 skip 后按 range_size 截断
            results = await loop.run_in_executor(None, _run, search, 0)

            for r in results:
                sid, _ = _extract_id(r.entry_id)
                if _is_skipped(sid, skip_ids):
                    continue
                papers.append(r)
            return papers[: options.max_results]

        # 增量：分页抓完 lookback 窗口
        # 窗口截断字段与排序字段一致（LastUpdatedDate 用 updated，SubmittedDate 用 published）；
        # 排序/窗口约束已由 __post_init__ 强制（时间排序必带 lookback_days>0 且必须降序，
        # 相关性排序强制 lookback_days=0），时间排序可靠。
        cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()[:10]
        offset = 0
        while True:
            page_search = Search(
                query=search.query,
                # arxiv 库 results(search, offset) 返回 max_results - offset 条（offset 作 start），
                # 故 max_results 需 = offset + range_size，才能每页固定 range_size 条。
                max_results=offset + range_size,
                sort_by=search.sort_by,
                sort_order=search.sort_order,
            )
            page_results = await loop.run_in_executor(None, _run, page_search, offset)

            if not page_results:  # 没有抓取结果
                break

            hit_past = _filter_page_results(page_results, papers, skip_ids, cutoff, search.sort_by)
            if hit_past or len(page_results) < range_size:  # 边界条件
                break

            offset += range_size

        # 批内相关性重排，再截断到最终数量
        if search.sort_by != SortCriterion.Relevance:
            papers.sort(
                key=lambda r: relevance_score(
                    {"title": r.title or "", "abstract": r.summary or ""}, keyword
                ),
                reverse=True,
            )
        return papers[: options.max_results]

    async def fetch(self, options: ArxivOptions) -> list[Record]:
        return await super().fetch(options, max_concurrent=1)

    # ── 数据源名称 ─────────────────────────────────────────────

    @property
    def source_name(self) -> str:
        return "arxiv"
