"""pipeline.score.score_rows 唯一评分+写回循环测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.core.score import LLMResult, ScoreSource
from src.pipeline.score import score_rows


def _llm_result(remark="useful"):
    return LLMResult(
        summary="s",
        remark=remark,
        reason="r",
        score=0.8,
        score_distribution=None,
        source=ScoreSource.LLM,
    )


def _scorer(results):
    scorer = MagicMock()
    scorer.score_batch_async = AsyncMock(return_value=results)
    return scorer


def test_score_rows_empty():
    count, rows = asyncio.run(score_rows(MagicMock(), []))
    assert count == 0
    assert rows == []


def test_score_rows_writes_fields():
    scorer = _scorer([_llm_result()])
    rows = [{"title": "T", "abstract": "A"}]
    count, out = asyncio.run(score_rows(scorer, rows))
    assert count == 1
    assert out[0]["llm_remark"] == "useful"
    assert out[0]["llm_score"] == 0.8
    assert out[0]["score_source"] == "llm"


def test_score_rows_async_exception_propagates():
    """score_batch_async 内部已 ``return_exceptions`` 兜底；真正的异常不再降级串行，直接上抛。"""
    scorer = MagicMock()
    scorer.score_batch_async.side_effect = RuntimeError("async down")
    rows = [{"title": "T"}]
    with pytest.raises(RuntimeError):
        asyncio.run(score_rows(scorer, rows))
