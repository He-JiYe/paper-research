"""网络层测试：Arxiv API 解析、查询、PDF 下载"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.network.arxiv import (ARXIV_API_BASE, DEFAULT_USER_AGENT,
                               _get_user_agent, _parse_atom, download_pdf,
                               fetch_all, fetch_by_ids, fetch_keyword,
                               search_arxiv)


class TestUserAgent:
    def test_default_ua(self):
        """_get_user_agent 返回默认 UA"""
        settings = MagicMock(spec=[])
        # MagicMock without arxiv attribute → 走默认
        ua = _get_user_agent(settings)
        assert ua == DEFAULT_USER_AGENT

    def test_arxiv_user_agent_empty_string(self):
        """arxiv.user_agent 为空字符串时返回空"""
        arxiv_mock = MagicMock()
        arxiv_mock.user_agent = ""
        settings = MagicMock()
        settings.arxiv = arxiv_mock
        ua = _get_user_agent(settings)
        assert ua == ""  # 函数显式返回 arxiv.user_agent

    def test_custom_ua(self):
        """_get_user_agent 返回自定义 UA"""
        settings = MagicMock()
        settings.arxiv.user_agent = "CustomAgent/1.0"
        ua = _get_user_agent(settings)
        assert ua == "CustomAgent/1.0"


class TestParseAtom:
    def test_parse_single_entry(self):
        """_parse_atom 解析单篇论文"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>Test Title</title>
            <summary>This is an abstract.</summary>
            <author><name>Alice Zhang</name></author>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-05T00:00:00Z</updated>
            <arxiv:primary_category term="cs.CV"/>
            <category term="cs.CV"/>
            <category term="cs.LG"/>
          </entry>
        </feed>"""
        papers = _parse_atom(xml, keyword="test")
        assert len(papers) == 1
        p = papers[0]
        assert p["arxiv_id"] == "2401.00001"
        assert p["version"] == 1
        assert p["title"] == "Test Title"
        assert p["abstract"] == "This is an abstract."
        assert p["authors"] == "Alice Zhang"
        assert p["primary_category"] == "cs.CV"
        assert p["categories"] == "cs.CV, cs.LG"
        assert p["published"] == "2024-01-01"
        assert p["keyword_match"] == "test"
        assert p["url"] == "https://arxiv.org/abs/2401.00001"

    def test_parse_multiple_entries(self):
        """_parse_atom 解析多篇论文"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>Paper 1</title>
            <summary>Abstract 1</summary>
            <author><name>A</name></author>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-02T00:00:00Z</updated>
            <arxiv:primary_category term="cs.AI"/>
            <category term="cs.AI"/>
          </entry>
          <entry>
            <id>http://arxiv.org/abs/2401.00002v2</id>
            <title>Paper 2</title>
            <summary>Abstract 2</summary>
            <author><name>B</name></author>
            <author><name>C</name></author>
            <published>2024-01-03T00:00:00Z</published>
            <updated>2024-01-04T00:00:00Z</updated>
            <arxiv:primary_category term="cs.LG"/>
          </entry>
        </feed>"""
        papers = _parse_atom(xml, keyword="ml")
        assert len(papers) == 2
        assert papers[0]["arxiv_id"] == "2401.00001"
        assert papers[0]["version"] == 1
        assert papers[1]["arxiv_id"] == "2401.00002"
        assert papers[1]["version"] == 2
        assert papers[1]["authors"] == "B, C"

    def test_parse_empty_feed(self):
        """_parse_atom 处理空 feed"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
        </feed>"""
        papers = _parse_atom(xml)
        assert papers == []

    def test_parse_invalid_xml(self):
        """_parse_atom 处理无效 XML"""
        papers = _parse_atom("not xml", keyword="test")
        assert papers == []

    def test_parse_entry_without_id(self):
        """无 id 的 entry 被跳过"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <title>No ID</title>
            <summary>Abstract</summary>
          </entry>
        </feed>"""
        papers = _parse_atom(xml)
        assert papers == []

    def test_parse_authors_empty(self):
        """无作者的论文"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>No Author</title>
            <summary>Abstract</summary>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-02T00:00:00Z</updated>
            <arxiv:primary_category term="cs.AI"/>
          </entry>
        </feed>"""
        papers = _parse_atom(xml)
        assert len(papers) == 1
        assert papers[0]["authors"] == ""

    def test_parse_entry_invalid_arxiv_id(self):
        """无效 arxiv_id 的 entry 被跳过"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/invalid-id</id>
            <title>Invalid ID</title>
            <summary>Abstract</summary>
            <author><name>A</name></author>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-02T00:00:00Z</updated>
          </entry>
        </feed>"""
        papers = _parse_atom(xml)
        assert papers == []


class TestFetchKeyword:
    @pytest.mark.asyncio
    async def test_fetch_keyword_success(self):
        """fetch_keyword 正常返回论文列表"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>Test</title>
            <summary>Abstract</summary>
            <author><name>A</name></author>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-02T00:00:00Z</updated>
            <arxiv:primary_category term="cs.CV"/>
            <category term="cs.CV"/>
          </entry>
        </feed>"""

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.text = xml
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        papers = await fetch_keyword(
            mock_client, "test keyword", max_results=10, lookback_days=30
        )
        assert len(papers) == 1
        assert papers[0]["arxiv_id"] == "2401.00001"
        # 验证 API 调用参数
        call_args = mock_client.get.call_args
        assert ARXIV_API_BASE in str(call_args[0][0])
        assert "test keyword" in str(call_args)

    @pytest.mark.asyncio
    async def test_fetch_keyword_with_categories(self):
        """fetch_keyword 包含分类筛选"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
        </feed>"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.text = xml
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await fetch_keyword(
            mock_client, "test", arxiv_cats=["cs.CV", "cs.LG"], max_results=5
        )
        call_args = mock_client.get.call_args
        query_str = str(call_args[1]["params"]["search_query"])
        assert "cat:cs.CV" in query_str
        assert "cat:cs.LG" in query_str

    @pytest.mark.asyncio
    async def test_fetch_keyword_with_date_range(self):
        """fetch_keyword 使用自定义日期范围"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
        </feed>"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.text = xml
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await fetch_keyword(
            mock_client, "test", date_from="20240101000000", date_to="20240201000000"
        )
        call_args = mock_client.get.call_args
        params = call_args[1]["params"]
        assert "20240101000000" in params["search_query"]
        assert "20240201000000" in params["search_query"]

    @pytest.mark.asyncio
    async def test_fetch_keyword_with_date_from_only(self):
        """仅设置 date_from，date_to 自动补全"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
        </feed>"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.text = xml
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await fetch_keyword(mock_client, "test", date_from="20240101000000")
        call_args = mock_client.get.call_args
        params = call_args[1]["params"]
        assert "20240101000000" in params["search_query"]

    @pytest.mark.asyncio
    async def test_search_arxiv_with_categories(self):
        """search_arxiv 带分类筛选"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
        </feed>"""
        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.text = xml
            mock_response.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_response)

            papers = await search_arxiv("transformer", categories=["cs.CV", "cs.LG"])
            assert papers == []

    @pytest.mark.asyncio
    async def test_fetch_keyword_http_error(self):
        """fetch_keyword 处理 HTTP 错误"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        mock_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            await fetch_keyword(mock_client, "test")


class TestFetchAll:
    @pytest.mark.asyncio
    async def test_fetch_all_empty_keywords(self, mock_settings):
        """无活跃关键词时返回空"""
        papers, total, dup = await fetch_all([], mock_settings)
        assert papers == []
        assert total == 0
        assert dup == 0

    @pytest.mark.asyncio
    async def test_fetch_all_all_inactive(self, mock_settings):
        """所有关键词非活跃时返回空"""
        kws = [{"keyword": "test", "active": False}]
        papers, total, _dup = await fetch_all(kws, mock_settings)
        assert papers == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_fetch_all_deduplication(self, mock_settings):
        """fetch_all 对跨关键词重复论文去重"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>Same Paper</title>
            <summary>Abstract</summary>
            <author><name>A</name></author>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-02T00:00:00Z</updated>
            <arxiv:primary_category term="cs.CV"/>
            <category term="cs.CV"/>
          </entry>
        </feed>"""

        kws = [
            {"keyword": "ML", "active": True},
            {"keyword": "CV", "active": True},
        ]

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.text = xml
            mock_response.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_response)

            papers, total, dup = await fetch_all(kws, mock_settings)

        # 2 个关键词返回相同论文（但同一篇 arxiv_id），去重后为 1
        assert len(papers) == 1
        assert total == 2
        assert dup == 1

    @pytest.mark.asyncio
    async def test_fetch_all_partial_failure(self, mock_settings):
        """fetch_all 部分关键词失败不中断"""
        kws = [
            {"keyword": "good", "active": True},
            {"keyword": "bad", "active": True},
        ]

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance

            async def mock_get(url, **kwargs):
                if "bad" in str(kwargs.get("params", {})):
                    raise httpx.HTTPStatusError(
                        "Error", request=MagicMock(), response=MagicMock()
                    )
                xml = """<?xml version="1.0" encoding="utf-8"?>
                <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
                  <entry>
                    <id>http://arxiv.org/abs/2401.00001v1</id>
                    <title>Good Paper</title>
                    <summary>Abstract</summary>
                    <author><name>A</name></author>
                    <published>2024-01-01T00:00:00Z</published>
                    <updated>2024-01-02T00:00:00Z</updated>
                    <arxiv:primary_category term="cs.CV"/>
                    <category term="cs.CV"/>
                  </entry>
                </feed>"""
                resp = MagicMock()
                resp.text = xml
                resp.raise_for_status = MagicMock()
                return resp

            mock_instance.get = AsyncMock(side_effect=mock_get)

            papers, _total, _dup = await fetch_all(kws, mock_settings)
            assert len(papers) == 1
            assert papers[0]["arxiv_id"] == "2401.00001"


class TestSearchAndFetchByIds:
    @pytest.mark.asyncio
    async def test_search_arxiv(self):
        """search_arxiv 返回搜索结果"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>Search Result</title>
            <summary>Abstract</summary>
            <author><name>A</name></author>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-02T00:00:00Z</updated>
            <arxiv:primary_category term="cs.CV"/>
            <category term="cs.CV"/>
          </entry>
        </feed>"""

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.text = xml
            mock_response.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_response)

            papers = await search_arxiv("transformer", max_results=5)
            assert len(papers) == 1
            assert papers[0]["title"] == "Search Result"

    @pytest.mark.asyncio
    async def test_fetch_by_ids(self):
        """fetch_by_ids 按 ID 获取论文"""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>By ID</title>
            <summary>Abstract</summary>
            <author><name>A</name></author>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-02T00:00:00Z</updated>
            <arxiv:primary_category term="cs.CV"/>
            <category term="cs.CV"/>
          </entry>
        </feed>"""

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.text = xml
            mock_response.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_response)

            papers = await fetch_by_ids(["2401.00001"])
            assert len(papers) == 1
            assert papers[0]["arxiv_id"] == "2401.00001"

    @pytest.mark.asyncio
    async def test_search_arxiv_empty_query(self):
        """空查询返回空列表"""
        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            xml = """<?xml version="1.0" encoding="utf-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom"
                  xmlns:arxiv="http://arxiv.org/schemas/atom">
            </feed>"""
            mock_instance = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.text = xml
            mock_response.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_response)

            papers = await search_arxiv("")
            assert papers == []


class TestDownloadPdf:
    @pytest.mark.asyncio
    async def test_download_pdf_success(self, temp_dir):
        """download_pdf 下载 PDF 到指定目录"""
        pdf_content = b"%PDF-1.4 test content " * 500  # > 10KB

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.content = pdf_content
            mock_response.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_response)

            pdf_path = await download_pdf("2401.00001", temp_dir)
            assert pdf_path.exists()
            assert pdf_path.stat().st_size > 10000

    @pytest.mark.asyncio
    async def test_download_pdf_skip_exists(self, temp_dir):
        """PDF 已存在且大小合理时跳过下载"""
        pdf_dir = temp_dir / "notes" / "2401.00001"
        pdf_dir.mkdir(parents=True)
        pdf_path = pdf_dir / "paper.pdf"
        pdf_path.write_bytes(b"X" * 20000)

        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            result = await download_pdf("2401.00001", temp_dir)
            assert result == pdf_path
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_pdf_http_error(self, temp_dir):
        """下载失败时抛出异常"""
        with patch("src.network.arxiv.httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock()
            )
            mock_instance.get = AsyncMock(return_value=mock_response)

            with pytest.raises(httpx.HTTPStatusError):
                await download_pdf("2401.00001", temp_dir)
