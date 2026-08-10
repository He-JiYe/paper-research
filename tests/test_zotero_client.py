"""ZoteroClient 薄原语测试（不触网，全 mock）。

覆盖：key 提取 / 失败抛错 / 批量创建（async 组合）/ shortTitle / paper→item 转换。
"""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest
from src.zotero.client import ZoteroClient
from src.zotero.convert import paper_to_zotero_item, parse_authors


class TestParseAuthors:
    """Zotero API 要求 creator 必含 creatorType（缺失会 400 拒绝）"""

    def test_creators_have_creator_type(self):
        creators = parse_authors("Alice Zhang, Bob Li")
        assert creators == [
            {"creatorType": "author", "firstName": "Alice", "lastName": "Zhang"},
            {"creatorType": "author", "firstName": "Bob", "lastName": "Li"},
        ]

    def test_single_name(self):
        creators = parse_authors("Zhang")
        assert creators == [{"creatorType": "author", "firstName": "", "lastName": "Zhang"}]

    def test_empty(self):
        assert parse_authors("") == []


class TestCreateItemsKeyExtraction:
    """successful 的键是提交序号，真实 item key 在值的 key 字段里"""

    def test_extracts_inner_key_not_index(self, client):
        client._client.create_items.return_value = {
            "successful": {"0": {"key": "REALKEY1", "version": 1, "data": {}}},
            "unchanged": {},
            "failed": {},
        }

        keys = asyncio.run(
            client.create_items([{"source": "arxiv", "source_id": "x", "authors": "A B"}])
        )

        assert keys == ["REALKEY1"]
        # 创建后不再额外取回 item
        client._client.item.assert_not_called()

    def test_batch_returns_keys_in_order(self, client):
        client._client.create_items.return_value = {
            "successful": {"0": {"key": "K1"}, "1": {"key": "K2"}},
            "failed": {},
        }
        keys = asyncio.run(
            client.create_items(
                [
                    {"source": "arxiv", "source_id": "a", "title": "A", "authors": ""},
                    {"source": "arxiv", "source_id": "b", "title": "B", "authors": ""},
                ],
                short_titles=["SA", "SB"],
            )
        )
        assert keys == ["K1", "K2"]
        # shortTitle 直接写入 payload（参数化转换，非事后覆盖）
        payload = client._client.create_items.call_args[0][0]
        assert payload[0]["shortTitle"] == "SA"
        assert payload[1]["shortTitle"] == "SB"


class TestCreateItemsFailure:
    """create_items 被 Zotero 拒绝时必须抛错，不得拿空 key 继续"""

    def test_raises_with_zotero_error_detail(self, client):
        client._client.create_items.return_value = {
            "successful": {},
            "failed": {"0": {"code": 400, "message": "creator object must contain 'creatorType'"}},
        }
        with pytest.raises(RuntimeError, match="creatorType"):
            asyncio.run(
                client.create_items(
                    [{"source": "arxiv", "source_id": "2401.00001", "authors": "A B"}]
                )
            )
        client._client.item.assert_not_called()


@pytest.fixture
def client() -> ZoteroClient:
    """构造一个不联网的 ZoteroClient（绕过 __init__，直接塞 mock）"""
    c = ZoteroClient.__new__(ZoteroClient)
    c._client = MagicMock()
    c._lock = threading.RLock()
    return c


def _sample_paper() -> dict:
    return {
        "source": "arxiv",
        "source_id": "2501.12345",
        "title": "Multi-turn RL with Structural and Performance Aware Rewards",
        "authors": "Alice Zhang, Bob Li",
        "abstract": "We propose...",
        "url": "https://arxiv.org/abs/2501.12345",
        "categories": "cs.LG, cs.CL",
        "published": "2025-01-20",
        "keyword_match": "RL",
    }


