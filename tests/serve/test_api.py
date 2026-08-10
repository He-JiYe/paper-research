"""serve 端口集成测试：TestClient 打全部 /api 端口（外部依赖全 mock，不触网）。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from src.serve import app


@pytest.fixture
def client():
    # 不进入 lifespan（避免启动调度器/真实抓取），仅测路由
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_runtime():
    """每测用例前清空 app.state.runtime，测后恢复。"""
    rt = app.state.runtime
    saved = (rt.settings, rt.zotero_client, rt.zotero_import, rt.scheduler)
    rt.settings = rt.zotero_client = rt.zotero_import = rt.scheduler = None
    yield
    rt.settings, rt.zotero_client, rt.zotero_import, rt.scheduler = saved


def make_paper(**overrides):
    paper = {
        "source": "arxiv",
        "source_id": "2401.00001",
        "title": "Test-Time Adaptation with Transformers",
        "authors": "Alice Zhang, Bob Li",
        "abstract": "We propose a novel method for test-time adaptation.",
        "url": "https://arxiv.org/abs/2401.00001",
        "pdf_url": "https://arxiv.org/pdf/2401.00001",
        "keyword_match": "test-time adaptation",
        "published": "2024-01-01",
        "fetch_date": "2024-01-05",
        "llm_remark": "important",
        "llm_score": 0.9,
        "short_title": "",
        "user_mark": "",
    }
    paper.update(overrides)
    return paper


class FakeDB:
    def __init__(self, unmarked=None, marked=None, lurk=None):
        self._u = unmarked or []
        self._m = marked or []
        self._l = lurk or []
        self.calls = []

    def get_pending(self, fetch_date_from=None):
        self.calls.append(("pending", fetch_date_from))
        return self._u

    def get_marked(self, fetch_date_from=None):
        return self._m

    def get_lurk(self, fetch_date_from=None):
        return self._l

    def get_stats(self, fetch_date_from=None):
        all_papers = self._u + self._m + self._l
        by_remark = {}
        for p in all_papers:
            r = p.get("llm_remark", "")
            if r:
                by_remark[r] = by_remark.get(r, 0) + 1
        return {
            "total": len(all_papers),
            "pending": len(self._u),
            "by_mark": {},
            "by_remark": by_remark,
            "by_keyword": {},
        }


@pytest.fixture
def mock_settings():
    return SimpleNamespace(
        scheduler=SimpleNamespace(enabled=False),
        notification=SimpleNamespace(),
        server=SimpleNamespace(host="127.0.0.1", port=8899),
        fetch=SimpleNamespace(sources=[]),
        keywords=[],
    )


# ─── /api/papers ──────────────────────────────────────────


def test_api_papers_shape(client, mock_settings):
    app.state.runtime.settings = mock_settings
    fake_db = FakeDB(unmarked=[make_paper()])
    with patch("src.serve.routes.papers.PaperDB", return_value=fake_db):
        resp = client.get("/api/papers")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"update_time", "range", "stats", "sections"}
    assert data["range"] == "all"
    assert data["stats"]["total"] == 1
    assert data["stats"]["important"] == 1
    assert "suggested_short_title" in data["sections"]["unmarked"][0]


def test_api_papers_range_passes_to_db(client, mock_settings):
    app.state.runtime.settings = mock_settings
    fake_db = FakeDB()
    with patch("src.serve.routes.papers.PaperDB", return_value=fake_db):
        resp = client.get("/api/papers?range=today")
    assert resp.json()["range"] == "today"
    # 今日范围 → get_pending(fetch_date_from=today 日期串)
    assert fake_db.calls[0][0] == "pending"
    assert fake_db.calls[0][1] is not None


def test_api_papers_skips_suggested_when_short_title(client, mock_settings):
    app.state.runtime.settings = mock_settings
    fake_db = FakeDB(unmarked=[make_paper(short_title="手动标题")])
    with patch("src.serve.routes.papers.PaperDB", return_value=fake_db):
        resp = client.get("/api/papers")
    paper = resp.json()["sections"]["unmarked"][0]
    assert paper["short_title"] == "手动标题"
    assert "suggested_short_title" not in paper


# ─── /api/static ──────────────────────────────────────────


def test_api_static_meta(client):
    resp = client.get("/api/static")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"categories", "remark_labels", "remark_colors", "section_labels"}
    assert "cs.AI" in data["categories"]


# ─── /api/fetch/status ────────────────────────────────────


def test_fetch_status_reports_last_success(client):
    fake_db = MagicMock()
    fake_db.get_last_success.return_value = {
        "run_time": "2026-08-12 08:30:00",
        "status": "success",
        "papers_new": 3,
    }
    with patch("src.serve.routes.papers.PaperDB", return_value=fake_db):
        resp = client.get("/api/fetch/status")
    assert resp.status_code == 200
    assert resp.json()["last_success"] == "2026-08-12 08:30:00"
    assert resp.json()["papers_new"] == 3


def test_fetch_status_none_when_no_success_today(client):
    fake_db = MagicMock()
    fake_db.get_last_success.return_value = None
    with patch("src.serve.routes.papers.PaperDB", return_value=fake_db):
        resp = client.get("/api/fetch/status")
    assert resp.json()["last_success"] is None


# ─── /mark ────────────────────────────────────────────────


def test_mark_ok(client):
    with patch("src.serve.routes.papers.PaperDB"):
        resp = client.post(
            "/mark", data={"source": "arxiv", "source_id": "x", "mark_type": "ignore"}
        )
    assert resp.status_code == 200
    assert resp.json()["mark_type"] == "ignore"


def test_mark_invalid_type(client):
    resp = client.post("/mark", data={"source": "arxiv", "source_id": "x", "mark_type": "bogus"})
    assert resp.status_code == 400


# ─── /api/zotero ──────────────────────────────────────────


def test_zotero_collections_not_connected(client):
    resp = client.get("/api/zotero/collections")
    assert resp.status_code == 500


def test_zotero_collections_ok(client):
    app.state.runtime.zotero_client = MagicMock()
    app.state.runtime.zotero_client.list_collections.return_value = [{"name": "A", "key": "K1"}]
    resp = client.get("/api/zotero/collections")
    assert resp.status_code == 200
    assert resp.json()["collections"] == [{"name": "A", "key": "K1"}]


def test_zotero_import_batch(client):
    app.state.runtime.zotero_import = MagicMock()
    app.state.runtime.zotero_import.submit.return_value = {"id": "1", "status": "running"}
    resp = client.post(
        "/api/zotero/import-batch",
        json={"items": [{"source": "arxiv", "source_id": "2401.00001"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == 1


def test_zotero_import_batch_busy_409(client):
    app.state.runtime.zotero_import = MagicMock()
    app.state.runtime.zotero_import.submit.return_value = None
    resp = client.post(
        "/api/zotero/import-batch",
        json={"items": [{"source": "arxiv", "source_id": "2401.00001"}]},
    )
    assert resp.status_code == 409


def test_zotero_import_batch_no_items(client):
    app.state.runtime.zotero_import = MagicMock()
    resp = client.post("/api/zotero/import-batch", json={"items": []})
    assert resp.status_code == 400


def test_zotero_import_batch_items_not_list(client):
    """items 非列表（如字符串）应 400，避免后台任务 AttributeError 假失败"""
    app.state.runtime.zotero_import = MagicMock()
    resp = client.post("/api/zotero/import-batch", json={"items": "not-a-list"})
    assert resp.status_code == 400


def test_zotero_import_batch_items_element_not_dict(client):
    """items 元素非对象（如字符串）应 400（B11）。"""
    app.state.runtime.zotero_import = MagicMock()
    resp = client.post(
        "/api/zotero/import-batch",
        json={"items": ["not-a-dict", {"source": "arxiv", "source_id": "x"}]},
    )
    assert resp.status_code == 400


def test_api_papers_invalid_range_400(client, mock_settings):
    """未知 range 值应 400（曾静默回退为 today）"""
    resp = client.get("/api/papers?range=bogus")
    assert resp.status_code == 400


def test_zotero_import_status(client):
    app.state.runtime.zotero_import = MagicMock()
    app.state.runtime.zotero_import.status.return_value = {"busy": False, "job": None}
    resp = client.get("/api/zotero/import/status")
    assert resp.status_code == 200


def test_import_events_sse_pushes_completion(client):
    """SSE 完成信号：import-done 推送 → 前端流收到。"""
    import threading
    import time

    def _pub():
        time.sleep(0.2)
        app.state.runtime.publish_sse({"type": "import-done", "status": "done", "job_id": "1"})

    t = threading.Thread(target=_pub)
    t.start()
    with client.stream("GET", "/api/import/events") as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())
    t.join()
    assert b"import-done" in body
    assert b"done" in body


def test_import_events_ignores_fetch_done(client):
    """BUG-2 回归：fetch-done（抓取域）不串扰到导入事件流。"""
    import threading
    import time

    def _pub():
        time.sleep(0.2)
        app.state.runtime.publish_sse({"type": "fetch-done", "status": "success", "papers_new": 1})
        app.state.runtime.publish_sse({"type": "import-done", "status": "done", "job_id": "1"})

    t = threading.Thread(target=_pub)
    t.start()
    with client.stream("GET", "/api/import/events") as r:
        body = b"".join(r.iter_bytes())
    t.join()
    assert b"fetch-done" not in body  # 跨域事件被过滤
    assert b"import-done" in body


# ─── 前端静态挂载 ─────────────────────────────────────────


def test_root_serves_frontend(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'id="paper-list"' in resp.text


def test_root_does_not_shadow_api(client, mock_settings):
    app.state.runtime.settings = mock_settings
    with patch("src.serve.routes.papers.PaperDB", return_value=FakeDB()):
        api_resp = client.get("/api/papers")
        root_resp = client.get("/")
    assert api_resp.status_code == 200
    assert root_resp.status_code == 200
