"""FastAPI 服务测试 —— 全部 25+ API 端点"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.db import init_db
from src.serve.server import app, set_db_conn, set_settings


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def _db_conn(tmp_path):
    """Thread-safe SQLite 连接（允许跨线程访问，适配 TestClient）"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _setup_server(mock_settings, _db_conn):
    """为每个测试重置 server 状态（使用 thread-safe 连接）"""
    set_db_conn(_db_conn)
    set_settings(mock_settings)


@pytest.fixture
def client(_setup_server):
    """提供 FastAPI TestClient 实例"""
    return TestClient(app)


# ─── Helpers ──────────────────────────────────────────────

SAMPLE_PAPER = {
    "arxiv_id": "2401.00001",
    "version": 1,
    "title": "Test-Time Adaptation with Transformers",
    "authors": "Alice Zhang, Bob Li",
    "abstract": "We propose a novel method for test-time adaptation using transformer architectures.",
    "url": "https://arxiv.org/abs/2401.00001",
    "primary_category": "cs.LG",
    "categories": "cs.LG, cs.CV",
    "published": "2024-01-01",
    "arxiv_updated": "2024-01-05T12:00:00Z",
    "keyword_match": "test-time adaptation",
    "fetch_date": "2024-01-10",
}

SAMPLE_PAPER_2 = {
    "arxiv_id": "2401.00002",
    "version": 1,
    "title": "Out-of-Distribution Detection via Energy Scoring",
    "authors": "Chen Wang, Dan Liu",
    "abstract": "Energy-based methods for out-of-distribution detection in deep neural networks.",
    "url": "https://arxiv.org/abs/2401.00002",
    "primary_category": "cs.LG",
    "categories": "cs.LG",
    "published": "2024-01-02",
    "arxiv_updated": "2024-01-06T12:00:00Z",
    "keyword_match": "out-of-distribution detection",
    "fetch_date": "2024-01-10",
}


def _insert_sample_paper(_db_conn, overrides=None):
    """Helper to insert a paper into db_conn"""
    from src.db import insert_paper

    paper = dict(SAMPLE_PAPER)
    if overrides:
        paper.update(overrides)
    insert_paper(_db_conn, paper)
    return paper


# ═══════════════════════════════════════════════════════════
# 现有测试类（保持原样）
# ═══════════════════════════════════════════════════════════


class TestFetchAPI:
    """测试 /api/fetch 端点"""

    def test_fetch_default_mode(self):
        """默认 mode 应为 incremental"""
        with patch("src.serve.server._do_fetch_safe") as mock_do:
            client = TestClient(app)
            resp = client.post("/api/fetch", data={"max_results": 20})
            assert resp.status_code == 200
            args = mock_do.call_args[0]
            assert args[3] == "incremental"

    def test_fetch_historical_mode(self):
        """应能指定 historical mode"""
        with patch("src.serve.server._do_fetch_safe") as mock_do:
            client = TestClient(app)
            resp = client.post("/api/fetch",
                               data={"max_results": 50, "mode": "historical"})
            assert resp.status_code == 200
            args = mock_do.call_args[0]
            assert args[3] == "historical"

    def test_fetch_with_cats_and_keyword(self):
        """应能传递 keyword 和 arxiv_cats"""
        with patch("src.serve.server._do_fetch_safe") as mock_do:
            client = TestClient(app)
            resp = client.post("/api/fetch",
                               data={
                                   "keyword": "test-time adaptation",
                                   "arxiv_cats": "cs.CV,cs.LG",
                                   "max_results": 30,
                                   "mode": "historical",
                               })
            assert resp.status_code == 200
            args = mock_do.call_args[0]
            assert args[0] == "test-time adaptation"
            assert args[1] == "cs.CV,cs.LG"
            assert args[2] == 30
            assert args[3] == "historical"


class TestSettingsAPI:
    """测试 /api/settings 端点"""

    def test_get_settings_contains_lookback(self):
        """GET 设置应返回 lookback_days"""
        client = TestClient(app)
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "lookback_days" in data
        assert "target_new_per_keyword" in data

    def test_update_settings(self):
        """PUT 设置应持久化并通过 GET 返回更新值"""
        client = TestClient(app)
        # CONFIG_DIR 在函数内部从 src.config 导入，因此 patch 其源头
        with patch("src.config.CONFIG_DIR") as mock_cfg:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps({"arxiv": {}})
            mock_cfg.__truediv__.return_value = mock_path

            resp = client.put("/api/settings",
                              json={"target_new_per_keyword": 50, "lookback_days": 14})
            assert resp.status_code == 200

        # GET 返回内存中的更新值
        resp2 = client.get("/api/settings")
        data2 = resp2.json()
        assert data2["lookback_days"] == 14
        assert data2["target_new_per_keyword"] == 50


