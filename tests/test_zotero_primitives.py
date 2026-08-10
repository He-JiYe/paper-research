"""ZoteroClient 薄原语集成测试：用 FakeZotero 模拟 pyzotero，不触真实 API。

覆盖：list_collections / create_collection / create_items（async 组合：自动
find-or-create 分类）。
（PDF 附件功能已移除：本机 Zotero connector 只读，不支持本地添加附件。）
"""

import asyncio
from unittest.mock import MagicMock

import pytest
import src.zotero.client as client_mod
from src.zotero.client import ZoteroClient

from tests._fake_zotero import FakeZotero


@pytest.fixture
def zc(monkeypatch):
    monkeypatch.setattr(client_mod, "PyzoteroClient", FakeZotero)
    return ZoteroClient("key", 1, "user")


def _papers():
    return [
        {
            "source": "arxiv",
            "source_id": "x1",
            "title": "Title One",
            "authors": "Alice Zhang",
            "abstract": "abs",
            "published": "2024-01-01",
            "url": "u1",
        },
        {
            "source": "arxiv",
            "source_id": "x2",
            "title": "Title Two",
            "authors": "Bob Li",
            "abstract": "abs2",
            "published": "2024-02-01",
            "url": "u2",
        },
    ]


class TestListCollections:
    def test_empty_library(self, zc):
        assert zc.list_collections() == []

    def test_builds_paths_and_sort(self, zc):
        a = zc.create_collection("A")
        b = zc.create_collection("B", a)
        zc.create_collection("C", b)
        paths = [c["path"] for c in zc.list_collections()]
        assert paths == ["A", "A / B", "A / B / C"]


class TestCreateCollection:
    def test_creates_top_level(self, zc):
        key = zc.create_collection("New")
        assert isinstance(key, str)
        assert key in zc._client._colls

    def test_creates_with_parent(self, zc):
        parent = zc.create_collection("Parent")
        child = zc.create_collection("Child", parent)
        assert zc._client._colls[child]["parent"] == parent


class TestCreateItems:
    def test_batch_with_short_title_and_collection(self, zc):
        c_key = zc.create_collection("A")
        keys = asyncio.run(
            zc.create_items(_papers(), short_titles=["T1", "T2"], collection_keys=[c_key, c_key])
        )
        assert len(keys) == 2
        for k, sid, st in zip(keys, ["x1", "x2"], ["T1", "T2"], strict=True):
            d = zc._client._items[k]["data"]
            assert d["shortTitle"] == st
            assert c_key in d["collections"]
            tags = {t["tag"] for t in d["tags"]}
            assert f"sid:arxiv:{sid}" in tags

    def test_find_item_by_sid_after_create(self, zc):
        keys = asyncio.run(zc.create_items(_papers()[:1]))
        found = zc.get_item("arxiv", "x1")
        assert found is not None
        assert found["key"] == keys[0]

    def test_create_items_empty_returns_empty(self, zc):
        assert asyncio.run(zc.create_items([])) == []

    def test_create_items_over_limit_raises(self, zc):
        many = [dict(_papers()[0]) for _ in range(51)]
        with pytest.raises(ValueError):
            asyncio.run(zc.create_items(many))

    def test_create_items_batch_failure_raises(self, zc):
        zc._client.create_items = MagicMock(side_effect=RuntimeError("api down"))
        with pytest.raises(RuntimeError):
            asyncio.run(zc.create_items(_papers()[:1]))


class TestCreateItemsAuto:
    """create_items 自动组合：find-or-create 分类"""

    def test_auto_creates_collection(self, zc):
        papers = _papers()[:1]
        keys = asyncio.run(
            zc.create_items(papers, short_titles=["T"], collection_keys=["A / B / C"])
        )
        assert len(zc._client._colls) == 3  # A、A / B、A / B / C 逐级创建
        it = zc._client._items[keys[0]]
        abc_key = next(c["key"] for c in zc.list_collections() if c["path"] == "A / B / C")
        assert abc_key in it["data"]["collections"]
        assert it["data"]["shortTitle"] == "T"


class TestEnsureCollection:
    """ensure_collection：8 位 base62 key 直接返回，路径 find-or-create"""

    def test_base62_key_returned_as_is(self, zc):
        """含非 hex 字母的 8 位 base62 key 直接返回，不新建 collection"""
        key = "KMNQRXYZ"  # 含 K/M/N/Q/R/X/Y/Z——旧 hex 判定会误判为路径
        assert zc.ensure_collection(key) == key
        assert len(zc._client._colls) == 0

    def test_hex_key_still_recognized(self, zc):
        """纯 hex key 仍识别（hex 是 base62 子集）"""
        key = "C0000001"
        assert zc.ensure_collection(key) == key
        assert len(zc._client._colls) == 0

    def test_path_find_or_create(self, zc):
        """路径 "A / B / C" 仍逐级 find-or-create"""
        key = zc.ensure_collection("A / B / C")
        assert len(zc._client._colls) == 3
        coll = next(c for c in zc.list_collections() if c["path"] == "A / B / C")
        assert coll["key"] == key

    def test_none_or_blank_returns_none(self, zc):
        """None/空/纯空白返回 None"""
        assert zc.ensure_collection(None) is None
        assert zc.ensure_collection("") is None
        assert zc.ensure_collection("   ") is None
