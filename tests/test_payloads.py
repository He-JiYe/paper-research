"""payloads 直接单测：range 换算 + /api/papers 响应组装（不触网、不触 DB）。"""

import datetime

from src.serve.payloads import build_papers_payload, range_start_date


class TestRangeStartDate:
    def test_all_and_none_return_none(self):
        assert range_start_date("all") is None
        assert range_start_date(None) is None
        assert range_start_date("") is None

    def test_today(self):
        today = datetime.date(2026, 8, 13)
        assert range_start_date("today", today) == "2026-08-13"

    def test_7d_and_30d(self):
        today = datetime.date(2026, 8, 13)
        assert range_start_date("7d", today) == "2026-08-07"
        assert range_start_date("30d", today) == "2026-07-15"

    def test_unknown_returns_none(self):
        """未知 range → None（与 all 同语义；路由层已对非法值 400）。"""
        today = datetime.date(2026, 8, 13)
        assert range_start_date("bogus", today) is None


def _paper(source_id, **overrides):
    paper = {
        "source": "arxiv",
        "source_id": source_id,
        "title": "Test-Time Adaptation with Transformers",
        "authors": "Alice Zhang, Bob Li",
        "abstract": "We propose a novel method.",
        "published": "2024-01-01",
        "short_title": "",
        "llm_remark": "important",
    }
    paper.update(overrides)
    return paper


class FakeDB:
    def __init__(self, unmarked=None, marked=None, lurk=None):
        self._u = unmarked or []
        self._m = marked or []
        self._l = lurk or []
        self.pending_from = None

    def get_pending(self, fetch_date_from=None):
        self.pending_from = fetch_date_from
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
        return {"total": len(all_papers), "pending": len(self._u), "by_remark": by_remark}


class TestBuildPapersPayload:
    def test_shape(self):
        payload = build_papers_payload(FakeDB(unmarked=[_paper("a")]))
        assert set(payload) == {"update_time", "range", "stats", "sections"}
        assert set(payload["sections"]) == {"unmarked", "marked", "lurk"}

    def test_stats_derived(self):
        db = FakeDB(unmarked=[_paper("a", llm_remark="important")])
        payload = build_papers_payload(db)
        assert payload["stats"]["total"] == 1
        assert payload["stats"]["important"] == 1
        assert payload["stats"]["unmarked"] == 1

    def test_range_passed_to_db(self):
        db = FakeDB()
        payload = build_papers_payload(db, range_="today")
        assert payload["range"] == "today"
        assert db.pending_from is not None  # today → 具体起始日，非 None

    def test_suggested_short_title_injected(self):
        paper = _paper("a")  # short_title 为空
        payload = build_papers_payload(FakeDB(unmarked=[paper]))
        assert "suggested_short_title" in payload["sections"]["unmarked"][0]

    def test_short_title_present_skips_suggestion(self):
        paper = _paper("a", short_title="手动标题")
        payload = build_papers_payload(FakeDB(unmarked=[paper]))
        row = payload["sections"]["unmarked"][0]
        assert row["short_title"] == "手动标题"
        assert "suggested_short_title" not in row