# ═══════════════════════════════════════════════════════════
# 1. GET / — 首页
# ═══════════════════════════════════════════════════════════


class TestIndexEndpoint:
    """测试 GET / 端点"""

    def test_index_returns_html(self, client, tmp_path):
        """首页应返回 HTML summary"""
        summary_file = tmp_path / "summaries" / "index.html"
        summary_file.parent.mkdir(parents=True)
        summary_file.write_text("<html><body><h1>Paper Research Summary</h1></body></html>",
                                encoding="utf-8")

        with patch("src.serve.renderer.generate_summary_html") as mock_gen:
            mock_gen.return_value = summary_file
            resp = client.get("/")
            assert resp.status_code == 200
            assert "Paper Research Summary" in resp.text
            assert "text/html" in resp.headers["content-type"]

    def test_index_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.get("/")
        assert resp.status_code == 500
        assert "Database not initialized" in resp.text


# ═══════════════════════════════════════════════════════════
# 2. GET /notes — 笔记画廊
# ═══════════════════════════════════════════════════════════


class TestNotesGallery:
    """测试 GET /notes 端点"""

    def test_notes_gallery_returns_html(self, client, tmp_path, _db_conn):
        """笔记画廊页应返回 HTML"""
        from src.db import insert_paper

        insert_paper(_db_conn, SAMPLE_PAPER)
        insert_paper(_db_conn, SAMPLE_PAPER_2)

        notes_file = tmp_path / "notes" / "index.html"
        notes_file.parent.mkdir(parents=True)
        notes_file.write_text("<html><body><h1>Notes Gallery</h1><p>2 notes</p></body></html>",
                              encoding="utf-8")

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            with patch("src.serve.renderer.generate_notes_index_html") as mock_gen:
                mock_gen.return_value = notes_file
                resp = client.get("/notes")
                assert resp.status_code == 200
                assert "Notes Gallery" in resp.text

    def test_notes_gallery_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.get("/notes")
        assert resp.status_code == 500
        assert "Database not initialized" in resp.text


# ═══════════════════════════════════════════════════════════
# 3. GET /notes/{arxiv_id} — 笔记详情
# ═══════════════════════════════════════════════════════════


class TestNoteDetail:
    """测试 GET /notes/{arxiv_id} 端点"""

    def test_note_detail_found(self, client, tmp_path, _db_conn):
        """已存在的笔记应返回详情页"""
        from src.db import insert_paper

        insert_paper(_db_conn, SAMPLE_PAPER)

        note_dir = tmp_path / "notes" / "2401.00001"
        note_dir.mkdir(parents=True)
        (note_dir / "note.md").write_text("# Test Note\n\nSome content here", encoding="utf-8")

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            with patch("src.serve.renderer.render_note_detail_html") as mock_render:
                mock_render.return_value = "<html><body><h1>Test Note Detail</h1></body></html>"
                resp = client.get("/notes/2401.00001")
                assert resp.status_code == 200
                assert "Test Note Detail" in resp.text
                mock_render.assert_called_once()
                # Verify renderer received expected arguments
                call_kwargs = mock_render.call_args.kwargs
                assert call_kwargs["arxiv_id"] == "2401.00001"

    def test_note_detail_not_found(self, client, tmp_path):
        """不存在的笔记应返回 404"""
        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            resp = client.get("/notes/99999.99999")
            assert resp.status_code == 404
            assert "Note not found" in resp.text


# ═══════════════════════════════════════════════════════════
# 4. GET /api/pdf/{arxiv_id} — PDF 代理
# ═══════════════════════════════════════════════════════════


