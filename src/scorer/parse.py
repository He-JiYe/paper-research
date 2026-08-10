"""LLM 响应解析原语：从 JSON 内容构建 LLMResult（无 I/O，薄函数组合）。"""

import contextlib
import json
import re

from src.core.score import REMARKS, LLMResult

# LLM 评分分布允许的分数档位（字符串 key，对应 0~5 分）
_VALID_SCORE_KEYS = {"0", "1", "2", "3", "4", "5"}

# 摘要为空时的 fallback 截断长度
_SUMMARY_FALLBACK_LIMIT = 200

# remark 允许的评级（单一来源 core.score.REMARKS；防小模型把占位说明抄进输出）
_VALID_REMARKS = set(REMARKS)


def extract_json(content: str) -> dict | None:
    """从 LLM 响应中提取 JSON 对象（支持纯 JSON 与 ```json``` 包裹）。

    只接受顶层为 dict 的结果；LLM 偶发返回数组/标量时视为无效（返回 None），
    避免后续 ``data.get`` 在非 dict 上抛 AttributeError。
    """
    if not content:
        return None
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if match:
        content = match.group(1).strip()
    parsed: object = None
    with contextlib.suppress(json.JSONDecodeError):
        parsed = json.loads(content)
    if not isinstance(parsed, dict):
        try:
            start = content.index("{")
            end = content.rindex("}")
            parsed = json.loads(content[start : end + 1])
        except (ValueError, json.JSONDecodeError):
            return None
    return parsed if isinstance(parsed, dict) else None


def normalize_distribution(dist: dict) -> dict[str, float] | None:
    """校验并归一化 LLM 给出的 score_distribution（薄原语）。

    规则：非空 dict；key 须为分数档位 "0".."5"（非数字键丢弃，容忍 LLM 附加键）；
    每个概率须为可转 float 的非负数；概率和接近 1.0（0.9~1.1）保留原值，
    越界时归一化到和为 1（guard 除零）。全部 key 无效时返回 None。

    Returns:
        归一化后的分布 {key: prob}；无效返回 None。
    """
    if not isinstance(dist, dict) or len(dist) == 0:
        return None
    probs: dict[str, float] = {}
    total = 0.0
    for k, v in dist.items():
        key = str(k)
        if key not in _VALID_SCORE_KEYS:
            continue  # LLM 返回额外/非数字键 → 丢弃该条目，不中断整次解析
        try:
            prob = float(v)
        except (ValueError, TypeError):
            return None
        if prob < 0:
            return None
        probs[key] = prob
        total += prob
    if not probs or total <= 0:
        return None
    if total < 0.9 or total > 1.1:
        probs = {k: v / total for k, v in probs.items()}
    return probs


def expected_score(probs: dict[str, float]) -> float:
    """从概率分布计算期望得分并映射到 0~1（薄原语）。

    期望 = Σ(p_i * i) / 5, 其中 i ∈ {0,1,2,3,4,5}
    """
    expected = sum(float(k) * p for k, p in probs.items())
    return max(0.0, min(1.0, expected / 5.0))


def try_build_result(content: str, abstract: str) -> tuple[LLMResult | None, str]:
    """解析并校验 LLM 响应，组合薄原语构建 LLMResult。

    Returns:
        (LLMResult, "") 校验通过；或 (None, 错误说明) 校验失败——
        错误说明会反馈给 LLM 以便下次重试时修正。
    """
    data = extract_json(content)
    if not data:
        return None, "响应无法解析为合法 JSON"

    probs = normalize_distribution(data.get("score_distribution"))
    if probs is None:
        return None, "score_distribution 无效（缺失/空/负数/非数字）"

    remark_raw = data.get("remark", "browse")
    remark = str(remark_raw).strip().lower()
    if remark not in _VALID_REMARKS:
        return (
            None,
            f"remark 字段必须是 important/useful/browse/skip 之一（英文），收到: {remark_raw!r}",
        )

    # 键存在但值为 null 时 .get() 返回 None（而非默认值），显式兜底避免写入 None。
    summary = data.get("summary") or abstract[:_SUMMARY_FALLBACK_LIMIT] + "..."
    reason = data.get("reason") or ""
    return (
        LLMResult(
            summary=summary,
            remark=remark,
            reason=reason,
            score=expected_score(probs),
            score_distribution=probs,
        ),
        "",
    )
