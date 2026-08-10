"""关键词相关性兜底评分（无 API Key / LLM 连接失败 / 输出无效时使用）。

- ``compute_keyword_relevance``：唯一的关键词相关性映射入口（空关键词 → 中性 0.5）；
- ``fallback_score``：综合学术词信号产出 LLMResult。
"""

from src.core.score import LLMResult, ScoreSource
from src.core.text import relevance_score, tokenize

# ── 评分常量（集中调参，值与旧字面量完全一致）────────────────
_SUMMARY_ABSTRACT_LIMIT = 300  # fallback 摘要展示截断长度
_RELEVANCE_NEUTRAL = 0.5  # 空关键词中性分
_RELEVANCE_FLOOR = 0.10  # 相关性映射下限
_RELEVANCE_CEIL = 0.95  # 相关性映射上限
_RELEVANCE_SCALE = 0.85  # 相关性映射斜率
_HIGH_BOOST = 0.08  # 高价值学术词 boost
_MEDIUM_BOOST = 0.04  # 中价值学术词 boost
_HIGH_RAW = 0.12  # 无关键词时高价值词原始加分
_MEDIUM_RAW = 0.06  # 无关键词时中价值词原始加分
_NO_KW_BASE = 0.25  # 无关键词基准分
_NO_KW_CEIL = 0.85  # 无关键词上限
_USEFUL_KW_THRESHOLD = 0.60  # 有关键词 useful 档阈值
_BROWSE_KW_THRESHOLD = 0.40  # 有关键词 browse 档阈值
_SKIP_KW_FLOOR = 0.15  # skip 档分数下限
_USEFUL_NO_KW_THRESHOLD = 0.65  # 无关键词 useful 档阈值
_BROWSE_NO_KW_FLOOR = 0.30  # 无关键词 browse 档分数下限


def compute_keyword_relevance(title: str, abstract: str, keyword: str) -> float:
    """计算论文标题/摘要与搜索关键词的相关性得分。

    复用 ``core.text.relevance_score`` 的 token 重叠匹配（标题权重 2x），
    将原始匹配度映射到 [_RELEVANCE_FLOOR, _RELEVANCE_CEIL] 供评分阈值使用；
    空关键词返回中性 _RELEVANCE_NEUTRAL。
    """
    if not keyword or not tokenize(keyword):
        return _RELEVANCE_NEUTRAL
    raw = relevance_score({"title": title, "abstract": abstract}, keyword)
    return round(min(_RELEVANCE_CEIL, _RELEVANCE_FLOOR + raw * _RELEVANCE_SCALE), 2)


# 无 API 时的语义补偿词表
_HIGH_TERMS = {
    "novel",
    "state-of-the-art",
    "breakthrough",
    "theoretical",
    "paradigm",
    "framework",
    "fundamental",
    "first",
}
_MEDIUM_TERMS = {
    "effective",
    "efficient",
    "improved",
    "robust",
    "practical",
    "scalable",
    "real-world",
    "empirical",
}


def fallback_score(
    title: str,
    abstract: str,
    keyword: str = "",
    source: ScoreSource = ScoreSource.FALLBACK_NO_KEY,
) -> LLMResult:
    """关键词加权评分 fallback。

    优先使用 keyword 对 title/abstract 做相关性评分（标题权重 2x），
    叠加高价值学术词评分作为辅助信号；keyword 为空时仅用学术词评分。
    """
    summary = (
        abstract[:_SUMMARY_ABSTRACT_LIMIT] + "..."
        if len(abstract) > _SUMMARY_ABSTRACT_LIMIT
        else abstract
    )

    # ── 主体：keyword 相关性评分（空关键词 → 中性分）──────────
    kw_score = compute_keyword_relevance(title, abstract, keyword)

    # ── 辅助：学术词信号 ─────────────────────────────────────
    all_words = set(tokenize(title)) | set(tokenize(abstract))
    high_matches = sum(1 for t in _HIGH_TERMS if t in all_words)
    medium_matches = sum(1 for t in _MEDIUM_TERMS if t in all_words)
    term_boost = high_matches * _HIGH_BOOST + medium_matches * _MEDIUM_BOOST

    # ── 综合得分 ────────────────────────────────────────────
    if keyword:
        score = min(_RELEVANCE_CEIL, kw_score + term_boost)  # 关键词相关性为主，学术词小幅提升
    else:
        raw = high_matches * _HIGH_RAW + medium_matches * _MEDIUM_RAW
        score = min(_NO_KW_CEIL, _NO_KW_BASE + raw)

    # ── 判定 ────────────────────────────────────────────────
    if keyword:
        if score >= _USEFUL_KW_THRESHOLD:
            remark, reason = "useful", f"标题/摘要与关键词「{keyword}」匹配度较高"
        elif score >= _BROWSE_KW_THRESHOLD:
            remark, reason = "browse", f"标题/摘要与关键词「{keyword}」部分匹配"
        else:
            remark, reason = "skip", f"与关键词「{keyword}」相关性较低"
            score = max(_SKIP_KW_FLOOR, score)
    else:
        if score >= _USEFUL_NO_KW_THRESHOLD:
            remark, reason = "useful", "标题/摘要学术价值较高"
        else:
            remark, reason = "browse", "关键词匹配较少,建议人工判断"
            score = max(_BROWSE_NO_KW_FLOOR, score)

    return LLMResult(
        summary=summary, remark=remark, reason=reason, score=round(score, 2), source=source
    )
