"""数据库层测试：CRUD、分类、查询、统计、抓取日志"""

import sqlite3

import pytest

from src.db import (add_category, delete_category, exists, get_all_categories,
                    get_all_papers, get_connection,
                    get_earliest_paper_date_for_keyword,
                    get_keyword_paper_count, get_most_recent_fetch_log,
                    get_note_categories, get_note_short_title, get_paper,
                    get_paper_status, get_papers_by_keyword,
                    get_papers_by_mark, get_papers_for_summary,
                    get_pending_keywords, get_recent_logs, get_stats, init_db,
                    insert_fetch_log, insert_paper, mark_paper,
                    set_note_categories, touch_paper, update_note_short_info,
                    update_paper_summary, update_paper_version)

# ─── 连接与初始化 ─────────────────────────────────────────


class TestConnectionInit:
    def test_get_connection(self, db_path):
        conn = get_connection(db_path)
        assert isinstance(conn, sqlite3.Connection)
        assert conn.row_factory is sqlite3.Row
        cursor = conn.execute("PRAGMA journal_mode")
        assert cursor.fetchone()[0] in ("wal", "memory")
        conn.close()

    def test_init_db_creates_tables(self, db_path):
        conn = get_connection(db_path)
        init_db(db_path)
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "papers" in tables
        assert "note_categories" in tables
        assert "note_category_map" in tables
        assert "fetch_log" in tables
        indexes = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        ]
        assert "idx_papers_arxiv_id" in indexes
        conn.close()

    def test_init_db_idempotent(self, db_path):
        init_db(db_path)
        init_db(db_path)
        conn = get_connection(db_path)
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert len(tables) >= 4
        conn.close()


# ─── Paper CRUD ─────────────────────────────────────────


