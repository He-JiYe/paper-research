"""Arxiv API 客户端测试（基于 arxiv 库）"""

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import arxiv
import pytest
from arxiv import SortCriterion
from src.core.models import KeywordItem
from src.core.text import relevance_score
from src.network.source.arxiv import ArxivOptions, ArxivSource

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
        # 空格分隔的未编码查询（arxiv 库内部负责 urlencode；+OR+/+AND+ 会被二次编码成字面量 +）
        query = self._build("test-time adaptation", ["cs.CV", "cs.LG"])
        assert query == "(cat:cs.CV OR cat:cs.LG) AND all:test-time adaptation"

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
        """__post_init__：所有模式都按排序序取 Top-N，page_size = max(默认, max_results*2)"""
        # 历史（relevance，lookback=0）
        opts = ArxivOptions(max_results=50, page_size=10)
        assert opts.page_size == min(max(10, 50 * 2), 2000)
        # 增量（relevance + lookback）
        opts = ArxivOptions(max_results=50, page_size=10, sort_by="relevance", lookback_days=3)
        assert opts.page_size == min(max(10, 50 * 2), 2000)
        # 增量（submittedDate + lookback）：同样 max_results*2（查询内窗口，无需整窗预估）
        opts = ArxivOptions(max_results=50, page_size=10, sort_by="submittedDate", lookback_days=3)
        assert opts.page_size == min(max(10, 50 * 2), 2000)

    def test_invalid_sort_by_raises(self):
        """非法 sort_by 加载即报错（fail-fast，避免 to_list 才 KeyError）"""
        with pytest.raises(ValueError, match="sort_by"):
            ArxivOptions(sort_by="lastupdated")  # 漏写 date

    def test_invalid_sort_order_raises(self):
        """非法/ascending sort_order 加载即报错（ascending 无合理增量语义）"""
        with pytest.raises(ValueError, match="sort_order"):
            ArxivOptions(sort_order="sideways")
        with pytest.raises(ValueError, match="sort_order"):
            ArxivOptions(sort_order="ascending")
        with pytest.raises(ValueError, match="sort_order"):
            ArxivOptions(sort_order="Ascending")

    def test_sort_order_normalized_lower(self):
        """sort_order 归一化为小写（SortOrder 按小写值匹配）"""
        assert ArxivOptions(sort_order="DESCENDING").sort_order == "descending"

    def test_valid_sort_params_accept_any_case(self):
        """合法 sort_by / sort_order 大小写不敏感（config 用 submittedDate，测试用小写）"""
        ArxivOptions(sort_by="submittedDate", lookback_days=1)
        ArxivOptions(sort_by="lastUpdatedDate", lookback_days=1)
        ArxivOptions(sort_by="Relevance")
        ArxivOptions(sort_by="SUBMITTEDDATE", lookback_days=1)
        ArxivOptions(sort_order="DESCENDING")

    def test_relevance_allows_lookback(self):
        """相关性排序 + 增量窗口：查询内 submittedDate 服务端过滤（不再强制 lookback=0）"""
        opts = ArxivOptions(sort_by="relevance", lookback_days=7)
        assert opts.lookback_days == 7

    def test_time_sorts_allow_zero_lookback(self):
        """时间排序 + lookback=0（历史模式）：合法，不追加日期约束，直接取 max_results"""
        opts = ArxivOptions(sort_by="submittedDate")
        assert opts.lookback_days == 0
        assert opts._date_filter() == ""
        opts2 = ArxivOptions(sort_by="lastUpdatedDate")
        assert opts2._date_filter() == ""

    def test_ascending_rejected_for_all_sorts(self):
        """ascending 对所有排序都无合理增量语义，加载即报错"""
        with pytest.raises(ValueError, match="sort_order"):
            ArxivOptions(sort_by="submittedDate", lookback_days=3, sort_order="ascending")
        with pytest.raises(ValueError, match="sort_order"):
            ArxivOptions(sort_by="relevance", sort_order="ascending")

    # ── 查询内日期窗口（_date_filter / to_list 拼装）────────────

    def test_no_date_filter_historical(self):
        """历史（lookback=0）：不追加日期过滤器"""
        assert ArxivOptions(sort_by="relevance")._date_filter() == ""

    def test_date_filter_relevance_windowed(self):
        """relevance + lookback>0：追加 submittedDate 区间（YYYYMMDDHHMM 分钟粒度）"""
        f = ArxivOptions(sort_by="relevance", lookback_days=7)._date_filter()
        assert re.fullmatch(r" AND submittedDate:\[\d{12} TO \d{12}\]", f)

    def test_date_filter_submitteddate_windowed(self):
        """submittedDate 排序 + lookback>0：同样追加 submittedDate 区间"""
        f = ArxivOptions(sort_by="submittedDate", lookback_days=3)._date_filter()
        assert re.fullmatch(r" AND submittedDate:\[\d{12} TO \d{12}\]", f)

    def test_date_filter_lastupdateddate_windowed(self):
        """lastUpdatedDate + lookback>0：同样追加 submittedDate 区间（API 唯一日期过滤器，
        排序只决定窗口内顺序）"""
        f = ArxivOptions(sort_by="lastUpdatedDate", lookback_days=3)._date_filter()
        assert re.fullmatch(r" AND submittedDate:\[\d{12} TO \d{12}\]", f)

    def test_to_list_appends_date_filter_for_windowed_sorts(self):
        """to_list 对增量（lookback>0）把日期过滤器拼进查询串；历史（lookback=0）不拼"""
        opts = ArxivOptions(keywords=_kw("test"), sort_by="relevance", lookback_days=7)
        query = opts.to_list()[0][1].query
        assert re.search(r" AND submittedDate:\[\d{12} TO \d{12}\]$", query)
        opts2 = ArxivOptions(keywords=_kw("test"), sort_by="relevance")
        assert "submittedDate" not in opts2.to_list()[0][1].query
        opts3 = ArxivOptions(keywords=_kw("test"), sort_by="lastUpdatedDate", lookback_days=7)
        assert re.search(r" AND submittedDate:\[\d{12} TO \d{12}\]$", opts3.to_list()[0][1].query)


