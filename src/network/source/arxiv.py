"""网络层：基于 arxiv 库的 ArxivSource

基于 arxiv 库和 BaseSource 抽象类实现 Arxiv 数据源，提供异步抓取接口。

契约实现：
    - ``ArxivOptions.to_list()``：把 keywords 合并，产出 ``(keyword, arxiv.Search)`` 元组列表；
    - ``_fetch(kw, options)``：解包 ``(keyword, Search)`` 分页抓取，按 ``skip_ids``
      过滤，按排序序（相关性/时间）抓满 ``max_results`` 篇未跳过即停；
    - ``adapt(items)``：把 ``arxiv.Result`` 转成 ``core.models.Record``，
      arxiv 特有字段（version/primary_category/doi 等）存入 ``raw_data``。

排序/增量窗口约束（本模块 enforce，historical 判断在**下游**）：
    - 抓取只有两种模式：增量（``lookback_days > 0``，推荐 ``relevance``）与历史
      （``lookback_days = 0``，忽略时间约束、直接取 max_results）；
    - 任意排序（relevance / submittedDate / lastUpdatedDate）+ ``lookback_days > 0``：
      查询内追加 ``submittedDate:[.. TO ..]`` 服务端过滤（2026-08 实证严格 0 越界），
      按该排序序抓满 max_results 篇未跳过即停；``lastUpdatedDate`` 的窗口约束同为
      submittedDate（API 无 updatedDate 服务端过滤，排序只决定窗口内顺序）；
    - ``lookback_days = 0``（历史）：不追加任何日期约束，按排序序直接取 max_results；
    - ``sort_order`` 仅支持 ``descending``（ascending 无合理增量语义，加载即报错）。
    - 旧实现（客户端按 updated/published 截断 + 批内手动相关性重排）已移除：
      日期窗口一律经 submittedDate 服务端过滤，相关性排序由 API 保证。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from arxiv import Client, Result, Search, SortCriterion, SortOrder

from src.core.models import Record
from src.network.base import BaseSource, FetchOptions
from src.network.registry import REGISTRY

# arXiv 论文 ID：新格式 2107.05580（2007 年后），可带可选的 vN 版本号；旧格式不再兼容
_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v(\d+))?")

_ARXIV_MAX_RESULTS = 2000  # arxiv API 单次请求上限
_ARXIV_ABS_URL = "https://arxiv.org/abs/{}"
_ARXIV_PDF_URL = "https://arxiv.org/pdf/{}"

_SORT_MAP = {
    "relevance": SortCriterion.Relevance,
    "submitteddate": SortCriterion.SubmittedDate,
    "lastupdateddate": SortCriterion.LastUpdatedDate,
}


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


# ─── Arxiv 抓取参数 ──────────────────────────────────────────


@REGISTRY.options.register("arxiv")
@dataclass
class ArxivOptions(FetchOptions):
    """Arxiv 数据源按次抓取参数：继承通用字段并扩展 arxiv API 特有项。"""

    # Search 相关参数（全局默认；可按 keyword 个性化覆盖）
    max_results: int = 20
    sort_by: str = "relevance"  # relevance | submittedDate | lastUpdatedDate（仅决定排序）
    sort_order: str = "descending"  # 仅支持 descending（ascending 无合理语义）

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

        排序与增量窗口的约束：
        - ``lookback_days > 0``（增量）：任意排序都追加查询内 ``submittedDate`` 区间
          服务端过滤（``_date_filter``，2026-08 实证严格 0 越界），按排序序取 Top-N；
        - ``lookback_days = 0``（历史）：不追加日期约束，按排序序直接取 max_results
          （submittedDate / lastUpdatedDate 仅决定排序，不再强制要求窗口）；
        - ``sort_order`` 仅支持 ``descending``：ascending（相关性升序=最不相关优先、
          时间升序=最旧优先）对增量/历史都没有合理语义，加载即报错。
        """
        if self.sort_by.lower() not in _SORT_MAP:
            raise ValueError(
                f"未知 sort_by: {self.sort_by!r}（可选: relevance/submittedDate/lastUpdatedDate）"
            )
        if self.sort_order.lower() != "descending":
            raise ValueError(f"sort_order 仅支持 descending（收到 {self.sort_order!r}）")
        self.sort_by = self.sort_by.lower()  # 与 sort_order 一致归一化，避免混合大小写外泄
        self.sort_order = self.sort_order.lower()  # to_list 的 SortOrder 按小写值匹配

        # 所有模式都按排序序取 Top-N（增量窗口在查询内过滤，无需整窗抓取）：
        # page_size 只需 max_results*2 的 skip 余量
        page_size = self.max_results * 2
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

        cutoff = ""
        if self.lookback_days:
            now = datetime.now(UTC).replace(second=0, microsecond=0)
            start = (now - timedelta(days=self.lookback_days)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            cutoff = f" AND submittedDate:[{start:%Y%m%d%H%M} TO {now:%Y%m%d%H%M}]"
        
        return f"{cat_part}all:{keyword}{cutoff}"

    def to_list(self) -> list[tuple[str, Search]]:
        """把 keywords（core.KeywordItem）转成 ``(keyword, Search)`` 元组列表。

        查询构造使用源级参数（max_results / sort_by / sort_order）；
        增量窗口（lookback_days>0）追加 ``submittedDate`` 区间过滤器。
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

        - ``lookback_days > 0``（增量）：查询内 ``submittedDate`` 区间服务端过滤
          （严格性已实证），按排序序（relevance=相关性 / submittedDate=时间 /
          lastUpdatedDate=更新时间）抓满 max_results 篇未跳过即停——提前停止，
          无需整窗抓取，也无需客户端截断/重排；
        - 否则（历史，``lookback_days = 0``）：一次抓取，过滤 skip 后截断
          options.max_results（不追加日期约束）。
        """
        _, search = kw  # keyword 供下游过滤使用；本实现仅用 Search（相关性/窗口已由 API 保证）
        skip_ids = options.skip_ids
        range_size = search.max_results  # to_list 恒设 max_results=page_size

        # fetch() 已建共享 Client（限流节流跨关键词生效）；直接调 _fetch 时兜底自建
        client = getattr(self, "_client", None)
        if client is None:
            client = Client(
                page_size=range_size,
                delay_seconds=options.delay_seconds,
                num_retries=options.num_retries,
            )

        def _run(search_obj: Search, offset: int) -> list[Result]:
            return list(client.results(search_obj, offset=offset))

        loop = asyncio.get_running_loop()

        papers: list[Result] = []

        # 抓取约束：如果抓取到 max_resutls（去重）或无返回结果则抓取结束
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

            for r in page_results:
                sid, _ = _extract_id(r.entry_id)
                if _is_skipped(sid, skip_ids):
                    continue
                papers.append(r)

            # 抓满 max_results 篇未跳过（提前停止），或窗口内结果不足一页（已抓完）
            if len(papers) >= options.max_results or len(page_results) < range_size:
                break
            offset += range_size

        return papers[: options.max_results]

    async def fetch(self, options: ArxivOptions) -> list[Record]:
        """构建共享 Client（限流节流跨关键词生效），再走基类模板方法。"""
        self._client = Client(
            page_size=options.page_size,
            delay_seconds=options.delay_seconds,
            num_retries=options.num_retries,
        )
        try:
            return await super().fetch(options, max_concurrent=1)
        finally:
            self._client = None

    # ── 数据源名称 ─────────────────────────────────────────────

    @property
    def source_name(self) -> str:
        return "arxiv"
