"""评分结果数据模型：ScoreSource / LLMResult（core 层，无依赖）。"""

from dataclasses import dataclass
from enum import StrEnum


class ScoreSource(StrEnum):
    """评分来源，用于区分 LLM judge 与各类 fallback 原因。"""

    LLM = "llm"  # LLM as judge 正常打分
    FALLBACK_NO_KEY = "no_api_key"  # 未提供 API Key
    FALLBACK_CONNECTION = "connection_failed"  # 无法连接 LLM
    FALLBACK_INVALID = "invalid_output"  # LLM 输出无效且重试耗尽


# 合法 LLM 评级词表（parse 校验用单一来源）。
# fallback 产出的 remark 取值是其子集（useful/browse/skip）；renderer 的标签/颜色
# 另从 app/app-meta.json 读取（与前端同源），三者都与本词表保持一致。
REMARKS: tuple[str, ...] = ("important", "useful", "browse", "skip")


@dataclass
class LLMResult:
    """LLM 输出结果"""

    summary: str
    remark: str  # important / useful / browse / skip
    reason: str
    score: float
    score_distribution: dict[str, float] | None = None  # 概率分布 {0: p0, 1: p1, ..., 5: p5}
    source: ScoreSource = ScoreSource.LLM  # 评分来源（LLM judge 或 fallback 原因）
