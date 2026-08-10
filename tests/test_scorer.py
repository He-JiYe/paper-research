"""PaperScorer 测试 — 覆盖 LLM 评分、结构化输出提取、概率分布计算、fallback

当前 scorer.py 为纯函数 + PaperScorer 类内联实现（不再有 PocketFlow 节点 API）。
"""

import asyncio
import json
from unittest.mock import patch

import pytest
import src.scorer.prompt as prompt_mod
from src.core.text import tokenize
from src.scorer import LLMResult, PaperScorer, ScoreSource, compute_keyword_relevance
from src.scorer.fallback import fallback_score
from src.scorer.parse import (
    expected_score,
    extract_json,
    normalize_distribution,
    try_build_result,
)
from src.scorer.prompt import SUMMARIZE_PROMPT, build_summarize_prompt, load_examples
from src.scorer.provider import OpenAIProvider


def _provider(api_key: str = "", **kw) -> OpenAIProvider:
    """构造 OpenAI provider（不联网：_check_llm 由调用方 patch 或走无 key 短路）。"""
    return OpenAIProvider(
        model="test-model",
        api_base="https://api.test",
        api_key=api_key,
        temperature=0.3,
        max_tokens=2000,
        **kw,
    )


# ════════════════════════════════════════════════════════════════════
#  纯函数：compute_keyword_relevance
# ════════════════════════════════════════════════════════════════════


def test_keyword_relevance_empty_keyword():
    """空关键词返回中性分 0.5"""
    assert compute_keyword_relevance("any title", "any abstract", "") == 0.5


def test_keyword_relevance_full_match():
    """关键词在标题+摘要全部命中 → 0.95（封顶）"""
    score = compute_keyword_relevance(
        "test-time adaptation paper",
        "we propose test-time adaptation for neural networks",
        "test-time adaptation",
    )
    assert score == 0.95


def test_keyword_relevance_no_match():
    """完全无命中 → 0.10（下限）"""
    assert compute_keyword_relevance("hello world", "nothing here", "quantum") == 0.10


def test_keyword_relevance_title_weights_more():
    """标题命中的权重是摘要的 2 倍：仅标题命中应高于仅摘要命中"""
    title_only = compute_keyword_relevance(
        "transformer architecture", "unrelated abstract", "transformer"
    )
    abstract_only = compute_keyword_relevance(
        "unrelated title", "we use transformer in our experiments", "transformer"
    )
    assert title_only > abstract_only


# ════════════════════════════════════════════════════════════════════
#  薄原语：normalize_distribution / expected_score / extract_json / tokenize
# ════════════════════════════════════════════════════════════════════


def test_normalize_and_expected_score():
    """均匀分布 {0..4 各 0.2} → 归一化后期望 2.0 → 得分 0.4"""
    probs = normalize_distribution({"0": 0.2, "1": 0.2, "2": 0.2, "3": 0.2, "4": 0.2, "5": 0.0})
    assert probs is not None
    assert expected_score(probs) == pytest.approx(0.4)


def test_normalize_empty_invalid():
    """空分布 → 无效，返回 None"""
    assert normalize_distribution({}) is None
    assert normalize_distribution(None) is None


def test_normalize_negative_prob_invalid():
    """负概率 → 无效，返回 None"""
    assert normalize_distribution({"0": -0.1, "1": 0.5}) is None


def test_normalize_non_numeric_invalid():
    """非数字概率 → 无效，返回 None"""
    assert normalize_distribution({"0": "abc"}) is None


def test_normalize_and_expected_score_normalizes():
    """概率和偏离 1.0 时自动归一化"""
    probs = normalize_distribution({"0": 2.0, "5": 0.0})
    assert probs is not None
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert expected_score(probs) == 0.0


def test_normalize_drops_non_numeric_keys():
    """混入非数字/额外键被丢弃，合法档位保留，期望分正常"""
    probs = normalize_distribution({"4": 1.0, "high": 0.0, "note": 0.5})
    assert probs == {"4": 1.0}
    assert expected_score(probs) == pytest.approx(0.8)


def test_normalize_all_invalid_keys_returns_none():
    """全部 key 非数字 → 无效，返回 None（触发重试/fallback）"""
    assert normalize_distribution({"a": 1.0, "high": 0.5}) is None


def test_normalize_int_keys_coerced():
    """整型键 {5: 0.5} 经 str 归一化后合法，不会让 expected_score 崩溃"""
    probs = normalize_distribution({5: 0.5, 4: 0.5})
    assert probs is not None
    assert "5" in probs and "4" in probs
    assert expected_score(probs) == pytest.approx(0.9)


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_markdown_block():
    content = '```json\n{"summary": "中文摘要", "remark": "useful"}\n```'
    assert extract_json(content) == {"summary": "中文摘要", "remark": "useful"}