class TestPaperToZoteroItem:
    """导入：仓库/存档ID/DOI/语言/文库编目 + date 归一化 + extra 只写引用分类"""

    def test_full_fields(self):
        item = paper_to_zotero_item(_sample_paper())
        assert item["itemType"] == "preprint"
        assert item["title"] == "Multi-turn RL with Structural and Performance Aware Rewards"
        assert item["abstractNote"] == "We propose..."
        assert item["date"] == "2025-01-20"  # YYYY-MM-DD
        assert item["url"] == "https://arxiv.org/abs/2501.12345"
        # 仓库 / 存档ID / 文库编目 / 语言
        assert item["repository"] == "arxiv"
        assert item["archiveID"] == "arxiv:2501.12345"
        assert item["libraryCatalog"] == "arxiv"
        assert item["language"] == "en"
        assert item["shortTitle"] == ""
        # 无 raw_data → 不写 DOI、不写 extra（引用分类来自 raw_data.citation）
        assert "DOI" not in item
        assert "extra" not in item

    def test_citation_from_raw_data(self):
        """extra 只写引用格式：raw_data.citation（如 arXiv:{id} [主分类]）"""
        paper = dict(_sample_paper(), raw_data={"citation": "arXiv:2501.12345 [cs.RO]"})
        item = paper_to_zotero_item(paper)
        assert item["extra"] == "arXiv:2501.12345 [cs.RO]"

    def test_citation_source_agnostic(self):
        """extra 不依赖硬编码 source：任何源的 citation 原样写入（引用知识在 network 层）"""
        paper = dict(
            _sample_paper(),
            source="pubmed",
            source_id="12345",
            raw_data={"citation": "PMID:12345"},
        )
        item = paper_to_zotero_item(paper)
        assert item["extra"] == "PMID:12345"
        assert item["archiveID"] == "pubmed:12345"

    def test_no_extra_when_no_citation(self):
        """raw_data 无 citation 时不写 extra"""
        item = paper_to_zotero_item(dict(_sample_paper(), raw_data={"v": 1}))
        assert "extra" not in item

    def test_date_normalized_from_iso(self):
        """published 为完整 ISO 时归一化为 YYYY-MM-DD"""
        paper = dict(_sample_paper(), published="2025-01-20T08:00:00+00:00")
        item = paper_to_zotero_item(paper)
        assert item["date"] == "2025-01-20"

    def test_doi_extracted_from_raw_data(self):
        """DOI 从 raw_data（dict / JSON 字符串）提取"""
        paper = dict(_sample_paper(), raw_data={"doi": "10.48550/arXiv.2501.12345"})
        assert paper_to_zotero_item(paper)["DOI"] == "10.48550/arXiv.2501.12345"
        import json as _json

        paper2 = dict(_sample_paper(), raw_data=_json.dumps({"doi": "10.48550/arXiv.9999.99999"}))
        assert paper_to_zotero_item(paper2)["DOI"] == "10.48550/arXiv.9999.99999"
        # raw_data 无 doi → 不写 DOI
        assert "DOI" not in paper_to_zotero_item(dict(_sample_paper(), raw_data={"v": 1}))

    def test_parametrized_short_title_collection(self):
        """short_title/collection 参数化写入；extra 只写 citation（不含 pdf_url）"""
        paper = dict(_sample_paper(), raw_data={"citation": "arXiv:2501.12345"})
        item = paper_to_zotero_item(paper, short_title="RL-2025-Mult", collection_key="C0000001")
        assert item["shortTitle"] == "RL-2025-Mult"
        assert item["collections"] == ["C0000001"]
        assert item["extra"] == "arXiv:2501.12345"

    def test_extra_only_citation(self):
        """extra 不再承载 source / source_id / shortTitle / pdf_url（只写引用分类）"""
        paper = dict(_sample_paper(), pdf_url="http://x.pdf", raw_data={"citation": "arXiv:1"})
        item = paper_to_zotero_item(paper, short_title="ST")
        assert item["extra"] == "arXiv:1"
        assert "pdf_url" not in item["extra"]

    def test_tags_only_sid(self):
        """标签只保留 sid:{source}:{id}（用于反查/去重）"""
        item = paper_to_zotero_item(_sample_paper())
        tags = {t["tag"] for t in item["tags"]}
        assert tags == {"sid:arxiv:2501.12345"}
