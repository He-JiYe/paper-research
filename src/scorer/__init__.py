"""评分包：PaperScorer + LLMResult + fallback。

入口为 ``from src.scorer import PaperScorer``；数据模型在 ``core.score``。
"""

from src.core.score import LLMResult, ScoreSource
from src.scorer.fallback import compute_keyword_relevance, fallback_score
from src.scorer.llm import PaperScorer

__all__ = [
    "LLMResult",
    "PaperScorer",
    "ScoreSource",
    "compute_keyword_relevance",
    "fallback_score",
]