def test_extract_json_embedded():
    """JSON 夹杂在自然语言中，取首个 { 到末个 }"""
    content = '说明：结果如下 {"score": 0.8, "ok": true} 完。'
    assert extract_json(content) == {"score": 0.8, "ok": True}


def test_extract_json_invalid():
    assert extract_json("not json at all") is None
    assert extract_json("") is None
    assert extract_json(None) is None


def test_tokenize():
    """连字符复合词作为一个 token；统一转小写"""
    tokens = tokenize("Test-Time Adaptation v2: 中英混排?")
    assert tokens == ["test-time", "adaptation", "v2"]
    assert all(t.islower() for t in tokens)


# ════════════════════════════════════════════════════════════════════
#  _fallback：无 API Key 的关键词加权评分
# ════════════════════════════════════════════════════════════════════


def test_fallback_with_keyword_high_match():
    """关键词高匹配 → useful"""
    result = fallback_score(
        "Test-Time Adaptation with Transformers",
        "We propose a novel method for test-time adaptation using transformer architectures.",
        "test-time adaptation",
    )
    assert isinstance(result, LLMResult)
    assert result.remark == "useful"
    assert result.score >= 0.60


def test_fallback_with_keyword_medium_match_browse():
    """关键词部分匹配 → browse（0.40~0.60 档）"""
    result = fallback_score("Deep Dive", "Learning methods for classification", "deep learning")
    assert result.remark == "browse"


def test_fallback_with_keyword_low_match():
    """关键词低匹配 → skip"""
    result = fallback_score(
        "A Study of Concrete Mix Design",
        "We investigate compressive strength of concrete specimens.",
        "transformer neural network",
    )
    assert result.remark == "skip"


def test_fallback_without_keyword():
    """无关键词 → browse（学术词信号弱时最低 browse）"""
    result = fallback_score("A Novel Framework", "We present a novel framework", "")
    assert result.remark == "browse"
    assert result.score >= 0.30


def test_fallback_without_keyword_high_terms_useful():
    """无关键词但高价值学术词信号强 → useful"""
    result = fallback_score(
        "A Novel Breakthrough Framework",
        "We present a novel state-of-the-art theoretical framework",
    )
    assert result.remark == "useful"


# ════════════════════════════════════════════════════════════════════
#  score / score_async：无 Key 走 fallback，有 Key 走 LLM
# ════════════════════════════════════════════════════════════════════


def _ready_scorer(api_key: str = "sk-test", max_retries: int = 2) -> PaperScorer:
    """构造 LLM 就绪的 PaperScorer（跳过初始化时的真实连接检测）。"""
    with patch.object(PaperScorer, "_check_llm", return_value=(True, ScoreSource.LLM)):
        return PaperScorer(_provider(api_key=api_key), max_retries=max_retries)


def test_score_no_api_key_uses_fallback(monkeypatch):
    """无 api_key 时 score() 返回 fallback 结果"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    scorer = PaperScorer(_provider())
    result = scorer.score("Some Title", "Some abstract", keyword="some")
    assert isinstance(result, LLMResult)
    assert result.remark in ("useful", "browse", "skip")


def test_score_with_api_key_parses_llm_json():
    """api_key 存在且 _call 返回合法 JSON → 解析为 LLMResult"""
    scorer = _ready_scorer(api_key="sk-test")
    payload = {
        "summary": "核心贡献是提出 X",
        "remark": "important",
        "reason": "范式创新",
        "score_distribution": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0.5, "5": 0.5},
    }
    with patch.object(scorer, "_call", return_value=json.dumps(payload, ensure_ascii=False)):
        result = scorer.score("Title", "Abstract", keyword="kw")
    assert isinstance(result, LLMResult)
    assert result.remark == "important"
    assert result.reason == "范式创新"
    # 期望 = (4*0.5 + 5*0.5) / 5 = 0.9
    assert result.score == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_score_async_no_api_key_uses_fallback(monkeypatch):
    """无 api_key 时 score_async() 返回 fallback 结果"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    scorer = PaperScorer(_provider())
    result = await scorer.score_async("Some Title", "Some abstract", keyword="some")
    assert isinstance(result, LLMResult)


def test_score_batch_async_empty():
    """空论文列表 → 空结果"""
    result = asyncio.run(PaperScorer(_provider()).score_batch_async([]))
    assert result == []


