"""ImportManager 后台导入编排测试：假 pyzotero + 假 DB，不触网。

覆盖：新论文导入 / 已在 Zotero 去重并标记 web 端已处理 / 单飞拒绝 / 批量 / on_done 回调。
（PDF 附件功能已移除：本机 Zotero connector 只读，不支持本地添加附件。）
"""

import asyncio

import pytest
import src.zotero.client as client_mod
from src.zotero.client import ZoteroClient
from src.zotero.manager import ZoteroImportManager

from tests._fake_zotero import FakeZotero


class FakeDB:
    def __init__(self):
        self.marks = []

    def get_paper(self, source, source_id):
        return {
            "source": source,
            "source_id": source_id,
            "title": "Test Paper",
            "authors": "Alice Zhang",
            "published": "2024-01-01",
            "url": "http://example.com",
            "abstract": "abs",
        }

    def update_mark(self, source, source_id, mark_type, short_title="", zotero_key=""):
        self.marks.append(
            {
                "source": source,
                "source_id": source_id,
                "mark_type": mark_type,
                "short_title": short_title,
                "zotero_key": zotero_key,
            }
        )


@pytest.fixture
def mgr(monkeypatch):
    monkeypatch.setattr(client_mod, "PyzoteroClient", FakeZotero)
    zc = ZoteroClient("k", 1, "user")
    fake_db = FakeDB()
    monkeypatch.setattr("src.db.PaperDB", lambda: fake_db)
    mgr = ZoteroImportManager(zc)
    mgr._fake_db = fake_db  # 供测试断言 update_mark 记录
    return mgr


def _job(source_id):
    return {
        "id": source_id,
        "items": 1,
        "status": "running",
        "step": "",
        "log": [],
        "items_status": {},
        "started_at": "",
        "finished_at": None,
        "result": None,
        "error": None,
    }


def _item(source_id, short_title="ST1", coll="N / X"):
    return {
        "source": "arxiv",
        "source_id": source_id,
        "short_title": short_title,
        "collection_key": coll,
    }


def _first(job):
    return job["result"]["items"][0]


class TestImportFlow:
    def test_new_import(self, mgr):
        job = _job("a1")
        asyncio.run(mgr._run(job, [_item("a1")]))
        assert job["status"] == "done", job.get("error")
        first = _first(job)
        assert first["created"] is True
        assert len(mgr._zotero._client._colls) == 2  # N 和 N/X
        assert len(mgr._zotero._client._items) == 1  # 父条目（Web API）
        it = mgr._zotero._client._items[first["zotero_key"]]
        assert it["data"]["shortTitle"] == "ST1"
        nx_key = next(c["key"] for c in mgr._zotero.list_collections() if c["path"] == "N / X")
        assert nx_key in it["data"]["collections"]
        # web 端标记 imported
        assert any(m["mark_type"] == "imported" for m in mgr._fake_db.marks)
        # items_status 记录 item=imported（合成 key "source:source_id"）
        assert job["items_status"]["arxiv:a1"] == {"item": "imported"}

    def test_existing_item_not_modified(self, mgr):
        job1 = _job("a1")
        asyncio.run(mgr._run(job1, [_item("a1")]))
        it = mgr._zotero._client._items[_first(job1)["zotero_key"]]

        job2 = _job("a1")
        asyncio.run(mgr._run(job2, [_item("a1", short_title="SHOULD_NOT_CHANGE")]))
        assert job2["status"] == "done", job2.get("error")
        assert _first(job2)["created"] is False
        assert it["data"]["shortTitle"] == "ST1"  # 内容未被改动
        assert len(mgr._zotero._client._items) == 1
        # 已在 Zotero → web 端也标记 imported（移入已处理）
        imported_marks = [m for m in mgr._fake_db.marks if m["mark_type"] == "imported"]
        assert len(imported_marks) == 2  # job1 导入 + job2 跳过各标记一次

    def test_existing_without_collection_skips(self, mgr):
        """已存在且无分类要求 → 直接跳过，不重复创建，但标记 web 端已处理。"""
        job1 = _job("a1")
        asyncio.run(mgr._run(job1, [_item("a1", coll="")]))
        job2 = _job("a1")
        asyncio.run(mgr._run(job2, [_item("a1", coll="")]))
        assert job2["status"] == "done", job2.get("error")
        assert _first(job2)["created"] is False
        assert len(mgr._zotero._client._items) == 1
        imported_marks = [m for m in mgr._fake_db.marks if m["mark_type"] == "imported"]
        assert len(imported_marks) == 2

    def test_existing_with_new_collection_skips_no_duplicate(self, mgr):
        """已存在于 Zotero + 归档到新分类 → 跳过不重复创建（B2）。"""
        job1 = _job("a1")
        asyncio.run(mgr._run(job1, [_item("a1", coll="N / X")]))
        assert len(mgr._zotero._client._items) == 1

        job2 = _job("a1")
        asyncio.run(mgr._run(job2, [_item("a1", coll="Other")]))
        assert job2["status"] == "done", job2.get("error")
        assert _first(job2)["created"] is False
        assert len(mgr._zotero._client._items) == 1  # 不产生第二条目

    def test_batch_imports_all(self, mgr):
        job = _job("batch")
        asyncio.run(mgr._run(job, [_item("b1", coll=""), _item("b2", coll="")]))
        assert job["status"] == "done", job.get("error")
        assert job["result"]["total"] == 2
        assert len(mgr._zotero._client._items) == 2

    def test_partial_failure_marks_successful_only(self, mgr, monkeypatch):
        """批量部分失败：成功项落库为 imported，失败项不伪造，任务走 error 终态。"""
        async def _partial_create_items(papers, *, short_titles=None, collection_keys=None):
            return ["KEY1", None]  # 第二篇被 Zotero 拒绝

        monkeypatch.setattr(mgr._zotero, "create_items", _partial_create_items)
        job = _job("partial")
        asyncio.run(mgr._run(job, [_item("p1", coll=""), _item("p2", coll="")]))
        assert job["status"] == "error"
        assert "Zotero 拒绝" in job["error"]
        marks = [(m["source_id"], m["mark_type"]) for m in mgr._fake_db.marks]
        assert ("p1", "imported") in marks
        assert ("p2", "imported") not in marks
        # B5：部分失败也保留已成功项明细
        assert job["result"] is not None
        assert [r["source_id"] for r in job["result"]["items"]] == ["p1"]

    def test_single_flight_rejects_while_busy(self, mgr):
        mgr._job = {"status": "running", "id": "busy", "log": [], "result": None}
        assert mgr.busy
        assert mgr.submit([{"source": "arxiv", "source_id": "a9"}]) is None