class TestPaperCRUD:
    def test_insert_and_get_paper(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        paper = get_paper(conn, "2401.00001")
        assert paper is not None
        assert paper["arxiv_id"] == "2401.00001"
        assert paper["title"] == "Test-Time Adaptation: A Survey"
        assert paper["authors"] == "Alice Zhang, Bob Li"
        assert paper["status"] == "new"

    def test_insert_duplicate_violates_unique(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        with pytest.raises(sqlite3.IntegrityError):
            insert_paper(conn, sample_paper)

    def test_exists(self, conn, sample_paper):
        assert not exists(conn, "2401.00001")
        insert_paper(conn, sample_paper)
        assert exists(conn, "2401.00001")
        assert not exists(conn, "2401.99999")

    def test_get_paper_nonexistent(self, conn):
        assert get_paper(conn, "9999.99999") is None

    def test_update_paper_summary(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        update_paper_summary(
            conn, "2401.00001", "Great paper", "important", "Novel", 0.95
        )
        paper = get_paper(conn, "2401.00001")
        assert paper["llm_summary"] == "Great paper"
        assert paper["llm_remark"] == "important"
        assert paper["llm_reason"] == "Novel"
        assert paper["llm_score"] == 0.95
        assert paper["status"] == "summarized"

    def test_update_paper_version(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        update_paper_version(
            conn, "2401.00001", 2, "2024-02-01T00:00:00Z", "2024-07-15"
        )
        paper = get_paper(conn, "2401.00001")
        assert paper["version"] == 2
        assert paper["is_updated"] == 1
        assert paper["old_version"] == 1
        assert paper["status"] == "new"

    def test_touch_paper(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        touch_paper(conn, "2401.00001", "2024-08-01")
        paper = get_paper(conn, "2401.00001")
        assert paper["last_fetch_date"] == "2024-08-01"

    def test_mark_paper(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        mark_paper(conn, "2401.00001", "deep_read")
        paper = get_paper(conn, "2401.00001")
        assert paper["user_mark"] == "deep_read"
        assert paper["status"] == "marked"

    def test_get_paper_status(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        assert get_paper_status(conn, "2401.00001") == "new"
        assert get_paper_status(conn, "9999.99999") is None

    def test_update_paper_version_nonexistent(self, conn):
        """对不存在的论文调用 update_paper_version"""
        update_paper_version(
            conn, "9999.99999", 2, "2024-02-01T00:00:00Z", "2024-07-15"
        )


# ─── Note Categories ─────────────────────────────────────


class TestNoteCategories:
    @pytest.fixture(autouse=True)
    def setup(self, conn):
        add_category(conn, "精读")
        add_category(conn, "NLP")
        add_category(conn, "CV")

    def test_get_all_categories(self, conn):
        cats = get_all_categories(conn)
        assert len(cats) >= 3
        names = [c["name"] for c in cats]
        assert "精读" in names
        assert "NLP" in names

    def test_add_category(self, conn):
        cid = add_category(conn, "新分类")
        assert isinstance(cid, int)
        cats = get_all_categories(conn)
        assert any(c["name"] == "新分类" for c in cats)

    def test_add_duplicate_category(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            add_category(conn, "NLP")

    def test_delete_category(self, conn):
        cats = get_all_categories(conn)
        target = next(c for c in cats if c["name"] == "NLP")
        result = delete_category(conn, target["id"])
        assert result is True
        cats = get_all_categories(conn)
        assert not any(c["name"] == "NLP" for c in cats)

    def test_delete_nonexistent_category(self, conn):
        result = delete_category(conn, 9999)
        assert result is False

    def test_set_and_get_note_categories(self, conn):
        cats = get_all_categories(conn)
        cvs = [c for c in cats if c["name"] == "CV"]
        nlps = [c for c in cats if c["name"] == "NLP"]

        from src.db import insert_paper

        insert_paper(
            conn,
            {
                "arxiv_id": "2401.00100",
                "title": "Test",
                "authors": "A",
                "abstract": "abstract",
                "url": "",
                "primary_category": "cs.CV",
                "categories": "cs.CV",
                "published": "2024-01-01",
                "arxiv_updated": "",
                "keyword_match": "test",
                "fetch_date": "2024-07-01",
            },
        )
        set_note_categories(conn, "2401.00100", [cvs[0]["id"], nlps[0]["id"]])
        note_cats = get_note_categories(conn, "2401.00100")
        assert len(note_cats) == 2
        assert any(c["name"] == "CV" for c in note_cats)

    def test_set_note_categories_replaces(self, conn):
        from src.db import insert_paper

        insert_paper(
            conn,
            {
                "arxiv_id": "2401.00101",
                "title": "T",
                "authors": "A",
                "abstract": "a",
                "url": "",
                "primary_category": "cs.LG",
                "categories": "cs.LG",
                "published": "2024-01-01",
                "arxiv_updated": "",
                "keyword_match": "t",
                "fetch_date": "2024-07-01",
            },
        )
        cats = get_all_categories(conn)
        set_note_categories(conn, "2401.00101", [cats[0]["id"]])
        assert len(get_note_categories(conn, "2401.00101")) == 1
        set_note_categories(conn, "2401.00101", [])
        assert len(get_note_categories(conn, "2401.00101")) == 0

    def test_get_note_short_title(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        assert get_note_short_title(conn, "2401.00001") == ""
        update_note_short_info(conn, "2401.00001", "2024-TTA", "A survey paper")
        assert get_note_short_title(conn, "2401.00001") == "2024-TTA"

    def test_update_note_short_info(self, conn, sample_paper):
        insert_paper(conn, sample_paper)
        update_note_short_info(conn, "2401.00001", "2024-TTA", "Survey of TTA")
        paper = get_paper(conn, "2401.00001")
        assert paper["note_short_title"] == "2024-TTA"
        assert paper["note_description"] == "Survey of TTA"


# ─── 查询 ───────────────────────────────────────────────


class TestQueries:
    def test_get_papers_for_summary(self, sample_papers, conn):
        result = get_papers_for_summary(conn)
        assert "unmarked" in result
        assert "marked" in result
        assert "lurk" in result
        assert any(p["arxiv_id"] == "2401.00001" for p in result["marked"])
        assert any(p["arxiv_id"] == "2401.00002" for p in result["unmarked"])

    def test_get_papers_for_summary_with_lurk(self, conn):
        """论文标记为 lurk 时归入 lurk 分组"""
        from src.db import insert_paper, mark_paper, update_paper_summary

        insert_paper(
            conn,
            {
                "arxiv_id": "2401.00010",
                "title": "Lurk Paper",
                "authors": "A",
                "abstract": "abstract",
                "url": "",
                "primary_category": "cs.CV",
                "categories": "cs.CV",
                "published": "2024-01-01",
                "arxiv_updated": "",
                "keyword_match": "test",
                "fetch_date": "2024-07-01",
            },
        )
        update_paper_summary(conn, "2401.00010", "S", "browse", "R", 0.5)
        mark_paper(conn, "2401.00010", "lurk")
        result = get_papers_for_summary(conn)
        assert any(p["arxiv_id"] == "2401.00010" for p in result["lurk"])

    def test_get_pending_keywords(self, sample_papers, conn):
        keywords = get_pending_keywords(conn)
        names = [k["keyword"] for k in keywords]
        assert "out-of-distribution detection" in names

    def test_get_papers_by_mark(self, sample_papers, conn):
        papers = get_papers_by_mark(conn, "deep_read")
        assert len(papers) == 1
        assert papers[0]["arxiv_id"] == "2401.00001"

    def test_get_papers_by_keyword(self, sample_papers, conn):
        papers = get_papers_by_keyword(conn, "test-time adaptation")
        assert len(papers) == 1
        assert papers[0]["arxiv_id"] == "2401.00001"

    def test_get_keyword_paper_count(self, sample_papers, conn):
        cnt = get_keyword_paper_count(conn, "test-time adaptation")
        assert cnt == 1

    def test_get_all_papers(self, sample_papers, conn):
        all_p = get_all_papers(conn)
        assert len(all_p) == 3

    def test_get_all_papers_with_status(self, sample_papers, conn):
        summarized = get_all_papers(conn, status="summarized")
        assert 1 <= len(summarized) <= 3

    def test_get_all_papers_limit(self, sample_papers, conn):
        limited = get_all_papers(conn, limit=2)
        assert len(limited) == 2


# ─── 统计 ───────────────────────────────────────────────


class TestStats:
    def test_get_stats_basic(self, sample_papers, conn):
        stats = get_stats(conn)
        assert stats["total"] == 3
        assert isinstance(stats["important"], int)
        assert isinstance(stats["deep_read"], int)

    def test_get_stats_detail(self, sample_papers, conn):
        stats = get_stats(conn)
        assert stats["important"] >= 1
        assert stats["useful"] >= 1
        assert stats["deep_read"] >= 1

    def test_get_stats_empty_db(self, conn):
        stats = get_stats(conn)
        assert stats["total"] == 0
        assert stats["new"] == 0
        assert stats["summarized_pending"] == 0

    def test_get_stats_all_fields(self, conn):
        stats = get_stats(conn)
        expected_fields = {
            "total",
            "new",
            "summarized_pending",
            "skim",
            "deep_read",
            "ignored",
            "lurk",
            "updated",
            "important",
            "useful",
            "browse",
            "skip",
        }
        assert set(stats.keys()) == expected_fields


# ─── Fetch Log ──────────────────────────────────────────


class TestFetchLog:
    def test_insert_and_get_recent_logs(self, conn):
        insert_fetch_log(
            conn,
            keywords_used=2,
            papers_fetched=50,
            papers_new=5,
            papers_updated=1,
            papers_summarized=5,
            status="success",
        )
        logs = get_recent_logs(conn)
        assert len(logs) == 1
        assert logs[0]["papers_fetched"] == 50
        assert logs[0]["status"] == "success"
        assert logs[0]["run_time"]

    def test_get_most_recent_fetch_log(self, conn):
        insert_fetch_log(
            conn,
            keywords_used=1,
            papers_fetched=10,
            papers_new=2,
            papers_updated=0,
            papers_summarized=2,
            status="success",
        )
        log = get_most_recent_fetch_log(conn)
        assert log is not None
        assert log["papers_fetched"] == 10

    def test_get_most_recent_fetch_log_empty(self, conn):
        assert get_most_recent_fetch_log(conn) is None

    def test_get_recent_logs_limit(self, conn):
        for i in range(5):
            insert_fetch_log(
                conn,
                keywords_used=i,
                papers_fetched=i * 10,
                papers_new=i,
                papers_updated=0,
                papers_summarized=i,
                status="success",
            )
        logs = get_recent_logs(conn, limit=3)
        assert len(logs) == 3

    def test_get_earliest_paper_date_for_keyword(self, sample_papers, conn):
        date = get_earliest_paper_date_for_keyword(conn, "test-time adaptation")
        assert date == "2024-01-01"

    def test_get_earliest_paper_date_for_keyword_missing(self, sample_papers, conn):
        date = get_earliest_paper_date_for_keyword(conn, "nonexistent")
        assert date is None

    def test_insert_fetch_log_with_error(self, conn):
        insert_fetch_log(
            conn,
            keywords_used=1,
            papers_fetched=0,
            papers_new=0,
            papers_updated=0,
            papers_summarized=0,
            status="failed",
            error_msg="Connection timeout",
        )
        logs = get_recent_logs(conn)
        assert logs[0]["status"] == "failed"
        assert logs[0]["error_msg"] == "Connection timeout"

    def test_mark_paper_pending(self, conn, sample_paper):
        """mark_paper pending 清空标记，状态回到 summarized"""
        from src.db import insert_paper, update_paper_summary

        insert_paper(conn, sample_paper)
        update_paper_summary(conn, "2401.00001", "S", "useful", "R", 0.7)
        mark_paper(conn, "2401.00001", "pending")
        paper = get_paper(conn, "2401.00001")
        assert paper["user_mark"] is None
        assert paper["status"] == "summarized"
