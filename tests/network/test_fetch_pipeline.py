"""Tests for run_fetch_pipeline"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.network.fetch_pipeline import run_fetch_pipeline


# ─── Fake LLM score result ─────────────────────────────────
class MockScoreResult:
    """Mimics LLMResult (summary / remark / reason / score)."""

    def __init__(
        self,
        summary="Test summary",
        remark="Test remark",
        reason="Test reason",
        score=0.85,
    ):
        self.summary = summary
        self.remark = remark
        self.reason = reason
        self.score = score


# ─── Helpers ────────────────────────────────────────────────

def _make_papers():
    """Return a fresh list of paper dicts (avoids cross-test mutation)."""
    return [
        {
            "arxiv_id": "2401.00001",
            "version": 1,
            "title": "Test-Time Adaptation with Transformers",
            "authors": "Alice Zhang, Bob Li",
            "abstract": "We propose a novel method for test-time adaptation.",
            "url": "https://arxiv.org/abs/2401.00001",
            "primary_category": "cs.LG",
            "categories": "cs.LG, cs.CV",
            "published": "2024-01-01",
            "arxiv_updated": "2024-01-05T12:00:00Z",
            "keyword_match": "test-time adaptation",
        },
        {
            "arxiv_id": "2401.00002",
            "version": 1,
            "title": "Out-of-Distribution Detection via Energy Scoring",
            "authors": "Chen Wang, Dan Liu",
            "abstract": "Energy-based methods for OOD detection.",
            "url": "https://arxiv.org/abs/2401.00002",
            "primary_category": "cs.LG",
            "categories": "cs.LG",
            "published": "2024-01-02",
            "arxiv_updated": "2024-01-06T12:00:00Z",
            "keyword_match": "out-of-distribution detection",
        },
    ]


# Existing-paper stubs for version-detection tests.
# Only the fields used by run_fetch_pipeline are populated.
OLD_PAPER = {
    "arxiv_id": "2401.00001",
    "version": 1,
    "arxiv_updated": "2023-12-01T00:00:00Z",  # older than fetched version
}

SAME_PAPER = {
    "arxiv_id": "2401.00001",
    "version": 1,
    "arxiv_updated": "2024-01-05T12:00:00Z",  # same as fetched version
}


# ─── Fixture: common patches ────────────────────────────────

@pytest.fixture
def pipeline_mocks():
    """Start all standard patches and yield a dict of mock references.

    Callers can override individual mocks (e.g. *side_effect*) before
    invoking ``run_fetch_pipeline``.
    """
    source = MagicMock()
    source.fetch_all = AsyncMock(return_value=([], 0, 0))

    entries = [
        ("get_source",              patch("src.network.factory.get_source", return_value=source)),
        ("get_paper",               patch("src.db.get_paper", return_value=None)),
        ("insert_paper",            patch("src.db.insert_paper")),
        ("update_paper_version",    patch("src.db.update_paper_version")),
        ("touch_paper",             patch("src.db.touch_paper")),
        ("update_paper_summary",    patch("src.db.update_paper_summary")),
        ("insert_fetch_log",        patch("src.db.insert_fetch_log")),
        ("get_papers_for_summary",  patch("src.db.get_papers_for_summary", return_value={})),
        ("gen_summary_html",        patch("src.serve.renderer.generate_summary_html")),
        ("gen_landing_html",        patch("src.serve.renderer.generate_summary_html")),
        ("gen_notes_html",          patch("src.serve.renderer.generate_notes_index_html")),
        ("PaperScorer",             patch("src.agents.PaperScorer")),
    ]

    mocks = {}
    for name, p in entries:
        mocks[name] = p.start()

    mocks["_source"] = source
    mocks["_scorer"] = mocks["PaperScorer"].return_value

    yield mocks

    for _, p in reversed(entries):
        p.stop()


# ═══════════════════════════════════════════════════════════
# 1.  Dry-run  mode
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dry_run_returns_early(
    db_conn, mock_settings, active_keywords, pipeline_mocks,
):
    """``dry_run=True`` should skip every DB-write call."""
    papers = _make_papers()
    m = pipeline_mocks
    m["_source"].fetch_all = AsyncMock(return_value=(papers, 2, 0))

    result = await run_fetch_pipeline(
        conn=db_conn, settings=mock_settings,
        keywords=active_keywords,
        max_results=50, mode="incremental", dry_run=True,
    )

    assert result["fetched"] == 2
    assert result["new"] == 0
    assert result["updated"] == 0
    assert result["summarized"] == 0
    assert result["papers_fetched"] == papers

    m["insert_paper"].assert_not_called()
    m["update_paper_version"].assert_not_called()
    m["touch_paper"].assert_not_called()
    m["update_paper_summary"].assert_not_called()
    m["insert_fetch_log"].assert_not_called()
    m["get_paper"].assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_with_papers(
    db_conn, mock_settings, active_keywords, pipeline_mocks, capsys,
):
    """``dry_run=True`` prints the paper count and lists first 10 IDs."""
    papers = _make_papers()
    m = pipeline_mocks
    m["_source"].fetch_all = AsyncMock(return_value=(papers, 2, 0))

    await run_fetch_pipeline(
        conn=db_conn, settings=mock_settings,
        keywords=active_keywords,
        max_results=50, mode="incremental", dry_run=True,
    )

    out = capsys.readouterr().out
    assert "共获取 2 篇论文" in out
    assert "2401.00001" in out
    assert "2401.00002" in out


# ═══════════════════════════════════════════════════════════
# 2.  Insert / Update / Touch  logic
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_new_papers_inserted(
    db_conn, mock_settings, active_keywords, pipeline_mocks,
):
    """When ``get_paper`` returns ``None``, every paper is inserted."""
    papers = _make_papers()
    m = pipeline_mocks
    m["_source"].fetch_all = AsyncMock(return_value=(papers, 2, 0))
    # get_paper already returns None by default

    await run_fetch_pipeline(
        conn=db_conn, settings=mock_settings,
        keywords=active_keywords,
        max_results=50, mode="incremental", dry_run=False,
    )

    assert m["insert_paper"].call_count == 2
    today = datetime.date.today().isoformat()
    for call_args in m["insert_paper"].call_args_list:
        conn_arg, paper = call_args[0]
        assert conn_arg is db_conn
        assert paper["arxiv_id"] in ("2401.00001", "2401.00002")
        assert paper["fetch_date"] == today

    m["update_paper_version"].assert_not_called()
    m["touch_paper"].assert_not_called()


@pytest.mark.asyncio
async def test_version_update(
    db_conn, mock_settings, active_keywords, pipeline_mocks,
):
    """Existing paper with older ``arxiv_updated`` triggers ``update_paper_version``."""
    papers = _make_papers()
    m = pipeline_mocks
    m["_source"].fetch_all = AsyncMock(return_value=(papers, 2, 0))

    def _get_paper(conn, aid):
        return OLD_PAPER if aid == "2401.00001" else None
    m["get_paper"].side_effect = _get_paper

    await run_fetch_pipeline(
        conn=db_conn, settings=mock_settings,
        keywords=active_keywords,
        max_results=50, mode="incremental", dry_run=False,
    )

    # Paper 2401.00001 exists with an older timestamp → update
    m["update_paper_version"].assert_called_once()
    args = m["update_paper_version"].call_args[0]
    assert args[0] is db_conn
    assert args[1] == "2401.00001"
    assert args[2] == 1          # version from fetched paper
    assert args[3] == "2024-01-05T12:00:00Z"
    assert len(args[4]) == 10    # today ISO date

    # Paper 2401.00002 is brand-new → insert
    m["insert_paper"].assert_called_once()
    inserted_id = m["insert_paper"].call_args[0][1]["arxiv_id"]
    assert inserted_id == "2401.00002"

    m["touch_paper"].assert_not_called()


@pytest.mark.asyncio
async def test_existing_paper_touched(
    db_conn, mock_settings, active_keywords, pipeline_mocks,
):
    """Existing paper with same ``arxiv_updated`` triggers ``touch_paper``."""
    papers = _make_papers()
    m = pipeline_mocks
    m["_source"].fetch_all = AsyncMock(return_value=(papers, 2, 0))

    def _get_paper(conn, aid):
        return SAME_PAPER if aid == "2401.00001" else None
    m["get_paper"].side_effect = _get_paper

    await run_fetch_pipeline(
        conn=db_conn, settings=mock_settings,
        keywords=active_keywords,
        max_results=50, mode="incremental", dry_run=False,
    )

    # Paper 2401.00001 exists with the same timestamp → touch
    m["touch_paper"].assert_called_once()
    args = m["touch_paper"].call_args[0]
    assert args[0] is db_conn
    assert args[1] == "2401.00001"
    assert len(args[2]) == 10    # today ISO date

    # Paper 2401.00002 is brand-new → insert
    m["insert_paper"].assert_called_once()
    m["update_paper_version"].assert_not_called()


# ═══════════════════════════════════════════════════════════
# 3.  LLM summarization
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_llm_summarize_with_api_key(
    db_conn, mock_settings, active_keywords, pipeline_mocks,
):
    """With ``api_key`` set, ``score_batch_async`` is called."""
    mock_settings.llm.api_key = "test-key"
    papers = _make_papers()
    m = pipeline_mocks
    m["_source"].fetch_all = AsyncMock(return_value=(papers, 2, 0))

    results = [
        MockScoreResult(summary="S1", score=0.9),
        MockScoreResult(summary="S2", score=0.8),
    ]
    mock_scorer = m["_scorer"]
    mock_scorer.score_batch_async = AsyncMock(return_value=results)

    await run_fetch_pipeline(
        conn=db_conn, settings=mock_settings,
        keywords=active_keywords,
        max_results=50, mode="incremental", dry_run=False,
    )

    mock_scorer.score_batch_async.assert_awaited_once()
    papers_arg = mock_scorer.score_batch_async.call_args[0][0]
    assert len(papers_arg) == 2
    assert [p["arxiv_id"] for p in papers_arg] == ["2401.00001", "2401.00002"]

    # Both papers should get a summary written
    assert m["update_paper_summary"].call_count == 2


@pytest.mark.asyncio
async def test_llm_summarize_without_api_key(
    db_conn, mock_settings, active_keywords, pipeline_mocks,
):
    """Without ``api_key``, per-paper synchronous ``score`` is called."""
    # api_key is empty by default in mock_settings
    papers = _make_papers()
    m = pipeline_mocks
    m["_source"].fetch_all = AsyncMock(return_value=(papers, 2, 0))

    mock_scorer = m["_scorer"]
    mock_scorer.score = MagicMock(return_value=MockScoreResult(score=0.75))

    await run_fetch_pipeline(
        conn=db_conn, settings=mock_settings,
        keywords=active_keywords,
        max_results=50, mode="incremental", dry_run=False,
    )

    # Two papers → two calls to score (via run_in_executor)
    assert mock_scorer.score.call_count == 2

    # Both papers get a summary written
    assert m["update_paper_summary"].call_count == 2


# ═══════════════════════════════════════════════════════════
# 4.  Fetch log
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fetch_log_inserted(
    db_conn, mock_settings, active_keywords, pipeline_mocks,
):
    """``insert_fetch_log`` is called with the correct cumulative stats."""
    papers = _make_papers()
    m = pipeline_mocks
    m["_source"].fetch_all = AsyncMock(return_value=(papers, 2, 0))
    # get_paper returns None → 2 new, 0 updated

    await run_fetch_pipeline(
        conn=db_conn, settings=mock_settings,
        keywords=active_keywords,
        max_results=50, mode="incremental", dry_run=False,
    )

    m["insert_fetch_log"].assert_called_once()
    args, kwargs = m["insert_fetch_log"].call_args

    assert args[0] is db_conn
    assert kwargs["keywords_used"] == 2
    assert kwargs["papers_fetched"] == 2
    assert kwargs["papers_new"] == 2
    assert kwargs["papers_updated"] == 0
    assert kwargs["papers_summarized"] == 2   # new + updated
    assert kwargs["status"] == "success"
