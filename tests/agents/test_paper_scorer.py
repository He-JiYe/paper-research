"""PaperScorer Agent 测试"""

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.paper_scorer import LLMResult, PaperScorer


class TestLLMResult:
    def test_llm_result_creation(self):
        result = LLMResult(
            summary="Good paper", remark="important", reason="Novel", score=0.9
        )
        assert result.summary == "Good paper"
        assert result.remark == "important"
        assert result.score == 0.9


class TestScore:
    def test_score_no_api_key_fallback(self):
        """无 api_key 时使用 fallback"""
        scorer = PaperScorer()
        result = scorer.score(
            "Test Title", "This is an abstract about novel breakthrough methods."
        )
        assert result is not None
        assert isinstance(result, LLMResult)
        assert result.remark in ("useful", "browse")

    def test_score_success(self):
        """成功调用 LLM 评分"""
        scorer = PaperScorer(api_key="sk-test")
        with patch.object(scorer, "_call", return_value=json_response()):
            result = scorer.score("Test Title", "Abstract text", categories="cs.CV")
            assert result is not None
            assert result.remark == "important"
            assert result.score == 0.92

    def test_score_fallback_on_llm_failure(self):
        """LLM 失败时使用 fallback"""
        scorer = PaperScorer(api_key="sk-test")
        with patch.object(scorer, "_call", return_value=None):
            result = scorer.score(
                "Test Title", "Abstract about novel breakthrough methods."
            )
            assert result is not None
            assert isinstance(result, LLMResult)

    def test_score_fallback_on_invalid_json(self):
        """LLM 返回无效 JSON 时使用 fallback"""
        scorer = PaperScorer(api_key="sk-test")
        with patch.object(scorer, "_call", return_value="Not JSON"):
            result = scorer.score("Test", "Abstract")
            assert result is not None

    def test_score_score_clamping(self):
        """评分被限制在 0-1 范围内"""
        scorer = PaperScorer(api_key="sk-test")

        with patch.object(
            scorer,
            "_call",
            return_value='{"summary":"S","remark":"useful","reason":"R","score":1.5}',
        ):
            result = scorer.score("Title", "Abstract")
            assert result.score == 1.0

        with patch.object(
            scorer,
            "_call",
            return_value='{"summary":"S","remark":"useful","reason":"R","score":-0.5}',
        ):
            result = scorer.score("Title", "Abstract")
            assert result.score == 0.0


class TestScoreAsync:
    @pytest.mark.asyncio
    async def test_score_async_no_api_key(self):
        scorer = PaperScorer()
        result = await scorer.score_async("Title", "Abstract")
        assert result is not None
        assert isinstance(result, LLMResult)

    @pytest.mark.asyncio
    async def test_score_async_success(self):
        scorer = PaperScorer(api_key="sk-test")
        with patch.object(
            scorer, "_call_async", AsyncMock(return_value=json_response())
        ):
            result = await scorer.score_async("Title", "Abstract")
            assert result is not None
            assert result.remark == "important"


class TestBatchScoring:
    def test_score_batch_empty(self):
        scorer = PaperScorer()
        results = scorer.score_batch([])
        assert results == []

    def test_score_batch(self):
        scorer = PaperScorer(api_key="sk-test")
        papers = [
            {"title": "Paper 1", "abstract": "Abstract 1", "categories": "cs.CV"},
            {"title": "Paper 2", "abstract": "Abstract 2", "categories": "cs.LG"},
        ]
        with patch.object(
            scorer, "score", return_value=LLMResult("S", "useful", "R", 0.7)
        ):
            results = scorer.score_batch(papers)
            assert len(results) == 2
            assert all(isinstance(r, LLMResult) for r in results)

    @pytest.mark.asyncio
    async def test_score_batch_async_empty(self):
        scorer = PaperScorer()
        results = await scorer.score_batch_async([])
        assert results == []

    @pytest.mark.asyncio
    async def test_score_batch_async_success(self):
        scorer = PaperScorer(api_key="sk-test")
        papers = [
            {"title": "Paper 1", "abstract": "Abstract 1", "categories": "cs.CV"},
        ]
        with patch.object(
            scorer,
            "score_async",
            AsyncMock(return_value=LLMResult("S", "important", "R", 0.9)),
        ):
            results = await scorer.score_batch_async(papers)
            assert len(results) == 1
            assert results[0].remark == "important"

    @pytest.mark.asyncio
    async def test_score_batch_async_fallback_on_exception(self):
        scorer = PaperScorer(api_key="sk-test")
        papers = [{"title": "Paper", "abstract": "Abstract", "categories": ""}]
        with patch.object(
            scorer, "score_async", AsyncMock(side_effect=Exception("Failed"))
        ):
            results = await scorer.score_batch_async(papers)
            assert len(results) == 1
            # 异常应触发 fallback
            assert results[0] is not None


class TestFallback:
    def test_fallback_high_keywords(self):
        """高价值关键词触发 useful"""
        scorer = PaperScorer()
        result = scorer._fallback(
            "A Novel Breakthrough: State-of-the-Art Method",
            "This paper proposes a novel framework that represents a breakthrough in the field.",
        )
        assert result.remark == "useful"
        assert result.score >= 0.7

    def test_fallback_medium_keywords(self):
        """中等关键词触发 browse"""
        scorer = PaperScorer()
        result = scorer._fallback(
            "An Effective Approach",
            "An efficient and robust method for practical applications.",
        )
        assert result.remark == "browse"

    def test_fallback_no_keywords(self):
        """无关键词时返回 browse"""
        scorer = PaperScorer()
        result = scorer._fallback("Simple Title", "Short abstract")
        assert result.remark == "browse"

    def test_fallback_summary_truncation(self):
        """长摘要被截断"""
        scorer = PaperScorer()
        long_abstract = "A" * 500
        result = scorer._fallback("Title", long_abstract)
        assert len(result.summary) <= 303 + 3  # 300 + "..."


def json_response():
    """返回模拟的 LLM JSON 响应"""
    return '{"summary":"Comprehensive survey","remark":"important","reason":"Timely and comprehensive","score":0.92}'
