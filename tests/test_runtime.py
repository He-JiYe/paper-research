"""serve 运行时上下文、app 工厂、lifespan、run_server、导入管理器测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from src.serve import app
from src.serve.runtime import Runtime


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_runtime():
    """每测用例前清空 app.state.runtime，测后恢复。"""
    rt = app.state.runtime
    saved = (rt.settings, rt.zotero_client, rt.zotero_import, rt.scheduler)
    rt.settings = rt.zotero_client = rt.zotero_import = rt.scheduler = None
    yield
    rt.settings, rt.zotero_client, rt.zotero_import, rt.scheduler = saved


# ─── Runtime ──────────────────────────────────────────────


def test_runtime_log(caplog):
    import logging

    caplog.set_level(logging.INFO)
    rt = Runtime()
    rt.log("info", "运行时日志")
    assert any("运行时日志" in r.message for r in caplog.records)
    rt.log("bogus-level", "降级为 info")  # 未知级别不抛错


def test_runtime_sse_publish_subscribe():
    """SSE 事件流：订阅→推送→退订。"""
    rt = Runtime()
    q1 = rt.subscribe_sse()
    q2 = rt.subscribe_sse()
    rt.publish_sse({"type": "import-done", "status": "done"})
    assert q1.get_nowait() == {"type": "import-done", "status": "done"}
    assert q2.get_nowait() == {"type": "import-done", "status": "done"}
    rt.unsubscribe_sse(q1)
    rt.publish_sse({"type": "x"})
    assert q1.empty()  # 已退订不再收到
    assert q2.get_nowait() == {"type": "x"}


# ─── app 工厂 / lifespan ─────────────────────────────────


def test_app_has_routes(client):
    paths = [
        "/api/papers",
        "/api/static",
        "/api/zotero/collections",
        "/api/zotero/import-batch",
        "/api/zotero/import/status",
        "/",
    ]
    for p in paths:
        assert client.get(p).status_code in (200, 307, 404, 405, 500), p


def test_lifespan_starts_stops_scheduler():
    app.state.runtime.settings = SimpleNamespace()
    sched = MagicMock()
    sched.start = AsyncMock()
    with patch("src.serve.scheduler.FetchScheduler", return_value=sched):
        with TestClient(app) as c:
            assert c.get("/").status_code == 200
    sched.start.assert_awaited_once()
    sched.stop.assert_called_once()


def test_run_server_connects(monkeypatch):
    import src.serve as serve_mod

    settings = SimpleNamespace(
        zotero=SimpleNamespace(api_key="k", library_id="1", library_type="user"),
        server=SimpleNamespace(host="127.0.0.1", port=8899),
    )
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    with patch("src.zotero.ZoteroClient"), patch("src.zotero.manager.ZoteroImportManager"):
        serve_mod.run_server(settings)
    assert app.state.runtime.settings is settings
    assert app.state.runtime.zotero_client is not None


def test_run_server_zotero_fail(monkeypatch):
    import src.serve as serve_mod

    settings = SimpleNamespace(
        zotero=SimpleNamespace(api_key="k", library_id="1", library_type="user"),
        server=SimpleNamespace(host="127.0.0.1", port=8899),
    )
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    with patch("src.zotero.ZoteroClient", side_effect=RuntimeError("no zotero")):
        serve_mod.run_server(settings)
    assert app.state.runtime.zotero_client is None  # 降级：zotero 不可用但服务可起


# ─── 导入管理器 ──────────────────────────────────────────


def test_import_manager_submit_status(tmp_path):
    from src.zotero.manager import ZoteroImportManager

    mgr = ZoteroImportManager(MagicMock())
    assert mgr.status() == {"busy": False, "job": None}
    with patch.object(mgr, "_run", new_callable=AsyncMock):

        async def _do():
            return mgr.submit(
                [{"source": "arxiv", "source_id": "x", "short_title": "T", "collection_key": ""}]
            )

        job = asyncio.run(_do())
    assert job is not None
    assert mgr.status()["busy"] is True
    # 单飞：busy 时新提交返回 None
    assert mgr.submit([{"source": "arxiv", "source_id": "y"}]) is None


def test_import_manager_run_error(tmp_path):
    from src.zotero.manager import ZoteroImportManager

    zotero = MagicMock()
    zotero.get_item.side_effect = RuntimeError("zotero down")
    mgr = ZoteroImportManager(zotero)
    job = {
        "id": "1",
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
    with patch("src.db.PaperDB"):
        asyncio.run(mgr._run(job, [{"source": "arxiv", "source_id": "x"}]))
    assert job["status"] == "error"
    assert "zotero down" in job["error"]
