"""Arxiv API 客户端测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.network.arxiv import (
    fetch_all,
    fetch_keyword,
    search_arxiv,
    _parse_atom,
)


class TestParseAtom:
    """测试 Atom XML 解析"""

    def test_parse_basic(self, sample_atom_xml):
        papers = _parse_atom(sample_atom_xml, keyword="test")
        assert len(papers) == 2
        assert papers[0]["arxiv_id"] == "2401.00001"
        assert papers[0]["title"] == "Test-Time Adaptation with Transformers"
        assert papers[0]["keyword_match"] == "test"

    def test_parse_empty(self):
        papers = _parse_atom("", keyword="test")
        assert papers == []

    def test_parse_invalid_xml(self):
        papers = _parse_atom("not xml", keyword="test")
        assert papers == []


def _make_paginated_xml(ids, total, start=0):
    """生成包含指定论文 ID 列表的 Atom XML，用于测试分页"""
    entries = []
    for aid in ids:
        entries.append(
            "  <entry>\n"
            '    <id>http://arxiv.org/abs/{}v1</id>\n'.format(aid)
            + '    <title>Paper {}</title>\n'.format(aid)
            + '    <summary>Abstract of {}</summary>\n'.format(aid)
            + '    <published>2024-01-01T00:00:00Z</published>\n'
            + '    <updated>2024-01-05T12:00:00Z</updated>\n'
            + '    <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>\n'
            + '    <category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>\n'
            + "  </entry>"
        )
    body = "\n".join(entries)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        + '<feed xmlns="http://www.w3.org/2005/Atom"\n'
        + '      xmlns:arxiv="http://arxiv.org/schemas/atom"\n'
        + '      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">\n'
        + "  <opensearch:totalResults>{}</opensearch:totalResults>\n".format(total)
        + "  <opensearch:startIndex>{}</opensearch:startIndex>\n".format(start)
        + "  <opensearch:itemsPerPage>{}</opensearch:itemsPerPage>\n".format(len(ids))
        + body
        + "\n</feed>"
    )


class TestFetchKeyword:
    """测试单关键词查询"""

    @pytest.mark.asyncio
    async def test_incremental_mode_date_filter(self, sample_atom_xml):
        """增量模式（historical=False）：query 不含 submittedDate，客户端做日期过滤"""
        client = AsyncMock(spec=httpx.AsyncClient)
        response = MagicMock()
        response.text = sample_atom_xml
        response.raise_for_status = MagicMock()
        client.get.return_value = response

        papers = await fetch_keyword(
            client, "test-time adaptation", max_results=25, lookback_days=7, historical=False
        )

        call_args, call_kwargs = client.get.call_args
        params = call_kwargs["params"]
        assert "submittedDate" not in params["search_query"]
        assert params["sortBy"] == "submittedDate"
        assert params["sortOrder"] == "descending"
        # 样本数据 published=2024-01-01，早于 lookback_days 截止日期，被客户端过滤掉
        assert len(papers) == 0

    @pytest.mark.asyncio
    async def test_historical_mode_no_date_filter(self, sample_atom_xml):
        """历史模式（historical=True）不应包含 submittedDate 过滤，按相关性排序"""
        client = AsyncMock(spec=httpx.AsyncClient)
        xml = _make_paginated_xml(["2401.00001", "2401.00002"], total=2)
        response = MagicMock()
        response.text = xml
        response.raise_for_status = MagicMock()
        client.get.return_value = response

        papers = await fetch_keyword(
            client, "test-time adaptation", max_results=50, historical=True
        )

        call_args, call_kwargs = client.get.call_args
        params = call_kwargs["params"]
        assert "submittedDate" not in params["search_query"]
        assert params["sortBy"] == "relevance"
        # historical 模式下 page_size = min(max(max_results * 2, 500), 2000)
        assert params["max_results"] == 500
        assert len(papers) == 2

    @pytest.mark.asyncio
    async def test_historical_with_categories(self, sample_atom_xml):
        """历史模式（historical=True）+ 分类过滤"""
        client = AsyncMock(spec=httpx.AsyncClient)
        xml = _make_paginated_xml(["2401.00001"], total=1)
        response = MagicMock()
        response.text = xml
        response.raise_for_status = MagicMock()
        client.get.return_value = response

        await fetch_keyword(
            client,
            "test-time adaptation",
            arxiv_cats=["cs.CV", "cs.LG"],
            max_results=30,
            historical=True,
        )

        call_args, call_kwargs = client.get.call_args
        params = call_kwargs["params"]
        assert "submittedDate" not in params["search_query"]
        assert "cat:cs.CV" in params["search_query"]
        assert "cat:cs.LG" in params["search_query"]
        assert "test-time adaptation" in params["search_query"]
        assert params["sortBy"] == "relevance"

    @pytest.mark.asyncio
    async def test_historical_skip_ids_filters_existing(self):
        """历史模式 + skip_ids 应过滤掉已有论文"""
        client = AsyncMock(spec=httpx.AsyncClient)
        xml = _make_paginated_xml(
            ["2401.00001", "2401.00002", "2401.00003"], total=3
        )
        response = MagicMock()
        response.text = xml
        response.raise_for_status = MagicMock()
        client.get.return_value = response

        papers = await fetch_keyword(
            client, "test keyword", max_results=10, historical=True,
            skip_ids={"2401.00001", "2401.00002"},
        )

        assert len(papers) == 1
        assert papers[0]["arxiv_id"] == "2401.00003"
        # 无 totalResults 时需要一次额外请求确认耗尽
        assert client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_historical_skip_ids_triggers_pagination(self):
        """历史模式：skip_ids 导致有效结果不足时应自动分页"""
        client = AsyncMock(spec=httpx.AsyncClient)

        page1 = _make_paginated_xml(["2401.00001", "2401.00002"], total=5)
        page2 = _make_paginated_xml(["2401.00001", "2401.00003"], total=5, start=2)
        page3 = _make_paginated_xml(["2401.00004"], total=5, start=4)
        empty_page = _make_paginated_xml([], total=5, start=5)

        responses = [
            MagicMock(text=page1),
            MagicMock(text=page2),
            MagicMock(text=page3),
            MagicMock(text=empty_page),
        ]
        for r in responses:
            r.raise_for_status = MagicMock()

        client.get = AsyncMock(side_effect=responses)

        papers = await fetch_keyword(
            client, "test keyword", max_results=10, historical=True,
            skip_ids={"2401.00001", "2401.00002"},
        )

        assert len(papers) == 2
        ids = {p["arxiv_id"] for p in papers}
        assert "2401.00003" in ids
        assert "2401.00004" in ids
        assert client.get.call_count == 4

    @pytest.mark.asyncio
    async def test_historical_pagination_stops_when_enough_new(self):
        """历史模式：收集足够新论文后应停止分页"""
        client = AsyncMock(spec=httpx.AsyncClient)

        page1 = _make_paginated_xml(
            ["2401.00001", "2401.00002", "2401.00003", "2401.00004"],
            total=100,
        )
        page2 = _make_paginated_xml(
            ["2401.00005", "2401.00006", "2401.00007", "2401.00008"],
            total=100, start=4,
        )

        responses = [MagicMock(text=page1), MagicMock(text=page2)]
        for r in responses:
            r.raise_for_status = MagicMock()

        client.get = AsyncMock(side_effect=responses)

        papers = await fetch_keyword(
            client, "test keyword", max_results=3, historical=True,
            skip_ids={"2401.00001", "2401.00002", "2401.00003", "2401.00004"},
        )

        assert len(papers) == 3
        assert client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_historical_pagination_exhausts_results(self):
        """历史模式：全部结果都不足 max_results 时返回所有有效结果"""
        client = AsyncMock(spec=httpx.AsyncClient)

        page1 = _make_paginated_xml(
            ["2401.00001", "2401.00002", "2401.00003", "2401.00004", "2401.00005"],
            total=5,
        )
        response = MagicMock(text=page1)
        response.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=response)

        papers = await fetch_keyword(
            client, "test keyword", max_results=10, historical=True,
            skip_ids={
                "2401.00001", "2401.00002", "2401.00003",
                "2401.00004", "2401.00005",
            },
        )

        assert len(papers) == 0
        # 无 totalResults 时需要一次额外请求确认耗尽
        assert client.get.call_count == 2


class TestFetchAll:
    """测试多关键词并发查询"""

    @pytest.mark.asyncio
    async def test_incremental_mode(self, active_keywords, mock_settings, sample_atom_xml):
        """增量模式（historical=False）：按 submittedDate 排序，客户端做日期过滤"""
        mock_client_instance = AsyncMock(spec=httpx.AsyncClient)
        response = MagicMock()
        response.text = sample_atom_xml
        response.raise_for_status = MagicMock()
        mock_client_instance.get.return_value = response

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client_instance

            papers, total, dup = await fetch_all(
                active_keywords, mock_settings,
                max_results=25,
                historical=False,
            )

            # 样本数据 published=2024-01-01，早于 lookback_days 截止日期，被客户端过滤
            assert len(papers) == 0
            assert mock_client_instance.get.call_count >= 2
            for call in mock_client_instance.get.call_args_list:
                params = call[1]["params"]
                assert "submittedDate" not in params["search_query"]
                assert params["sortBy"] == "submittedDate"

    @pytest.mark.asyncio
    async def test_historical_mode(self, active_keywords, mock_settings, sample_atom_xml):
        """历史模式（historical=True）：所有关键词都跳过日期过滤"""
        mock_client_instance = AsyncMock(spec=httpx.AsyncClient)
        xml = _make_paginated_xml(["2401.00001", "2401.00002"], total=2)
        response = MagicMock()
        response.text = xml
        response.raise_for_status = MagicMock()
        mock_client_instance.get.return_value = response

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client_instance

            papers, total, dup = await fetch_all(
                active_keywords, mock_settings,
                max_results=50,
                historical=True,
            )

            assert len(papers) > 0
            for call in mock_client_instance.get.call_args_list:
                params = call[1]["params"]
                assert "submittedDate" not in params["search_query"]
                assert params["sortBy"] == "relevance"

    @pytest.mark.asyncio
    async def test_historical_with_skip_ids(self, active_keywords, mock_settings):
        """历史模式 + skip_ids 应在 fetch_all 去重阶段过滤"""
        mock_client_instance = AsyncMock(spec=httpx.AsyncClient)
        xml = _make_paginated_xml(["2401.00003"], total=1)
        response = MagicMock()
        response.text = xml
        response.raise_for_status = MagicMock()
        mock_client_instance.get.return_value = response

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client_instance

            papers, total, dup = await fetch_all(
                active_keywords, mock_settings,
                max_results=10,
                historical=True,
                skip_ids={"2401.00001", "2401.00002"},
            )

            assert len(papers) > 0
            assert all(p["arxiv_id"] not in ("2401.00001", "2401.00002") for p in papers)


class TestSearchArxiv:
    """测试搜索功能"""

    @pytest.mark.asyncio
    async def test_search_uses_relevance(self, sample_atom_xml):
        """搜索应按相关性排序"""
        mock_instance = MagicMock()
        response = MagicMock()
        response.text = sample_atom_xml
        response.raise_for_status = MagicMock()
        mock_instance.get = AsyncMock(return_value=response)

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_instance

            papers = await search_arxiv("test query", max_results=20)

            call_args, call_kwargs = mock_instance.get.call_args
            params = call_kwargs["params"]
            assert params["sortBy"] == "relevance"
            assert len(papers) == 2


# ─── 补充 coverage 测试 ─────────────────────────────────


class TestCoverage:
    """覆盖 arxiv.py 剩余的边缘路径"""

    def test_get_user_agent_fallback(self, mock_settings):
        """user_agent 为空时返回默认值"""
        from src.network.arxiv import _get_user_agent

        mock_settings.fetch.user_agent = ""
        result = _get_user_agent(mock_settings)
        assert result == "PaperResearch/1.0"

    def test_get_user_agent_no_fetch_attr(self):
        """settings 无 fetch 属性时返回默认值"""
        from src.network.arxiv import _get_user_agent

        result = _get_user_agent(object())
        assert result == "PaperResearch/1.0"

    @pytest.mark.asyncio
    async def test_fetch_all_keyword_error(self, active_keywords, mock_settings):
        """某个关键词抓取失败不应影响其他关键词"""
        mock_client_instance = AsyncMock(spec=httpx.AsyncClient)

        async def _mock_get(*args, **kwargs):
            if "test-time" in str(args):
                resp = MagicMock()
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "403", request=MagicMock(), response=MagicMock()
                )
                return resp
            xml = _make_paginated_xml(["2401.00001"], total=1)
            mock_resp = MagicMock()
            mock_resp.text = xml
            mock_resp.raise_for_status.return_value = None
            return mock_resp

        mock_client_instance.get.side_effect = _mock_get
        with patch("src.network.arxiv.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client_instance
            papers, total, dup = await fetch_all(
                active_keywords, mock_settings, max_results=25, historical=True,
            )
            assert len(papers) == 1