class TestSingleFetch:
    """测试单关键词 _fetch（(keyword, Search) 元组 + options 双参数）"""

    @pytest.mark.asyncio
    async def test_incremental_mode_no_client_cutoff(self):
        """增量模式：窗口在查询内（submittedDate 服务端过滤），客户端不再按日期截断，
        旧发布样本也原样透传（窗口保证由 API 负责）"""
        mock_results = [
            _make_mock_result("2401.00001", published="2024-01-01"),
            _make_mock_result("2401.00002", published="2024-01-02"),
        ]
        opts = ArxivOptions(
            keywords=_kw("test"),
            max_results=25,
            lookback_days=7,
            sort_by="submittedDate",
        )

        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = mock_results
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

            # 查询串带日期窗口；样本全部透传（无客户端截断），不足一页即停
            assert "submittedDate" in opts.to_list()[0][1].query
            assert len(papers) == 2

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
    async def test_historical_time_sort(self):
        """历史模式 + 时间排序（lookback=0）：合法，不追加日期约束，按排序序取 max_results"""
        mock_results = [
            _make_mock_result("2401.00001"),
            _make_mock_result("2401.00002"),
        ]
        opts = ArxivOptions(keywords=_kw("test"), max_results=50, sort_by="submittedDate")

        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = mock_results
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

            assert "submittedDate" not in opts.to_list()[0][1].query  # 无窗口约束
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
        """多关键词抓取（共享 Client + 错峰启动），keyword_match 回填各自关键词"""
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
                    delay_seconds=0,  # 测试免错峰等待
                )
            )

            assert len(records) == 2
            kw_map = {r.source_id: r.keyword_match for r in records}
            assert kw_map["2401.00001"] == "a"
            assert kw_map["2401.00002"] == "b"
            # 共享同一个 Client（限流节流跨关键词生效）
            assert mock_client_cls.call_count == 1

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
                ArxivOptions(
                    keywords=_kw("a", "b"),
                    max_results=50,
                    sort_by="relevance",
                    delay_seconds=0,
                )
            )

            assert len(records) == 3  # 2401.00001 出现两次（不去重）


