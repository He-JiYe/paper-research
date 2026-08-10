"""PaperDB 状态机测试（真实 SQLite，tmp_path 隔离）"""

import pytest
from src.db import PaperDB


@pytest.fixture
def db(tmp_path) -> PaperDB:
    return PaperDB(db_path=tmp_path / "papers.db")


def _add_paper(db: PaperDB, source_id: str = "2501.12345", source: str = "arxiv") -> None:
    db.add_papers(
        [
            {
                "source": source,
                "source_id": source_id,
                "title": "Some Paper",
                "authors": "Alice Zhang",
                "status": "summarized",
                "fetch_date": "2026-07-24",
            }
        ]
    )


class TestImportedMark:
    """导入 Zotero 成功后标记为已处理"""

    def test_imported_writes_all_fields(self, db):
        _add_paper(db)
        db.update_mark(
            "arxiv", "2501.12345", "imported", short_title="未读-RL-2025-Mult", zotero_key="ABC123"
        )

        paper = db.get_paper("arxiv", "2501.12345")
        assert paper["user_mark"] == "imported"
        assert paper["status"] == "reviewed"
        assert paper["short_title"] == "未读-RL-2025-Mult"
        assert paper["zotero_key"] == "ABC123"
        assert paper["marked_date"]  # 非空

    def test_imported_leaves_pending_pool(self, db):
        _add_paper(db)
        assert len(db.get_pending()) == 1

        db.update_mark("arxiv", "2501.12345", "imported", short_title="x", zotero_key="K")
        assert len(db.get_pending()) == 0

    def test_imported_appears_in_marked_group(self, db):
        _add_paper(db, "2501.00001")
        _add_paper(db, "2501.00002")
        _add_paper(db, "2501.00003")

        db.update_mark("arxiv", "2501.00001", "imported", short_title="x", zotero_key="K1")
        db.update_mark("arxiv", "2501.00002", "ignore")
        db.update_mark("arxiv", "2501.00003", "lurk")

        marked_ids = {p["source_id"] for p in db.get_marked()}
        # 已处理组 = 已忽略 + 已导入；lurk 在独立的延后组
        assert marked_ids == {"2501.00001", "2501.00002"}

    def test_pending_reset_restores_fresh_state(self, db):
        """pending 移回待审阅：清空导入字段，恢复到刚入库形态（幂等）。"""
        _add_paper(db)
        db.update_mark("arxiv", "2501.12345", "imported", short_title="x", zotero_key="K")
        db.update_mark("arxiv", "2501.12345", "pending")

        paper = db.get_paper("arxiv", "2501.12345")
        assert paper["user_mark"] is None
        assert paper["status"] == "summarized"
        assert paper["short_title"] == ""  # 离开 imported 即清空
        assert paper["zotero_key"] == ""  # 离开 imported 即清空
        assert len(db.get_pending()) == 1

    def test_non_imported_marks_clear_import_fields(self, db):
        """lurk/ignore 同样清空 imported 的 short_title / zotero_key（幂等）。"""
        _add_paper(db)
        db.update_mark("arxiv", "2501.12345", "imported", short_title="x", zotero_key="K")
        for mark in ("lurk", "ignore"):
            db.update_mark("arxiv", "2501.12345", mark)
            paper = db.get_paper("arxiv", "2501.12345")
            assert paper["short_title"] == ""
            assert paper["zotero_key"] == ""

    def test_unknown_mark_type_raises(self, db):
        """未知 mark_type 快速失败，而不是静默 no-op。"""
        _add_paper(db)
        with pytest.raises(ValueError):
            db.update_mark("arxiv", "2501.12345", "bogus")


class TestPendingOrdering:
    """待审核列表同日按评分稳定排序（BUG-5 修复）。"""

    def test_pending_sorted_by_score_desc(self, db):
        """同日论文按 llm_score 降序，消除刷新重排。"""
        db.add_papers(
            [
                {
                    "source": "arxiv",
                    "source_id": "2501.00001",
                    "title": "Low",
                    "status": "summarized",
                    "llm_score": 0.3,
                    "fetch_date": "2026-07-24",
                },
                {
                    "source": "arxiv",
                    "source_id": "2501.00002",
                    "title": "High",
                    "status": "summarized",
                    "llm_score": 0.9,
                    "fetch_date": "2026-07-24",
                },
                {
                    "source": "arxiv",
                    "source_id": "2501.00003",
                    "title": "Mid",
                    "status": "summarized",
                    "llm_score": 0.6,
                    "fetch_date": "2026-07-24",
                },
            ]
        )
        ids = [p["source_id"] for p in db.get_pending()]
        assert ids == ["2501.00002", "2501.00003", "2501.00001"]