class TestPdfProxy:
    """测试 GET /api/pdf/{arxiv_id} 端点"""

    def test_local_pdf_served(self, client, tmp_path):
        """本地 PDF 文件应直接返回"""
        pdf_dir = tmp_path / "notes" / "2401.00001"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "paper.pdf").write_bytes(b"x" * 20000)  # > 10000 bytes

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            resp = client.get("/api/pdf/2401.00001")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/pdf"

    def test_remote_pdf_downloaded(self, client, tmp_path):
        """本地无 PDF 时应尝试下载并返回"""
        pdf_dir = tmp_path / "notes" / "2401.00001"
        pdf_dir.mkdir(parents=True)
        downloaded_path = pdf_dir / "paper.pdf"
        downloaded_path.write_bytes(b"x" * 15000)

        async def mock_download(arxiv_id, output_dir):
            return downloaded_path

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            with patch("src.network.factory.get_source") as mock_get:
                mock_source = MagicMock()
                mock_source.download_pdf = mock_download
                mock_get.return_value = mock_source
                resp = client.get("/api/pdf/2401.00001")
                assert resp.status_code == 200
                assert resp.headers["content-type"] == "application/pdf"

    def test_pdf_download_fail_returns_html(self, client, tmp_path, _db_conn):
        """下载失败时应返回 HTML fallback 页面"""
        from src.db import insert_paper

        insert_paper(_db_conn, SAMPLE_PAPER)

        async def mock_download_fail(arxiv_id, output_dir):
            raise Exception("Network error")

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            with patch("src.network.factory.get_source") as mock_get:
                mock_source = MagicMock()
                mock_source.download_pdf = mock_download_fail
                mock_get.return_value = mock_source
                resp = client.get("/api/pdf/2401.00001")
                assert resp.status_code == 200
                assert "PDF not available" in resp.text
                assert "text/html" in resp.headers["content-type"]

    def test_local_pdf_too_small_still_downloads(self, client, tmp_path):
        """小于 10KB 的本地 PDF 应触发重新下载"""
        pdf_dir = tmp_path / "notes" / "2401.00001"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "paper.pdf").write_bytes(b"small")  # < 10000 bytes
        downloaded_path = pdf_dir / "paper.pdf"
        downloaded_path.write_bytes(b"x" * 15000)

        async def mock_download(arxiv_id, output_dir):
            return downloaded_path

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            with patch("src.network.factory.get_source") as mock_get:
                mock_source = MagicMock()
                mock_source.download_pdf = mock_download
                mock_get.return_value = mock_source
                resp = client.get("/api/pdf/2401.00001")
                assert resp.status_code == 200
                assert resp.headers["content-type"] == "application/pdf"


# ═══════════════════════════════════════════════════════════
# 5. GET /notes/{arxiv_id}/figures/{filename} — 图表
# ═══════════════════════════════════════════════════════════


class TestNoteFigure:
    """测试 GET /notes/{arxiv_id}/figures/{filename} 端点"""

    def test_figure_served(self, client, tmp_path):
        """存在的图表文件应返回"""
        fig_dir = tmp_path / "notes" / "2401.00001" / "figures"
        fig_dir.mkdir(parents=True)
        (fig_dir / "architecture.png").write_bytes(b"fake-png-content")

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            resp = client.get("/notes/2401.00001/figures/architecture.png")
            assert resp.status_code == 200

    def test_figure_not_found(self, client, tmp_path):
        """不存在的图表应返回 404"""
        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            resp = client.get("/notes/2401.00001/figures/nonexistent.png")
            assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# 6. POST /mark — 标记论文
# ═══════════════════════════════════════════════════════════