def test_score_batch_async_falls_back_without_key(monkeypatch):
    """无 Key 时批量异步评分全部走 fallback（返回 LLMResult 而非 None）"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    papers = [
        {"title": "T1", "abstract": "A1", "keyword_match": "kw"},
        {"title": "T2", "abstract": "A2", "keyword_match": "kw"},
    ]
    results = asyncio.run(PaperScorer(_provider()).score_batch_async(papers))
    assert len(results) == 2
    assert all(isinstance(r, LLMResult) for r in results)


def test_score_source_no_api_key(monkeypatch):
    """无 Key → fallback，source=no_api_key"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    scorer = PaperScorer(_provider())
    result = scorer.score("T", "A", keyword="kw")
    assert result.source == ScoreSource.FALLBACK_NO_KEY


def test_score_source_connection_failure():
    """初始化检测连接失败 → 全程 fallback，source=connection_failed"""
    with patch.object(
        PaperScorer,
        "_check_llm",
        return_value=(False, ScoreSource.FALLBACK_CONNECTION),
    ):
        scorer = PaperScorer(_provider(api_key="sk-test"))
    result = scorer.score("T", "A", keyword="kw")
    assert result.source == ScoreSource.FALLBACK_CONNECTION


def test_score_source_connection_failure_on_call_none():
    """_call 返回 None（provider 调用失败）→ fallback，source=connection_failed"""
    scorer = _ready_scorer(api_key="sk-test")
    with patch.object(scorer, "_call", return_value=None):
        result = scorer.score("T", "A", keyword="kw")
    assert result.source == ScoreSource.FALLBACK_CONNECTION


def test_from_settings_builds_provider():
    """from_settings 按 settings.llm 构造 provider 化的评分器"""
    from types import SimpleNamespace

    from src.core.config import LLMConfig

    settings = SimpleNamespace(llm=LLMConfig(api_key="sk-test"))
    scorer = PaperScorer.from_settings(settings)
    assert scorer._provider is not None
    assert scorer._llm_ready is False  # 无 key → 走 fallback（真实 _check_llm 不联网）


def test_from_settings_wires_max_concurrent():
    """from_settings 把 settings.llm.max_concurrent 注入评分器（本地 Ollama 默认 1）"""
    from types import SimpleNamespace

    from src.core.config import LLMConfig

    settings = SimpleNamespace(llm=LLMConfig(api_key="sk-test", max_concurrent=2))
    with patch.object(PaperScorer, "_check_llm", return_value=(True, ScoreSource.LLM)):
        scorer = PaperScorer.from_settings(settings)
    assert scorer.max_concurrent == 2


def test_score_batch_async_uses_instance_max_concurrent():
    """未显式传 max_concurrent 时用实例值（构造注入），Semaphore 按该值创建"""
    real_sem = asyncio.Semaphore  # 捕获真实类，避免 patch 作用域内自递归
    captured = {}

    def spy_semaphore(value=1):
        captured["n"] = value
        return real_sem(value)

    scorer = PaperScorer(_provider(), max_concurrent=2)
    papers = [
        {"title": "T1", "abstract": "A1", "keyword_match": "kw"},
        {"title": "T2", "abstract": "A2", "keyword_match": "kw"},
    ]
    with patch.object(asyncio, "Semaphore", side_effect=spy_semaphore):
        asyncio.run(scorer.score_batch_async(papers))
    assert captured["n"] == 2


def test_score_source_invalid_output_retries_then_fallback():
    """输出无效 → 带错误说明重试 → 耗尽 fallback，source=invalid_output"""
    scorer = _ready_scorer(api_key="sk-test", max_retries=2)
    calls = []

    def fake_call(system_prompt, user_prompt):
        calls.append(user_prompt)
        return "not json"

    with patch.object(scorer, "_call", side_effect=fake_call):
        result = scorer.score("T", "A", keyword="kw")
    assert result.source == ScoreSource.FALLBACK_INVALID
    assert len(calls) == 3  # 首次 + 2 次重试
    assert "错误反馈" in calls[1]  # 重试时附带上一次的错误说明


def test_score_retry_succeeds_on_second_attempt():
    """第一次无效、重试成功 → LLM judge，source=llm"""
    scorer = _ready_scorer(api_key="sk-test")
    payload = json.dumps(
        {
            "summary": "s",
            "remark": "useful",
            "reason": "r",
            "score_distribution": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 1, "5": 0},
        }
    )
    n = {"count": 0}

    def fake_call(system_prompt, user_prompt):
        n["count"] += 1
        return "bad json" if n["count"] == 1 else payload

    with patch.object(scorer, "_call", side_effect=fake_call):
        result = scorer.score("T", "A", keyword="kw")
    assert result.source == ScoreSource.LLM
    assert result.remark == "useful"