class TestIncrementalPagination:
    """增量模式：查询内窗口（服务端过滤），按排序序抓满 max_results 篇未跳过即停"""

    @staticmethod
    def _days_ago(days: int) -> str:
        """相对今天的日期（防硬编码日期越过窗口后测试失效）。"""
        return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")

    @pytest.mark.asyncio
    async def test_stops_when_page_short(self):
        """窗口内结果不足一页 → 停止翻页（超窗样本由服务端过滤，客户端不截断）"""
        page1 = [
            _make_mock_result("2401.00001", published=self._days_ago(1)),
            _make_mock_result("2401.00002", published=self._days_ago(2)),
            _make_mock_result("2401.00003", published=self._days_ago(30)),
        ]
        opts = ArxivOptions(
            keywords=_kw("test"), max_results=50, lookback_days=7, sort_by="submittedDate"
        )
        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = page1
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

        assert len(papers) == 3
        assert mock_client.results.call_count == 1  # 不足一页即停，不再翻页

    @pytest.mark.asyncio
    async def test_requests_next_page_when_not_filled(self):
        """首页未抓满 max_results（且页满）→ 翻第 2 页凑满后截断"""
        opts = ArxivOptions(
            keywords=_kw("test"), max_results=2500, lookback_days=7, sort_by="submittedDate"
        )
        range_size = opts.to_list()[0][1].max_results  # min(max(100, 5000), 2000) = 2000
        page1 = [
            _make_mock_result(f"2401.{i:05d}", published=self._days_ago(1))
            for i in range(1, range_size + 1)
        ]
        page2 = [
            _make_mock_result(f"2501.{i:05d}", published=self._days_ago(1)) for i in range(1, 601)
        ]
        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.side_effect = [page1, page2]
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

        # 第 1 页 2000 篇未凑满 2500 → 翻第 2 页，凑满后截断
        assert mock_client.results.call_count == 2
        assert len(papers) == 2500

    @pytest.mark.asyncio
    async def test_relevance_windowed_stops_after_max_results(self):
        """relevance + lookback：API 已按相关性降序，抓满 max_results 篇未跳过即停
        （不抓完整窗口 —— 增量路径主要提速点；超窗样本由服务端过滤，客户端不再截断）"""
        opts = ArxivOptions(
            keywords=_kw("test"), max_results=2, lookback_days=7, sort_by="relevance"
        )
        range_size = opts.to_list()[0][1].max_results  # relevance → max_results*2 = 4
        page1 = [
            _make_mock_result(f"2401.{i:05d}", published=self._days_ago(1))
            for i in range(1, range_size + 1)
        ]
        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = page1
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

        # 查询串已带日期窗口
        assert "submittedDate" in opts.to_list()[0][1].query
        assert mock_client.results.call_count == 1  # 首页即抓满，不再翻页
        assert len(papers) == 2

    @pytest.mark.asyncio
    async def test_relevance_windowed_pages_past_skipped(self):
        """skip 覆盖整个首页 → 继续翻页直到 max_results 篇未跳过"""
        opts = ArxivOptions(
            keywords=_kw("test"),
            max_results=3,
            lookback_days=7,
            sort_by="relevance",
        )
        range_size = opts.to_list()[0][1].max_results  # max(默认100, 3*2) = 100
        page1 = [
            _make_mock_result(f"2401.{i:05d}", published=self._days_ago(1))
            for i in range(1, range_size + 1)
        ]
        page2 = [
            _make_mock_result(f"2501.{i:05d}", published=self._days_ago(1)) for i in range(1, 4)
        ]
        opts.skip_ids = {f"2401.{i:05d}" for i in range(1, range_size + 1)}  # 首页全部已入库
        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.side_effect = [page1, page2]
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

        # 首页 100 篇全部 skip → 翻第 2 页凑满 3 篇未跳过
        assert mock_client.results.call_count == 2
        assert len(papers) == 3
        assert all(p.entry_id.startswith("http://arxiv.org/abs/2501.") for p in papers)

    @pytest.mark.asyncio
    async def test_lastupdateddate_windowed_stops_after_max_results(self):
        """lastUpdatedDate + lookback：查询带 submittedDate 窗口（API 唯一日期过滤器），
        按更新时间排序抓满即停"""
        opts = ArxivOptions(
            keywords=_kw("test"), max_results=2, lookback_days=7, sort_by="lastUpdatedDate"
        )
        range_size = opts.to_list()[0][1].max_results  # max(默认100, 2*2) = 100
        page1 = [
            _make_mock_result(f"2401.{i:05d}", published=self._days_ago(1))
            for i in range(1, range_size + 1)
        ]
        with patch("src.network.source.arxiv.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.results.return_value = page1
            mock_client_cls.return_value = mock_client

            papers = await ArxivSource()._fetch(opts.to_list()[0], opts)

        assert re.search(r" AND submittedDate:\[\d{12} TO \d{12}\]$", opts.to_list()[0][1].query)
        assert mock_client.results.call_count == 1
        assert len(papers) == 2


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
