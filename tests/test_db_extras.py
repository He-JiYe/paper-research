"""PaperDB 统计/日志/查询补充测试（真实 SQLite，tmp_path 隔离）。"""

import pytest
from src.db import PaperDB


@pytest.fixture
def db(tmp_path):
    return PaperDB(db_path=tmp_path / "papers.db")


def _add(db, source_id, remark="important", kw="RL", fetch_date="2026-08-01"):
    db.add_papers(
        [
            {
                "source": "arxiv",
                "source_id": source_id,
                "title": f"Paper {source_id}",
                "llm_remark": remark,
                "llm_score": 0.8,
                "keyword_match": kw,
                "fetch_date": fetch_date,
            }
        ]
    )


def test_add_fetch_log_and_recent(db):
    db.add_fetch_log(keywords_used=2, papers_fetched=10, papers_new=3, papers_summarized=3)
    logs = db.get_recent_logs(limit=5)
    assert len(logs) == 1
    assert logs[0]["papers_new"] == 3
    assert logs[0]["status"] == "success"


def test_get_last_success_skips_failed_latest(db):
    """最新日志是 failed 时，仍返回今天更早的成功记录（fetch/status 判新的语义）。"""
    db.add_fetch_log(papers_new=2, run_time="2026-08-12 08:00:00")
    db.add_fetch_log(status="failed", run_time="2026-08-12 09:00:00")
    log = db.get_last_success("2026-08-12")
    assert log is not None
    assert log["status"] == "success"
    assert log["papers_new"] == 2


def test_get_last_success_none_when_no_success_today(db):
    """今日无成功记录返回 None；其它日期互不影响。"""
    db.add_fetch_log(status="failed", run_time="2026-08-12 09:00:00")
    assert db.get_last_success("2026-08-12") is None
    assert db.get_last_success("2026-08-13") is None


def test_has_successful_since_filters_by_time(db):
    """补抓判定：since 之后的成功记录算，之前的（含昨天）不算；边界含等于。"""
    db.add_fetch_log(papers_new=1, run_time="2026-08-12 08:00:00")  # 昨天
    db.add_fetch_log(papers_new=2, run_time="2026-08-13 08:30:01")
    assert db.has_successful_since("2026-08-13 08:30:00") is True
    assert db.has_successful_since("2026-08-13 08:30:01") is True  # 边界含等于
    assert db.has_successful_since("2026-08-13 09:00:00") is False  # 之后无记录


def test_has_successful_since_ignores_failed(db):
    """失败日志不算成功抓取。"""
    db.add_fetch_log(status="failed", run_time="2026-08-13 09:00:00")
    assert db.has_successful_since("2026-08-13 08:00:00") is False


def test_get_stats(db):
    _add(db, "1", remark="important")
    _add(db, "2", remark="useful")
    db.update_mark("arxiv", "1", "ignore")
    stats = db.get_stats()
    assert stats["total"] == 2
    assert stats["pending"] == 1
    assert stats["by_mark"]["ignore"] == 1
    assert stats["by_remark"]["important"] == 1
    assert stats["by_remark"]["useful"] == 1
    assert stats["by_keyword"]["RL"] == 2


def test_get_existing_ids(db):
    _add(db, "a")
    assert "a" in db.get_existing_ids("arxiv")


def test_pending_marked_lurk_groups(db):
    _add(db, "a")
    _add(db, "b")
    _add(db, "c")
    db.update_mark("arxiv", "b", "ignore")
    db.update_mark("arxiv", "c", "lurk")
    pending_ids = {p["source_id"] for p in db.get_pending()}
    marked_ids = {p["source_id"] for p in db.get_marked()}
    lurk_ids = {p["source_id"] for p in db.get_lurk()}
    assert pending_ids == {"a"}
    assert marked_ids == {"b"}
    assert lurk_ids == {"c"}


def test_update_mark_pending_resets(db):
    """pending 移回待审阅：清空标记并清空导入字段（恢复到刚入库形态，幂等）。"""
    _add(db, "a")
    db.update_mark("arxiv", "a", "imported", short_title="x", zotero_key="K")
    db.update_mark("arxiv", "a", "pending")
    paper = db.get_paper("arxiv", "a")
    assert paper["user_mark"] is None
    assert paper["zotero_key"] == ""  # 离开 imported 即清空
    assert paper["short_title"] == ""  # 离开 imported 即清空


def test_fetch_date_from_filters_all_queries(db):
    """fetch_date_from 起始日过滤：pending/marked/lurk/stats 四路同时生效。"""
    _add(db, "old1", fetch_date="2026-08-01")
    _add(db, "old2", fetch_date="2026-08-01")
    _add(db, "new1", fetch_date="2026-08-05")
    _add(db, "new2", fetch_date="2026-08-05")
    db.update_mark("arxiv", "old2", "ignore")
    db.update_mark("arxiv", "new2", "lurk")
    since = "2026-08-02"  # 只保留 08-02 及之后抓取的

    pending = db.get_pending(since)
    marked = db.get_marked(since)
    lurk = db.get_lurk(since)
    assert {p["source_id"] for p in pending} == {"new1"}
    assert {p["source_id"] for p in marked} == set()
    assert {p["source_id"] for p in lurk} == {"new2"}
    stats = db.get_stats(since)
    assert stats["total"] == 2
    assert stats["pending"] == 1

    # 不过滤时四路均可见
    assert len(db.get_pending()) == 2
    assert len(db.get_marked()) == 1
    assert len(db.get_lurk()) == 1
    assert db.get_stats()["total"] == 4