def test_score_llm_judge_success():
    """正常 judge → source=llm"""
    scorer = _ready_scorer(api_key="sk-test")
    payload = json.dumps(
        {
            "summary": "s",
            "remark": "important",
            "reason": "r",
            "score_distribution": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0.5, "5": 0.5},
        }
    )
    with patch.object(scorer, "_call", return_value=payload):
        result = scorer.score("T", "A", keyword="kw")
    assert result.source == ScoreSource.LLM
    assert result.score == pytest.approx(0.9)


def test_summarize_prompt_defined():
    """SUMMARIZE_PROMPT 是含 {title}/{keyword}/{examples} 占位符的可格式化字符串"""
    assert isinstance(SUMMARIZE_PROMPT, str)
    assert "{title}" in SUMMARIZE_PROMPT
    assert "{keyword}" in SUMMARIZE_PROMPT
    assert "{examples}" in SUMMARIZE_PROMPT


def test_load_examples_by_keyword(tmp_path, monkeypatch, caplog):
    """load_examples 按关键词读 {keyword}-few-shot.txt；缺失时创建空文件并记日志"""
    monkeypatch.setattr(prompt_mod, "EXAMPLES_DIR", tmp_path)
    assert load_examples("") == ""

    # 缺失 → 创建空文件 + warning 日志，返回空
    assert load_examples("kw") == ""
    assert (tmp_path / "kw-few-shot.txt").exists()
    assert any("请补充样例" in r.message for r in caplog.records)

    # 已有文件 → 读取内容
    (tmp_path / "kw-few-shot.txt").write_text("示例内容", encoding="utf-8")
    assert load_examples("kw") == "示例内容"


def test_try_build_result_rejects_placeholder_remark():
    """LLM 把占位说明抄进 remark 时拒收并给错误说明（触发重试/fallback）"""
    content = json.dumps(
        {
            "summary": "s",
            "remark": "从以下4个等级中选择一个",
            "reason": "r",
            "score_distribution": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 1, "5": 0},
        }
    )
    result, err = try_build_result(content, "abstract")
    assert result is None
    assert "remark" in err


def test_try_build_result_accepts_case_insensitive_remark():
    """大小写变体归一化为小写合法值"""
    content = json.dumps(
        {
            "summary": "s",
            "remark": "Useful",
            "reason": "r",
            "score_distribution": {"4": 1.0},
        }
    )
    result, _ = try_build_result(content, "abstract")
    assert result is not None
    assert result.remark == "useful"


def test_build_prompt_injects_source_updated(tmp_path, monkeypatch):
    """prompt 提供来源与更新时间（LLM 辅助判断）"""
    monkeypatch.setattr(prompt_mod, "EXAMPLES_DIR", tmp_path)
    p = build_summarize_prompt(
        keyword="kw",
        title="T",
        abstract="A",
        categories="C",
        source="arxiv",
        updated="2025-01-21T08:00:00+00:00",
    )
    assert "来源:arxiv" in p
    assert "更新时间:2025-01-21" in p  # ISO 时间戳截取为日期


def test_build_prompt_injects_examples(tmp_path, monkeypatch):
    """build_summarize_prompt 按关键词注入示例；无示例文件时创建空文件且不注入"""
    monkeypatch.setattr(prompt_mod, "EXAMPLES_DIR", tmp_path)
    (tmp_path / "kw-few-shot.txt").write_text("标题: 某示例\nremark: important", encoding="utf-8")

    p = build_summarize_prompt(keyword="kw", title="T", abstract="A", categories="C")
    assert "以下是关键词「kw」的评分示例" in p
    assert "标题: 某示例" in p
    assert "标题:T" in p

    # 无文件的关键词：load_examples 创建空文件并返回空 → 不注入示例段
    p2 = build_summarize_prompt(keyword="no-examples", title="T", abstract="A", categories="C")
    assert "评分示例" not in p2
    assert (tmp_path / "no-examples-few-shot.txt").exists()


# ════════════════════════════════════════════════════════════════════
#  LLMResult dataclass
# ════════════════════════════════════════════════════════════════════


def test_llm_result_defaults():
    result = LLMResult(summary="s", remark="browse", reason="r", score=0.5)
    assert result.score_distribution is None
