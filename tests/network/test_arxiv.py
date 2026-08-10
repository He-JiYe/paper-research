"""Arxiv API 客户端测试（基于 arxiv 库）"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import arxiv
import pytest
from arxiv import SortCriterion
from src.core.models import KeywordItem
from src.core.text import relevance_score
from src.network.source.arxiv import AVG_PER_DAY, ArxivOptions, ArxivSource

# ─── 辅助：构建 mock Result ──────────────────────────────


def _kw(*specs) -> list[KeywordItem]:
    """把 str / dict 规格转成 KeywordItem（测试用；生产契约即 KeywordItem）。"""
    out = []
    for s in specs:
        if isinstance(s, str):
            out.append(KeywordItem(keyword=s))
        else:
            out.append(
                KeywordItem(
                    keyword=s.get("keyword", ""),
                    categories=s.get("categories") or [],
                    active=s.get("active", True),
                )
            )
    return out


def _make_mock_result(
    arxiv_id: str = "2401.00001",
    title: str = "Test-Time Adaptation with Transformers",
    summary: str = "We propose a novel method for test-time adaptation.",
    authors: list[str] | None = None,
    published: str = "2024-01-01",
    primary_cat: str = "cs.LG",
    categories: list[str] | None = None,
    version: int = 1,
) -> MagicMock:
    """创建一个模拟的 arxiv.Result 对象。"""
    if authors is None:
        authors = ["Alice Zhang", "Bob Li"]
    if categories is None:
        categories = ["cs.LG", "cs.CV"]

    result = MagicMock(spec=arxiv.Result)
    result.entry_id = f"http://arxiv.org/abs/{arxiv_id}v{version}"
    result.title = title
    result.summary = summary
    result.authors = [MagicMock(name=a, spec=arxiv.Result.Author) for a in authors]
    for a_mock, a_name in zip(result.authors, authors):
        a_mock.name = a_name
    result.published = (
        datetime.fromisoformat(published).replace(tzinfo=UTC)
        if "T" in published
        else datetime.strptime(published, "%Y-%m-%d").replace(tzinfo=UTC)
    )
    result.updated = result.published
    result.primary_category = primary_cat
    result.categories = categories
    result.pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    result.doi = None
    result.journal_ref = None
    result.comment = None
    return result


class TestAdapt:
    """测试 adapt（arxiv.Result → Record）"""

    @pytest.mark.asyncio
    async def test_basic_conversion(self):
        result = _make_mock_result()
        recs = await ArxivSource().adapt([result])
        rec = recs[0]
        assert rec.source_id == "2401.00001"
        assert rec.source == "arxiv"
        assert rec.title == "Test-Time Adaptation with Transformers"
        assert rec.authors == ["Alice Zhang", "Bob Li"]
        assert rec.categories == ["cs.LG", "cs.CV"]
        # keyword_match 由 BaseSource._try_fetch 回填，adapt 本身留空
        assert rec.keyword_match == ""
        assert rec.raw_data["version"] == 1
        assert rec.raw_data["primary_category"] == "cs.LG"
        assert rec.published.startswith("2024-01-01")  # ISO 格式

    @pytest.mark.asyncio
    async def test_empty_result(self):
        assert await ArxivSource().adapt([]) == []

    @pytest.mark.asyncio
    async def test_entry_id_without_version(self):
        """entry_id 无 vN 后缀不再崩溃（int(None)），版本号缺省为 1"""
        result = _make_mock_result("2401.00001")
        result.entry_id = "http://arxiv.org/abs/2401.00001"  # 无版本号
        recs = await ArxivSource().adapt([result])
        assert recs[0].source_id == "2401.00001"
        assert recs[0].raw_data["version"] == 1

class TestBuildQuery:
    """测试 ArxivOptions.build_query（共享 KeywordItem → 查询串）"""

    def _build(self, keyword, categories=None):
        return ArxivOptions().build_query(keyword, categories)

    def test_keyword_only(self):
        assert self._build("test-time adaptation") == "all:test-time adaptation"

    def test_with_categories(self):
        query = self._build("test-time adaptation", ["cs.CV", "cs.LG"])
        assert "cat:cs.CV" in query
        assert "cat:cs.LG" in query
        assert "AND" in query
        assert "all:test-time adaptation" in query

    def test_keyword_with_not(self):
        query = self._build("vision-language model NOT detection", ["cs.CV"])
        assert "NOT" in query


class TestToList:
    """测试 to_list：全源共享 keyword（str/dict 归一化），查询参数一律用源级"""

    def test_shared_keywords_use_source_params(self):
        opts = ArxivOptions(
            keywords=_kw(
                {"keyword": "a", "max_results": 5, "sort_by": "submittedDate"},  # 每词自定义被忽略
                {"keyword": "b"},  # 只用源级参数
            ),
            max_results=20,
            sort_by="relevance",
        )
        lst = opts.to_list()
        assert len(lst) == 2
        # 相关性排序：请求 page_size（含 2×max_results 缓冲，过滤 skip 后仍够截断）
        expected_max = opts.page_size
        assert lst[0][0] == "a"
        assert lst[1][0] == "b"
        for _, search in lst:
            assert search.max_results == expected_max
            assert search.sort_by == SortCriterion.Relevance  # 一律源级 sort_by

    def test_post_init_page_size(self):
        """__post_init__ 按模式计算 page_size（历史 max_results*2，增量覆盖窗口）"""
        # 历史（相关性排序强制 lookback=0）：至少 max_results*2
        opts = ArxivOptions(max_results=50, page_size=10)
        assert opts.page_size == min(max(10, 50 * 2), 2000)
        # 增量（时间排序）：至少 (lookback+1)*AVG_PER_DAY
        opts = ArxivOptions(
            max_results=50, page_size=10, sort_by="lastUpdatedDate", lookback_days=3
        )
        assert opts.page_size == min(max(10, 4 * AVG_PER_DAY), 2000)

    def test_invalid_sort_by_raises(self):
        """非法 sort_by 加载即报错（fail-fast，避免 to_list 才 KeyError）"""
        with pytest.raises(ValueError, match="sort_by"):
            ArxivOptions(sort_by="lastupdated")  # 漏写 date

    def test_invalid_sort_order_raises(self):
        """非法 sort_order 加载即报错"""
        with pytest.raises(ValueError, match="sort_order"):
            ArxivOptions(sort_order="sideways")

    def test_sort_order_normalized_lower(self):
        """sort_order 归一化为小写（SortOrder 按小写值匹配）"""
        assert ArxivOptions(sort_order="DESCENDING").sort_order == "descending"

    def test_valid_sort_params_accept_any_case(self):
        """合法 sort_by 大小写不敏感（config 用 lastUpdatedDate，测试用 submittedDate）"""
        ArxivOptions(sort_by="lastUpdatedDate", lookback_days=1)
        ArxivOptions(sort_by="submittedDate", lookback_days=1)
        ArxivOptions(sort_by="Relevance")
        ArxivOptions(sort_order="Ascending")

    def test_relevance_forces_lookback_zero(self):
        """相关性排序无法做时间窗口截断，强制 lookback_days=0（消除窗口静默截断）"""
        opts = ArxivOptions(sort_by="relevance", lookback_days=7)
        assert opts.lookback_days == 0

    def test_time_sort_requires_lookback(self):
        """时间排序必须携带增量窗口（lookback_days>0），否则加载即报错（fail-fast）"""
        with pytest.raises(ValueError, match="lookback_days"):
            ArxivOptions(sort_by="lastUpdatedDate")  # lookback 默认 0


class TestSingleFetch:
    """测试单关键词 _fetch（(keyword, Search) 元组 + options 双参数）"""

    @pytest.mark.asyncio
    async def test_incremental_mode(self):
        """增量模式：过滤超出 lookback_days 的论文"""
        mock_results = [
            _make_mock_result("2401.00001", published="2024-01-01"),
            _make_mock_result("2401.00002", published="2024-01-02"),
        ]
        opts = ArxivOptions(
            keywords=_kw("test"),
            max_results=25,
            lookback_days=7,
            sort_by="lastUpdatedDate",
        )

        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = mock_results
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

            # 样本 published=2024-01-01，远早于 7 天内（当前时间约为 2026 年），所以被过滤
            assert len(papers) == 0

    @pytest.mark.asyncio
    async def test_historical_mode(self):
        """历史模式：不过滤日期"""
        mock_results = [
            _make_mock_result("2401.00001"),
            _make_mock_result("2401.00002"),
        ]
        opts = ArxivOptions(keywords=_kw("test"), max_results=50, sort_by="relevance")

        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = mock_results
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

            assert len(papers) == 2

    @pytest.mark.asyncio
    async def test_skip_ids(self):
        """offset（skip_ids）过滤已有论文"""
        mock_results = [
            _make_mock_result("2401.00001"),
            _make_mock_result("2401.00002"),
            _make_mock_result("2401.00003"),
        ]
        opts = ArxivOptions(
            keywords=_kw("test"),
            max_results=10,
            sort_by="relevance",
            skip_ids={"2401.00001", "2401.00002"},
        )

        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = mock_results
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

            assert len(papers) == 1
            assert "2401.00003" in papers[0].entry_id


class TestFetch:
    """测试多关键词 fetch 编排（基类模板：to_list + _try_fetch）"""

    @pytest.mark.asyncio
    async def test_fetch_all(self):
        """多关键词并发抓取，keyword_match 回填各自关键词"""
        mock_results_1 = [_make_mock_result("2401.00001")]
        mock_results_2 = [_make_mock_result("2401.00002")]

        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.side_effect = [mock_results_1, mock_results_2]
            mock_client_cls.return_value = mock_client

            records = await ArxivSource().fetch(
                ArxivOptions(
                    keywords=_kw({"keyword": "a"}, {"keyword": "b", "categories": ["cs.LG"]}),
                    max_results=50,
                    sort_by="relevance",
                )
            )

            assert len(records) == 2
            kw_map = {r.source_id: r.keyword_match for r in records}
            assert kw_map["2401.00001"] == "a"
            assert kw_map["2401.00002"] == "b"

    @pytest.mark.asyncio
    async def test_no_dedup(self):
        """不去重：跨关键词重复论文各自保留"""
        mock_results_1 = [_make_mock_result("2401.00001")]
        mock_results_2 = [_make_mock_result("2401.00001"), _make_mock_result("2401.00002")]

        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.side_effect = [mock_results_1, mock_results_2]
            mock_client_cls.return_value = mock_client

            records = await ArxivSource().fetch(
                ArxivOptions(keywords=_kw("a", "b"), max_results=50, sort_by="relevance"),
            )

            assert len(records) == 3  # 2401.00001 出现两次（不去重）


class TestIncrementalPagination:
    """增量模式：分页抓完 lookback 窗口，按相关性重排后截断"""

    @pytest.mark.asyncio
    async def test_stops_at_window_end(self):
        """第 1 页内出现超窗 → 停止，只留窗口内"""
        in_window = [
            _make_mock_result("2401.00001", published="2026-08-09"),
            _make_mock_result("2401.00002", published="2026-08-08"),
        ]
        past = _make_mock_result("2401.00003", published="2026-01-01")
        opts = ArxivOptions(
            keywords=_kw("test"), max_results=50, lookback_days=7, sort_by="lastUpdatedDate"
        )
        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = in_window + [past]
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

        assert {r.entry_id for r in papers} == {r.entry_id for r in in_window}
        assert mock_client.results.call_count == 1  # 首页即遇超窗，不再翻页

    @pytest.mark.asyncio
    async def test_requests_next_page_when_window_not_exceeded(self):
        """第 1 页满 range_size 且全在窗口 → offset 增加翻第 2 页，抓全窗口"""
        opts = ArxivOptions(
            keywords=_kw("test"), max_results=1000, lookback_days=7, sort_by="lastUpdatedDate"
        )
        range_size = opts.to_list()[0][1].max_results  # = min(8*AVG_PER_DAY, 2000)
        page1 = [
            _make_mock_result(f"2401.{i:05d}", published="2026-08-09")
            for i in range(1, range_size + 1)
        ]
        page2 = [
            _make_mock_result("2501.00001", published="2026-08-08"),
            _make_mock_result("2501.00002", published="2026-01-01"),
        ]
        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.side_effect = [page1, page2]
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

        # 第 1 页全在窗口 → 翻第 2 页抓完窗口
        assert mock_client.results.call_count == 2
        assert len(papers) == range_size + 1

    @pytest.mark.asyncio
    async def test_truncates_to_max_results_after_relevance_sort(self):
        """窗口内多于 max_results → 按相关性重排后截断"""
        opts = ArxivOptions(
            keywords=_kw("test-time adaptation"),
            max_results=1,
            lookback_days=7,
            sort_by="lastUpdatedDate",
        )
        matched = _make_mock_result(
            "2401.00001", title="test-time adaptation for LLMs", published="2026-08-09"
        )
        unrelated = _make_mock_result("2401.00002", title="unrelated title", published="2026-08-08")
        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = [unrelated, matched]  # API 返回序：unrelated 在前
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

        assert len(papers) == 1
        assert papers[0].entry_id == matched.entry_id  # 相关性重排后 matched 胜出


class TestRelevanceSortKey:
    """测试相关性排序函数"""

    def test_basic_matching(self):
        paper = {
            "title": "Test-Time Adaptation with Transformers",
            "abstract": "A method for test-time adaptation in vision tasks",
        }
        score = relevance_score(paper, "test-time adaptation")
        assert score > 0
        assert score <= 1.0

    def test_no_match(self):
        paper = {"title": "Image Classification", "abstract": "A classification method"}
        score = relevance_score(paper, "reinforcement learning")
        assert score == 0.0

    def test_empty_keyword(self):
        paper = {"title": "Any Paper", "abstract": "Any abstract"}
        score = relevance_score(paper, "")
        assert score == 0.0

    def test_title_weight_higher(self):
        title_match = relevance_score(
            {
                "title": "test-time adaptation for neural networks",
                "abstract": "some unrelated text",
            },
            "test-time adaptation",
        )
        abstract_match = relevance_score(
            {"title": "unrelated title", "abstract": "a test-time adaptation method"},
            "test-time adaptation",
        )
        assert title_match > abstract_match