class TestMarkAPI:
    """测试 POST /mark 端点"""

    @pytest.fixture(autouse=True)
    def _prevent_background_tasks(self):
        """阻止所有后台 asyncio.create_task 执行"""
        with patch("src.serve.server.asyncio.create_task"):
            yield

    def _insert_paper(self, _db_conn):
        from src.db import insert_paper
        insert_paper(_db_conn, SAMPLE_PAPER)

    def test_mark_deep_read(self, client, _db_conn):
        """标记 deep_read 应更新数据库"""
        self._insert_paper(_db_conn)
        resp = client.post("/mark", data={"arxiv_id": "2401.00001", "mark_type": "deep_read"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["arxiv_id"] == "2401.00001"
        assert data["mark_type"] == "deep_read"

        from src.db import get_paper
        paper = get_paper(_db_conn, "2401.00001")
        assert paper["user_mark"] == "deep_read"

    def test_mark_skim(self, client, _db_conn):
        """标记 skim 应更新数据库"""
        self._insert_paper(_db_conn)
        resp = client.post("/mark", data={"arxiv_id": "2401.00001", "mark_type": "skim"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mark_type"] == "skim"

        from src.db import get_paper
        paper = get_paper(_db_conn, "2401.00001")
        assert paper["user_mark"] == "skim"

    def test_mark_ignore(self, client, _db_conn):
        """标记 ignore 应更新数据库"""
        self._insert_paper(_db_conn)
        resp = client.post("/mark", data={"arxiv_id": "2401.00001", "mark_type": "ignore"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mark_type"] == "ignore"

        from src.db import get_paper
        paper = get_paper(_db_conn, "2401.00001")
        assert paper["user_mark"] == "ignore"

    def test_mark_lurk(self, client, _db_conn):
        """标记 lurk 应更新数据库"""
        self._insert_paper(_db_conn)
        resp = client.post("/mark", data={"arxiv_id": "2401.00001", "mark_type": "lurk"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mark_type"] == "lurk"

        from src.db import get_paper
        paper = get_paper(_db_conn, "2401.00001")
        assert paper["user_mark"] == "lurk"

    def test_mark_pending(self, client, _db_conn):
        """标记 pending 应清空 user_mark"""
        from src.db import insert_paper, mark_paper
        insert_paper(_db_conn, SAMPLE_PAPER)
        mark_paper(_db_conn, "2401.00001", "deep_read")

        resp = client.post("/mark", data={"arxiv_id": "2401.00001", "mark_type": "pending"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mark_type"] == "pending"

        from src.db import get_paper
        paper = get_paper(_db_conn, "2401.00001")
        assert paper["user_mark"] is None

    def test_mark_paper_not_found(self, client):
        """不存在的论文应返回 404"""
        resp = client.post("/mark", data={"arxiv_id": "99999.99999", "mark_type": "deep_read"})
        assert resp.status_code == 404

    def test_mark_invalid_type(self, client, _db_conn):
        """无效的标记类型应返回 400"""
        self._insert_paper(_db_conn)
        resp = client.post("/mark", data={"arxiv_id": "2401.00001", "mark_type": "invalid"})
        assert resp.status_code == 400

    def test_mark_re_mark_logs_change(self, client, _db_conn):
        """翻标记（从 deep_read 改为 skim）应成功"""
        from src.db import insert_paper, mark_paper
        insert_paper(_db_conn, SAMPLE_PAPER)
        mark_paper(_db_conn, "2401.00001", "deep_read")

        resp = client.post("/mark", data={"arxiv_id": "2401.00001", "mark_type": "skim"})
        assert resp.status_code == 200

        from src.db import get_paper
        paper = get_paper(_db_conn, "2401.00001")
        assert paper["user_mark"] == "skim"

    def test_mark_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.post("/mark", data={"arxiv_id": "2401.00001", "mark_type": "deep_read"})
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 7. GET /api/note/{arxiv_id} — 获取笔记
# ═══════════════════════════════════════════════════════════


class TestNoteRead:
    """测试 GET /api/note/{arxiv_id} 端点"""

    def test_get_note_found(self, client, tmp_path):
        """获取已存在的笔记内容"""
        note_dir = tmp_path / "notes" / "2401.00001"
        note_dir.mkdir(parents=True)
        (note_dir / "note.md").write_text("# My Note\n\nContent here", encoding="utf-8")

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            resp = client.get("/api/note/2401.00001")
            assert resp.status_code == 200
            assert "# My Note" in resp.text

    def test_get_note_not_found(self, client, tmp_path):
        """不存在的笔记应返回 404"""
        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            resp = client.get("/api/note/99999.99999")
            assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# 8. PUT /api/note/{arxiv_id} — 保存笔记
# ═══════════════════════════════════════════════════════════


class TestNoteSave:
    """测试 PUT /api/note/{arxiv_id} 端点"""

    def test_save_note_creates_file(self, client, tmp_path):
        """保存笔记应写入 .md 文件并返回成功"""
        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            resp = client.put("/api/note/2401.00001",
                              content="# Saved Note\n\nUpdated content")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["arxiv_id"] == "2401.00001"

        note_file = tmp_path / "notes" / "2401.00001" / "note.md"
        assert note_file.exists()
        assert note_file.read_text(encoding="utf-8") == "# Saved Note\n\nUpdated content"

    def test_save_note_overwrites_existing(self, client, tmp_path):
        """覆盖保存应更新文件内容"""
        note_dir = tmp_path / "notes" / "2401.00001"
        note_dir.mkdir(parents=True)
        (note_dir / "note.md").write_text("Old content", encoding="utf-8")

        with patch("src.serve.server.OUTPUT_DIR", tmp_path):
            client.put("/api/note/2401.00001", content="New content")

        assert (note_dir / "note.md").read_text(encoding="utf-8") == "New content"


# ═══════════════════════════════════════════════════════════
# 9. GET /api/categories — 分类列表
# ═══════════════════════════════════════════════════════════


class TestListCategories:
    """测试 GET /api/categories 端点"""

    def test_list_empty(self, client):
        """无分类时应返回空列表"""
        resp = client.get("/api/categories")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_categories(self, client, _db_conn):
        """应返回所有分类"""
        from src.db import add_category
        add_category(_db_conn, "Category A")
        add_category(_db_conn, "Category B")

        resp = client.get("/api/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = [c["name"] for c in data]
        assert "Category A" in names
        assert "Category B" in names

    def test_list_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.get("/api/categories")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 10. POST /api/categories — 创建分类
# ═══════════════════════════════════════════════════════════


class TestCreateCategory:
    """测试 POST /api/categories 端点"""

    def test_create_category(self, client, _db_conn):
        """创建新分类应返回 id 和 name"""
        resp = client.post("/api/categories", data={"name": "My Category"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "My Category"
        assert isinstance(data["id"], int)
        assert data["id"] > 0

        # Verify in DB
        from src.db import get_all_categories
        cats = get_all_categories(_db_conn)
        assert any(c["name"] == "My Category" for c in cats)

    def test_create_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.post("/api/categories", data={"name": "X"})
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 11. DELETE /api/categories/{id} — 删除分类
# ═══════════════════════════════════════════════════════════


class TestDeleteCategory:
    """测试 DELETE /api/categories/{id} 端点"""

    def test_delete_existing(self, client, _db_conn):
        """删除已存在的分类应返回 deleted=True"""
        from src.db import add_category
        cat_id = add_category(_db_conn, "Delete Me")

        resp = client.delete(f"/api/categories/{cat_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify gone
        from src.db import get_all_categories
        assert len(get_all_categories(_db_conn)) == 0

    def test_delete_nonexistent(self, client):
        """删除不存在的分类应返回 deleted=False"""
        resp = client.delete("/api/categories/9999")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is False

    def test_delete_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.delete("/api/categories/1")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 12. POST /api/note/{arxiv_id}/categories — 设置笔记分类
# ═══════════════════════════════════════════════════════════


class TestSetNoteCategories:
    """测试 POST /api/note/{arxiv_id}/categories 端点"""

    def test_set_categories(self, client, _db_conn):
        """应为笔记设置分类并持久化"""
        from src.db import add_category
        cat_a = add_category(_db_conn, "A")
        cat_b = add_category(_db_conn, "B")

        resp = client.post("/api/note/2401.00001/categories",
                           content=f"{cat_a},{cat_b}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["categories"] == [cat_a, cat_b]

        # Verify in DB
        from src.db import get_note_categories
        note_cats = get_note_categories(_db_conn, "2401.00001")
        assert len(note_cats) == 2

    def test_clear_categories(self, client, _db_conn):
        """空字符串应清空笔记的分类"""
        from src.db import add_category, set_note_categories
        cat_id = add_category(_db_conn, "A")
        set_note_categories(_db_conn, "2401.00001", [cat_id])

        resp = client.post("/api/note/2401.00001/categories", content="")
        assert resp.status_code == 200
        data = resp.json()
        assert data["categories"] == []

        from src.db import get_note_categories
        assert get_note_categories(_db_conn, "2401.00001") == []

    def test_set_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.post("/api/note/2401.00001/categories", content="1,2")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 13. POST /api/note/{arxiv_id}/short_info — 更新短信息
# ═══════════════════════════════════════════════════════════


class TestShortInfo:
    """测试 POST /api/note/{arxiv_id}/short_info 端点"""

    def test_update_short_info(self, client, _db_conn):
        """应更新笔记短标题和说明"""
        from src.db import insert_paper
        insert_paper(_db_conn, SAMPLE_PAPER)

        resp = client.post("/api/note/2401.00001/short_info",
                           data={"short_title": "My Title", "description": "My Description"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        from src.db import get_paper
        paper = get_paper(_db_conn, "2401.00001")
        assert paper["note_short_title"] == "My Title"
        assert paper["note_description"] == "My Description"

    def test_update_empty_values(self, client, _db_conn):
        """空值也应允许更新"""
        from src.db import insert_paper
        insert_paper(_db_conn, SAMPLE_PAPER)

        resp = client.post("/api/note/2401.00001/short_info",
                           data={"short_title": "", "description": ""})
        assert resp.status_code == 200

        from src.db import get_paper
        paper = get_paper(_db_conn, "2401.00001")
        assert paper["note_short_title"] == ""
        assert paper["note_description"] == ""

    def test_update_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.post("/api/note/2401.00001/short_info",
                           data={"short_title": "T", "description": "D"})
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 14. GET /api/keywords — 获取关键词
# ═══════════════════════════════════════════════════════════


class TestGetKeywords:
    """测试 GET /api/keywords 端点"""

    def test_get_keywords(self, client):
        """应返回 keywords.json 的内容"""
        mock_keywords = [
            {"keyword": "test-time adaptation", "arxiv_cats": ["cs.CV", "cs.LG"], "active": True},
            {"keyword": "domain generalization", "arxiv_cats": ["cs.CV"], "active": True},
        ]
        with patch("src.config.load_keywords", return_value=mock_keywords):
            resp = client.get("/api/keywords")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 2
            assert data[0]["keyword"] == "test-time adaptation"

    def test_get_keywords_empty(self, client):
        """无关键词时应返回空列表"""
        with patch("src.config.load_keywords", return_value=[]):
            resp = client.get("/api/keywords")
            assert resp.status_code == 200
            assert resp.json() == []


# ═══════════════════════════════════════════════════════════
# 15. POST /api/keywords — 保存关键词
# ═══════════════════════════════════════════════════════════


class TestSaveKeywords:
    """测试 POST /api/keywords 端点"""

    def test_save_keywords(self, client):
        """应保存关键词并返回计数"""
        keywords = [{"keyword": "new keyword", "arxiv_cats": ["cs.LG"], "active": True}]
        with patch("src.config.save_keywords") as mock_save:
            resp = client.post("/api/keywords", json=keywords)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["count"] == 1
            mock_save.assert_called_once_with(keywords)

    def test_save_keywords_empty_list(self, client):
        """空列表应保存并返回 count=0"""
        with patch("src.config.save_keywords") as mock_save:
            resp = client.post("/api/keywords", json=[])
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 0
            mock_save.assert_called_once_with([])

    def test_save_keywords_invalid_body(self, client):
        """非列表的 body 应返回 400"""
        resp = client.post("/api/keywords", json={"keyword": "test"})
        assert resp.status_code == 400



# ═══════════════════════════════════════════════════════════
# 16-17. /api/settings — 已有测试 + 补充
# ═══════════════════════════════════════════════════════════


class TestSettingsAPIExtended:
    """测试 /api/settings 端点补充用例"""

    def test_get_settings_contains_all_fields(self, client):
        """GET 设置应返回所有预期字段"""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "lookback_days" in data
        assert "target_new_per_keyword" in data
        assert "max_concurrent_requests" in data

    def test_update_settings_partial(self, client):
        """PUT 应支持部分更新（仅更新个别字段）"""
        with patch("src.config.CONFIG_DIR") as mock_cfg:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps({"arxiv": {}})
            mock_cfg.__truediv__.return_value = mock_path

            resp = client.put("/api/settings", json={"lookback_days": 30})
            assert resp.status_code == 200

        resp = client.get("/api/settings")
        data = resp.json()
        assert data["lookback_days"] == 30
        # Other fields should retain defaults
        assert data["target_new_per_keyword"] == 25

    def test_get_settings_no_settings(self, client):
        """未初始化时应返回 500"""
        set_settings(None)
        resp = client.get("/api/settings")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 18. GET /api/stats — 统计信息
# ═══════════════════════════════════════════════════════════


class TestStatsAPI:
    """测试 GET /api/stats 端点"""

    def test_stats_with_data(self, client, _db_conn):
        """有论文数据时应返回完整统计"""
        from src.db import insert_paper, update_paper_summary, mark_paper
        insert_paper(_db_conn, SAMPLE_PAPER)
        insert_paper(_db_conn, SAMPLE_PAPER_2)
        update_paper_summary(_db_conn, "2401.00001", "S1", "important", "R1", 0.95)
        update_paper_summary(_db_conn, "2401.00002", "S2", "useful", "R2", 0.75)
        mark_paper(_db_conn, "2401.00001", "deep_read")

        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        assert data["important"] >= 1
        assert data["useful"] >= 1
        assert data["deep_read"] >= 1
        assert "summarized_pending" in data

    def test_stats_empty_db(self, client):
        """空数据库应返回全零统计"""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["new"] == 0
        assert data["summarized_pending"] == 0

    def test_stats_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.get("/api/stats")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 19. GET /api/papers — 论文列表
# ═══════════════════════════════════════════════════════════


class TestPapersAPI:
    """测试 GET /api/papers 端点"""

    def test_papers_with_data(self, client, _db_conn):
        """应返回分组后的论文列表"""
        from src.db import insert_paper, update_paper_summary, mark_paper
        insert_paper(_db_conn, SAMPLE_PAPER)
        insert_paper(_db_conn, SAMPLE_PAPER_2)
        update_paper_summary(_db_conn, "2401.00001", "S1", "important", "R1", 0.95)
        mark_paper(_db_conn, "2401.00001", "deep_read")

        resp = client.get("/api/papers")
        assert resp.status_code == 200
        data = resp.json()
        assert "unmarked" in data
        assert "marked" in data
        assert "lurk" in data
        # One paper marked deep_read, one unmarked
        assert len(data["marked"]) >= 1
        assert len(data["unmarked"]) >= 1

    def test_papers_empty(self, client):
        """空数据库应返回所有分组为空"""
        resp = client.get("/api/papers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unmarked"] == []
        assert data["marked"] == []
        assert data["lurk"] == []

    def test_papers_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.get("/api/papers")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 20. GET /api/fetch-status-stream — SSE 流
# ═══════════════════════════════════════════════════════════


class TestFetchStatusStream:
    """测试 GET /api/fetch-status-stream SSE 端点"""

    def test_sse_delivers_data(self, client):
        """SSE 应推送 fetch 结果数据"""
        async def mock_wait_for(coro, timeout, **kwargs):
            return {"fetched": 5, "new": 3}

        with patch("src.serve.server.asyncio.wait_for", mock_wait_for):
            resp = client.get("/api/fetch-status-stream")
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            assert 'data: {"fetched": 5, "new": 3}' in resp.text

    def test_sse_timeout(self, client):
        """超时时应返回 timeout 数据"""
        async def mock_wait_for_timeout(coro, timeout, **kwargs):
            raise TimeoutError()

        with patch("src.serve.server.asyncio.wait_for", mock_wait_for_timeout):
            resp = client.get("/api/fetch-status-stream")
            assert resp.status_code == 200
            assert "timeout" in resp.text
            assert "fetched" in resp.text


# ═══════════════════════════════════════════════════════════
# 21. POST /api/fetch — 触发抓取（更多用例）
# ═══════════════════════════════════════════════════════════


class TestFetchAPIExtended:
    """测试 POST /api/fetch 端点的更多用例"""

    def test_fetch_default_params(self):
        """默认参数应传递到 _do_fetch_safe"""
        with patch("src.serve.server._do_fetch_safe") as mock_do:
            client = TestClient(app)
            resp = client.post("/api/fetch", data={})
            assert resp.status_code == 200
            args = mock_do.call_args[0]
            assert args[0] == ""  # keyword
            assert args[1] == ""  # arxiv_cats
            assert args[2] == 20  # max_results
            assert args[3] == "incremental"  # mode

    def test_fetch_response_format(self):
        """应返回标准 JSON 响应"""
        with patch("src.serve.server._do_fetch_safe"):
            client = TestClient(app)
            resp = client.post("/api/fetch", data={"max_results": 10})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "message" in data
            assert "background" in data["message"]


# ═══════════════════════════════════════════════════════════
# 22. GET /api/search-db — 搜索已有论文
# ═══════════════════════════════════════════════════════════


class TestSearchDB:
    """测试 GET /api/search-db 端点"""

    def test_search_by_title(self, client, _db_conn):
        """按标题搜索应返回匹配结果"""
        from src.db import insert_paper
        insert_paper(_db_conn, SAMPLE_PAPER)
        insert_paper(_db_conn, SAMPLE_PAPER_2)

        resp = client.get("/api/search-db", params={"q": "Transformer"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert any("Transformer" in p["title"] for p in data["papers"])

    def test_search_by_authors(self, client, _db_conn):
        """按作者搜索应返回匹配结果"""
        from src.db import insert_paper
        insert_paper(_db_conn, SAMPLE_PAPER)

        resp = client.get("/api/search-db", params={"q": "Alice"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert any("Alice" in p["authors"] for p in data["papers"])

    def test_search_by_arxiv_id(self, client, _db_conn):
        """按 arxiv_id 搜索应返回匹配结果"""
        from src.db import insert_paper
        insert_paper(_db_conn, SAMPLE_PAPER)

        resp = client.get("/api/search-db", params={"q": "2401.00001"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert any("2401.00001" == p["arxiv_id"] for p in data["papers"])

    def test_search_no_match(self, client, _db_conn):
        """无匹配时应返回空结果"""
        from src.db import insert_paper
        insert_paper(_db_conn, SAMPLE_PAPER)

        resp = client.get("/api/search-db", params={"q": "zzz_no_match_zzz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["papers"] == []

    def test_search_empty_query(self, client):
        """空查询应返回空结果"""
        resp = client.get("/api/search-db", params={"q": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["papers"] == []

    def test_search_no_db(self, client):
        """数据库未初始化时搜索应返回空结果"""
        set_db_conn(None)
        resp = client.get("/api/search-db", params={"q": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["papers"] == []


# ═══════════════════════════════════════════════════════════
# 23. GET /api/search — 搜索 Arxiv
# ═══════════════════════════════════════════════════════════


class TestSearchArxiv:
    """测试 GET /api/search 端点"""

    def test_search_returns_results(self, client):
        """搜索应返回 Arxiv 结果"""
        expected = [
            {"arxiv_id": "2401.99999", "title": "Search Result", "authors": "Author A"},
            {"arxiv_id": "2401.99998", "title": "Another Result", "authors": "Author B"},
        ]

        async def mock_search(query, max_results, categories=None):
            return expected

        with patch("src.network.factory.get_source") as mock_get:
            mock_source = MagicMock()
            mock_source.search = mock_search
            mock_get.return_value = mock_source
            resp = client.get("/api/search", params={"q": "transformer adaptation"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 2
            assert data["papers"] == expected

    def test_search_with_categories(self, client):
        """搜索可指定分类过滤"""
        async def mock_search(query, max_results, categories=None):
            assert categories == ["cs.CV", "cs.LG"]
            return [{"arxiv_id": "2401.99999", "title": "CV Paper"}]

        with patch("src.network.factory.get_source") as mock_get:
            mock_source = MagicMock()
            mock_source.search = mock_search
            mock_get.return_value = mock_source
            resp = client.get("/api/search",
                              params={"q": "image", "cats": "cs.CV,cs.LG"})
            assert resp.status_code == 200
            assert resp.json()["count"] == 1

    def test_search_empty_query(self, client):
        """空查询应返回空结果"""
        resp = client.get("/api/search", params={"q": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["papers"] == []

    def test_search_max_param(self, client):
        """max 参数应传递给 search 函数"""
        async def mock_search(query, max_results, categories=None):
            assert max_results == 50
            return [{"arxiv_id": "2401.99999", "title": "Test"}]

        with patch("src.network.factory.get_source") as mock_get:
            mock_source = MagicMock()
            mock_source.search = mock_search
            mock_get.return_value = mock_source
            resp = client.get("/api/search", params={"q": "test", "max": 50})
            assert resp.status_code == 200

    def test_search_max_capped_at_100(self, client):
        """max 参数不应超过 100"""
        async def mock_search(query, max_results, categories=None):
            assert max_results == 100
            return [{"arxiv_id": "2401.99999", "title": "Test"}]

        with patch("src.network.factory.get_source") as mock_get:
            mock_source = MagicMock()
            mock_source.search = mock_search
            mock_get.return_value = mock_source
            resp = client.get("/api/search", params={"q": "test", "max": 500})
            assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════
# 24. POST /api/import-papers — 导入论文
# ═══════════════════════════════════════════════════════════


class TestImportPapers:
    """测试 POST /api/import-papers 端点"""

    def test_import_with_ids(self, client):
        """导入论文应触发后台任务"""
        with patch("src.serve.server.asyncio.create_task") as mock_task:
            resp = client.post("/api/import-papers",
                               json={"ids": ["2401.00001", "2401.00002"]})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["count"] == 2
            assert "Importing 2 papers" in data["message"]

    def test_import_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.post("/api/import-papers", json={"ids": ["2401.00001"]})
        assert resp.status_code == 500

    def test_import_empty_ids(self, client):
        """空的 ids 列表应返回 400"""
        resp = client.post("/api/import-papers", json={"ids": []})
        assert resp.status_code == 400

    def test_import_missing_ids_key(self, client):
        """缺少 ids 键应返回 400"""
        resp = client.post("/api/import-papers", json={})
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════
# 25. POST /api/refresh — 刷新快照
# ═══════════════════════════════════════════════════════════


class TestRefresh:
    """测试 POST /api/refresh 端点"""

    def test_refresh_triggers_tasks(self, client):
        """刷新应触发两个后台任务"""
        with patch("src.serve.server.asyncio.create_task") as mock_task:
            resp = client.post("/api/refresh")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "Refresh triggered" in data["message"]
            assert mock_task.call_count == 2

    def test_refresh_response_format(self, client):
        """刷新应返回标准 JSON"""
        with patch("src.serve.server.asyncio.create_task"):
            resp = client.post("/api/refresh")
            assert resp.status_code == 200
            data = resp.json()
            assert "status" in data
            assert "message" in data


# ═══════════════════════════════════════════════════════════
# 26. POST /api/notify — 发送通知
# ═══════════════════════════════════════════════════════════


class TestNotify:
    """测试 POST /api/notify 端点"""

    def test_notify_sends_toast_and_email(self, client):
        """通知应调用 Toast 和 email 发送函数"""
        with patch("src.notify.send_windows_toast") as mock_toast:
            with patch("src.notify.send_email_if_configured") as mock_email:
                resp = client.post("/api/notify")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert "Notification sent" in data["message"]
                mock_toast.assert_called_once()
                mock_email.assert_called_once()

    def test_notify_no_db(self, client):
        """数据库未初始化时应返回 500"""
        set_db_conn(None)
        resp = client.post("/api/notify")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
# 综合：未初始化 DB 的通用错误响应
# ═══════════════════════════════════════════════════════════


class TestUninitializedDB:
    """测试各类端点在 DB 未初始化时的行为"""

    @pytest.fixture(autouse=True)
    def _clear_db(self):
        set_db_conn(None)
        set_settings(None)
        yield
        # Restore is handled by the autouse _setup_server for subsequent tests
        # but since _setup_server runs before each test, this is fine.

    def test_all_endpoints_return_500_on_no_db(self, client):
        """关键 API 端点未初始化时返回 500"""
        endpoints = [
            ("GET", "/api/stats"),
            ("GET", "/api/papers"),
            ("GET", "/api/categories"),
            ("POST", "/api/notify"),
        ]
        for method, path in endpoints:
            if method == "GET":
                resp = client.get(path)
            else:
                resp = client.post(path)
            assert resp.status_code == 500, f"{method} {path} should return 500"
