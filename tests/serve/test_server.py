"""FastAPI 服务端路由测试"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── 在 import server 前先 patch 全局变量 ──────
# server 模块在 import 时挂载静态文件（STATIC_DIR.exists() 判断）
# 我们需要 mock 掉 db_conn 和 settings 的依赖


@pytest.fixture
def mock_server_deps():
    """打 patch server 模块的全局变量"""
    patches = [
        patch("src.serve.server._db_conn", MagicMock()),
        patch("src.serve.server._settings", MagicMock()),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


# 延迟 import，让 patch 先生效
@pytest.fixture
def client():
    """创建 FastAPI TestClient"""
    # 先 patch 掉 STATIC_DIR 避免挂载失败
    with patch("src.serve.server.STATIC_DIR") as mock_static:
        mock_static.exists.return_value = False
        from src.serve.server import app

        with TestClient(app) as c:
            yield c


class TestServerCore:
    def test_index_no_db(self, client):
        """无数据库时返回 500"""
        # 确保 _db_conn 为 None
        import src.serve.server as srv

        srv._db_conn = None
        srv._settings = None
        resp = client.get("/")
        assert resp.status_code == 500
        assert "Database not initialized" in resp.text

    def test_notes_no_db(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        resp = client.get("/notes")
        assert resp.status_code == 500
        assert "Database not initialized" in resp.text

    def test_note_detail_not_found(self, client):
        """不存在的笔记返回 404"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        resp = client.get("/notes/9999.99999")
        assert resp.status_code == 404
        assert "Note not found" in resp.text

    def test_note_figure_not_found(self, client):
        """不存在的图表返回 404"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        resp = client.get("/notes/2401.00001/figures/nonexist.png")
        assert resp.status_code == 404

    def test_api_stats_no_db(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        resp = client.get("/api/stats")
        assert resp.status_code == 500
        assert "Database not initialized" in resp.text

    def test_api_papers_no_db(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        resp = client.get("/api/papers")
        assert resp.status_code == 500

    def test_api_fetch_status_no_file(self, client):
        """无状态文件时返回缓存"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()
        srv.FETCH_STATUS_CACHE = {"fetched": 0, "new": 0, "pending": False}
        # 确保 fetch_status.json 不存在
        status_file = srv.OUTPUT_DIR / "fetch_status.json"
        if status_file.exists():
            status_file.unlink()
        resp = client.get("/api/fetch_status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 0

    def test_api_fetch_not_initialized(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        srv._settings = None
        resp = client.post("/api/fetch")
        assert resp.status_code == 500
        assert "Not initialized" in resp.text

    def test_api_notify_not_initialized(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        srv._settings = None
        resp = client.post("/api/notify")
        assert resp.status_code == 500
        assert "Not initialized" in resp.text

    def test_api_refresh(self, client):
        """/api/refresh 返回成功"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()
        resp = client.post("/api/refresh")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_api_search_empty_query(self, client):
        """空查询返回空列表"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()
        resp = client.get("/api/search?q=")
        assert resp.status_code == 200
        assert resp.json()["papers"] == []

    def test_api_categories_no_db(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        resp = client.get("/api/categories")
        assert resp.status_code == 500

    def test_api_import_no_ids(self, client):
        """导入论文无 ID 时报错"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()
        resp = client.post("/api/import-papers", json={"ids": []})
        assert resp.status_code == 400
        assert "No ids" in resp.text

    def test_api_import_not_initialized(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        srv._settings = None
        resp = client.post("/api/import-papers", json={"ids": ["2401.00001"]})
        assert resp.status_code == 500

    def test_api_fetch_status_with_file(self, client, temp_dir):
        """状态文件存在时返回文件内容"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()
        srv.OUTPUT_DIR / "fetch_status.json"

        # 先读配置的 OUTPUT_DIR
        from src.config import OUTPUT_DIR

        status_path = OUTPUT_DIR / "fetch_status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps({"fetched": 5, "new": 3}))
        srv.FETCH_STATUS_CACHE = {"fetched": 0, "new": 0, "pending": False}

        resp = client.get("/api/fetch_status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 5
        # 文件应在读取后被删除
        assert not status_path.exists()

    def test_mark_paper_no_db(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        resp = client.post(
            "/mark", data={"arxiv_id": "2401.00001", "mark_type": "skim"}
        )
        assert resp.status_code == 500


class TestApiSearch:
    def test_api_search_with_query(self, client):
        """api_search 调用 search_arxiv 并返回结果"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        mock_papers = [{"arxiv_id": "2401.00001", "title": "Test"}]

        with patch(
            "src.network.arxiv.search_arxiv", AsyncMock(return_value=mock_papers)
        ):
            resp = client.get("/api/search?q=transformer")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["papers"][0]["arxiv_id"] == "2401.00001"


class TestNoteEndpoints:
    def test_get_note_not_found(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        resp = client.get("/api/note/2401.99999")
        assert resp.status_code == 404

    def test_put_and_get_note(self, client, temp_dir):
        """保存后读取笔记"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()

        from src.config import OUTPUT_DIR

        note_dir = OUTPUT_DIR / "notes" / "2401.00001"
        note_dir.mkdir(parents=True, exist_ok=True)

        resp = client.put("/api/note/2401.00001", content="# Test Note")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        resp = client.get("/api/note/2401.00001")
        assert resp.status_code == 200
        assert "# Test Note" in resp.text

        # 清理
        import shutil

        shutil.rmtree(OUTPUT_DIR / "notes" / "2401.00001", ignore_errors=True)


class TestProxyPdf:
    def test_proxy_pdf_fallback_html(self, client):
        """PDF 不存在且下载失败时返回 HTML fallback"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()

        with (
            patch(
                "src.network.arxiv.download_pdf",
                AsyncMock(side_effect=Exception("Download failed")),
            ),
            patch(
                "src.db.get_paper",
                return_value={"title": "Test Paper", "arxiv_id": "2401.00001"},
            ),
        ):
            resp = client.get("/api/pdf/2401.00001")
            assert resp.status_code == 200
            assert "PDF not available" in resp.text
            assert "Test Paper" in resp.text


class TestCategoryEndpoints:
    def test_create_and_list_categories(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        with patch("src.db.add_category", return_value=1):
            resp = client.post("/api/categories", data={"name": "NewCat"})
            assert resp.status_code == 200
            assert resp.json()["id"] == 1

        with patch(
            "src.db.get_all_categories",
            return_value=[{"id": 1, "name": "NewCat"}, {"id": 2, "name": "TestCat"}],
        ):
            resp = client.get("/api/categories")
            assert resp.status_code == 200
            data = resp.json()
            assert any(c["name"] == "NewCat" for c in data)

    def test_delete_category(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        with (
            patch("src.db.delete_category", return_value=True),
            patch("src.serve.server._refresh_notes_index", AsyncMock()),
        ):
            resp = client.delete("/api/categories/1")
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True

    def test_set_note_categories(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        with patch("src.db.set_note_categories") as mock_set:
            resp = client.post("/api/note/2401.00001/categories", content="1,3,5")
            assert resp.status_code == 200
            mock_set.assert_called_once()

    def test_update_short_info(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        resp = client.post(
            "/api/note/2401.00001/short_info",
            data={"short_title": "2024-Test", "description": "A test"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestMarkEndpoint:
    def test_mark_paper_not_found(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()

        with patch("src.db.get_paper", return_value=None):
            resp = client.post(
                "/mark", data={"arxiv_id": "9999.99999", "mark_type": "skim"}
            )
            assert resp.status_code == 404
            assert "Paper not found" in resp.text

    def test_mark_paper_invalid_type(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()

        with patch("src.db.get_paper", return_value={"arxiv_id": "2401.00001"}):
            resp = client.post(
                "/mark", data={"arxiv_id": "2401.00001", "mark_type": "invalid"}
            )
            assert resp.status_code == 400
            assert "Invalid mark type" in resp.text

    def test_mark_paper_ignore(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        with patch(
            "src.db.get_paper", return_value={"arxiv_id": "2401.00001", "title": "Test"}
        ):
            with patch("src.db.mark_paper"):
                with patch("src.serve.server._refresh_snapshot", AsyncMock()):
                    with patch("src.serve.server._refresh_notes_index", AsyncMock()):
                        resp = client.post(
                            "/mark",
                            data={"arxiv_id": "2401.00001", "mark_type": "ignore"},
                        )
                        assert resp.status_code == 200
                        assert resp.json()["mark_type"] == "ignore"


class TestMoreApiEndpoints:
    def test_api_stats_with_data(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()

        with patch(
            "src.db.get_stats",
            return_value={"total": 10, "summarized_pending": 3, "important": 2},
        ):
            resp = client.get("/api/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 10
            assert data["important"] == 2

    def test_api_import_papers(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        resp = client.post(
            "/api/import-papers", json={"ids": ["2401.00001", "2401.00002"]}
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_api_notify_sends(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        with patch(
            "src.db.get_stats", return_value={"important": 1, "summarized_pending": 2}
        ):
            with patch("src.db.get_pending_keywords", return_value=[]):
                with patch("src.notify.send_windows_toast"):
                    with patch("src.notify.send_email_if_configured"):
                        resp = client.post("/api/notify")
                        assert resp.status_code == 200
                        assert resp.json()["status"] == "ok"


class TestMdToHtml:
    """_md_to_html 纯函数测试"""

    def test_heading(self):
        from src.serve.server import _md_to_html

        assert "<h1>Title</h1>" in _md_to_html("# Title")
        assert "<h2>Section</h2>" in _md_to_html("## Section")

    def test_paragraph(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("Hello world")
        assert "<p>Hello world</p>" in result

    def test_bold(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("**bold** text")
        assert "<strong>bold</strong>" in result

    def test_list(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("- item1\n- item2")
        assert "<ul>" in result
        assert "<li>item1</li>" in result
        assert "<li>item2</li>" in result

    def test_horizontal_rule(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("---")
        assert "<hr>" in result

    def test_image_with_fig_prefix(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("![alt](figures/fig1.png)", arxiv_id="2401.00001")
        assert '/notes/2401.00001/figures/fig1.png"' in result
        assert 'alt="alt"' in result

    def test_image_no_prefix(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("![alt](https://example.com/img.png)")
        assert 'src="https://example.com/img.png"' in result

    def test_link_with_fig_prefix(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("[link](figures/doc.pdf)", arxiv_id="2401.00001")
        assert "/notes/2401.00001/figures/doc.pdf" in result

    def test_html_comment(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("<!-- comment -->")
        assert "<!-- comment -->" in result

    def test_empty_br(self):
        from src.serve.server import _md_to_html

        result = _md_to_html("a\n\nb")
        assert "<br>" in result

    def test_mixed_content(self):
        from src.serve.server import _md_to_html

        text = "# Title\n\nParagraph with **bold**\n\n- item **bold**"
        result = _md_to_html(text)
        assert "<h1>Title</h1>" in result
        assert "<strong>bold</strong>" in result
        assert "<li>" in result


class TestSseEndpoint:
    """SSE 通知机制测试"""

    def test_sse_notify_clients(self):
        """_notify_sse_clients 推送数据到所有队列"""
        import src.serve.server as srv

        q1 = asyncio.Queue()
        q2 = asyncio.Queue()
        srv._sse_clients.extend([q1, q2])
        try:
            srv._notify_sse_clients({"fetched": 3, "new": 1})
            assert q1.get_nowait() == {"fetched": 3, "new": 1}
            assert q2.get_nowait() == {"fetched": 3, "new": 1}
        finally:
            srv._sse_clients.clear()

    def test_sse_notify_empty_clients(self):
        """空客户端列表时不影响推送"""
        import src.serve.server as srv

        srv._sse_clients.clear()
        srv._notify_sse_clients({"fetched": 0})  # should not raise

    def test_fetch_status_stream_endpoint(self, client):
        """SSE 端点超时时返回 timeout 事件"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()
        srv._sse_clients.clear()
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            resp = client.get("/api/fetch-status-stream")
            assert "text/event-stream" in resp.headers.get("content-type", "")
            assert "timeout" in resp.text


class TestKeywordApi:
    """关键词 API 测试"""

    def test_get_keywords(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        mock_kws = [{"keyword": "test", "active": True}]
        with patch("src.config.load_keywords", return_value=mock_kws):
            resp = client.get("/api/keywords")
            assert resp.status_code == 200
            assert resp.json()[0]["keyword"] == "test"

    def test_save_keywords_invalid(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        resp = client.post("/api/keywords", json={"not": "a list"})
        assert resp.status_code == 400

    def test_save_keywords_valid(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        with patch("src.config.save_keywords"):
            resp = client.post(
                "/api/keywords", json=[{"keyword": "test", "active": True}]
            )
            assert resp.status_code == 200
            assert resp.json()["count"] == 1


class TestSearchDb:
    """数据库搜索测试"""

    def test_search_db_empty(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        resp = client.get("/api/search-db?q=")
        assert resp.status_code == 200
        assert resp.json()["papers"] == []

    def test_search_db_no_db(self, client):
        import src.serve.server as srv

        srv._db_conn = None
        resp = client.get("/api/search-db?q=test")
        assert resp.status_code == 200
        assert resp.json()["papers"] == []

    def test_search_db_with_results(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"arxiv_id": "2401.00001", "title": "Test", "authors": "A"}
        ]
        srv._db_conn.execute.return_value = mock_cursor
        resp = client.get("/api/search-db?q=test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1


class TestLogFunction:
    """_log 函数测试"""

    def test_log_info(self):
        from src.serve.server import _log

        with patch("builtins.print") as mock_print:
            _log("info", "test message")
            mock_print.assert_called_once()

    def test_log_with_logger(self):
        from src.serve.server import _log, _logger

        old_logger = _logger
        mock_logger = MagicMock()
        import src.serve.server as srv

        srv._logger = mock_logger
        try:
            _log("info", "test")
            mock_logger.info.assert_called_once_with("test")
        finally:
            srv._logger = old_logger


class TestMarkEndpointEdgeCases:
    """标记端点的边界情况测试"""

    def test_mark_pending(self, client):
        """pending 标记类型"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        with (
            patch(
                "src.db.get_paper",
                return_value={"arxiv_id": "2401.00001", "title": "Test"},
            ),
            patch("src.db.mark_paper"),
            patch("src.serve.server._refresh_snapshot", AsyncMock()),
            patch("src.serve.server._refresh_notes_index", AsyncMock()),
        ):
            resp = client.post(
                "/mark", data={"arxiv_id": "2401.00001", "mark_type": "pending"}
            )
            assert resp.status_code == 200
            assert resp.json()["mark_type"] == "pending"

    def test_mark_lurk(self, client):
        """lurk 标记类型"""
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()

        with (
            patch(
                "src.db.get_paper",
                return_value={"arxiv_id": "2401.00001", "title": "Test"},
            ),
            patch("src.db.mark_paper"),
            patch("src.serve.server._refresh_snapshot", AsyncMock()),
            patch("src.serve.server._refresh_notes_index", AsyncMock()),
        ):
            resp = client.post(
                "/mark", data={"arxiv_id": "2401.00001", "mark_type": "lurk"}
            )
            assert resp.status_code == 200
            assert resp.json()["mark_type"] == "lurk"


class TestServerEdgeCases:
    """更多边界情况测试"""

    def test_note_detail_with_paper(self, client, temp_dir):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        from src.config import OUTPUT_DIR

        note_dir = OUTPUT_DIR / "notes" / "2401.00001"
        note_dir.mkdir(parents=True, exist_ok=True)
        (note_dir / "note.md").write_text("# Test Paper\n\nContent", encoding="utf-8")
        with patch(
            "src.db.get_paper", return_value={"arxiv_id": "2401.00001", "title": "Test"}
        ):
            resp = client.get("/notes/2401.00001")
            assert resp.status_code == 200
            assert "Test Paper" in resp.text
        import shutil

        shutil.rmtree(OUTPUT_DIR / "notes" / "2401.00001", ignore_errors=True)

    def test_api_keywords_post_no_list(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        resp = client.post("/api/keywords", json={"keyword": "test"})
        assert resp.status_code == 400

    def test_mark_pending_with_re_mark(self, client):
        import src.serve.server as srv

        srv._db_conn = MagicMock()
        srv._settings = MagicMock()
        with (
            patch(
                "src.db.get_paper",
                return_value={
                    "arxiv_id": "2401.00001",
                    "title": "Test",
                    "user_mark": "skim",
                },
            ),
            patch("src.db.mark_paper"),
            patch("src.serve.server._refresh_snapshot", AsyncMock()),
            patch("src.serve.server._refresh_notes_index", AsyncMock()),
        ):
            resp = client.post(
                "/mark", data={"arxiv_id": "2401.00001", "mark_type": "deep_read"}
            )
            assert resp.status_code == 200
            assert resp.json()["mark_type"] == "deep_read"