class TestOnDone:
    def test_manager_calls_on_done_callback(self, mgr):
        """任务完成时调用注入的 on_done（serve 用它经 SSE 推送完成信号）。"""
        events = []
        mgr._on_done = lambda r: events.append(r)
        job = _job("x1")
        asyncio.run(mgr._run(job, [_item("x1", coll="")]))
        assert job["status"] == "done"
        assert len(events) == 1
        assert events[0]["type"] == "import-done"
        assert events[0]["status"] == "done"
        assert events[0]["job_id"] == "x1"

    def test_manager_on_done_error(self, mgr, monkeypatch):
        """任务失败时 on_done 收到 error 状态。"""
        from unittest.mock import MagicMock

        monkeypatch.setattr("src.db.PaperDB", lambda: FakeDB())
        zotero = MagicMock()
        zotero.get_item.side_effect = RuntimeError("zotero down")
        events = []
        mgr2 = ZoteroImportManager(zotero, on_done=lambda r: events.append(r))
        job = _job("x1")
        asyncio.run(mgr2._run(job, [_item("x1", coll="")]))
        assert job["status"] == "error"
        assert len(events) == 1
        assert events[0]["status"] == "error"
        assert "zotero down" in events[0]["error"]


@pytest.fixture
def zc(monkeypatch):
    monkeypatch.setattr(client_mod, "PyzoteroClient", FakeZotero)
    return ZoteroClient("k", 1, "user")


class TestEnsureCollectionPathToKey:
    """ensure_collection(ref, path_to_key=...)：base62 key 直接返回 / 路径走预拉映射 / 缺级创建"""

    def test_base62_key_returned_directly(self, zc):
        """含非 hex 字母的 8 位 base62 key 直接返回，不触达 list/create"""
        assert zc.ensure_collection("KMNQRXYZ", path_to_key={}) == "KMNQRXYZ"
        assert len(zc._client._colls) == 0

    def test_hex_key_returned_directly(self, zc):
        """纯 hex key 仍识别（hex 是 base62 子集）"""
        assert zc.ensure_collection("C0000001", path_to_key={}) == "C0000001"
        assert len(zc._client._colls) == 0

    def test_path_uses_path_to_key_map(self, zc):
        """路径（含中间级）命中预拉映射时直接返回，不触发 list/create"""
        assert zc.ensure_collection("A / B", path_to_key={"a": "KEYA", "a / b": "KEY2"}) == "KEY2"
        assert len(zc._client._colls) == 0

    def test_path_missing_creates(self, zc):
        """映射未命中（需新建）时逐级 find-or-create"""
        key = zc.ensure_collection("A / B", path_to_key={})
        coll = next(c for c in zc.list_collections() if c["path"] == "A / B")
        assert coll["key"] == key
        assert len(zc._client._colls) == 2  # A、A / B

    def test_none_or_blank_returns_none(self, zc):
        """None/空/纯空白返回 None"""
        assert zc.ensure_collection(None, path_to_key={}) is None
        assert zc.ensure_collection("", path_to_key={}) is None
        assert zc.ensure_collection("   ", path_to_key={}) is None
        assert len(zc._client._colls) == 0
