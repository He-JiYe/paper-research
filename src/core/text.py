"""低层文本原语：分词、标题缩写。

core 包无外部依赖，避免 ``utils ↔ network`` 循环 import；
被 scorer / network / zotero / serve 多处复用。
"""

import json
import re

# token 分词：字母数字词，支持 ``-``/``_`` 连接（全项目唯一定义处）
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """分词：提取字母数字词并转小写。"""
    return _TOKEN_RE.findall((text or "").lower())


_STOP_WORDS = {
    "a",
    "an",
    "the",
    "for",
    "of",
    "in",
    "on",
    "at",
    "to",
    "by",
    "and",
    "or",
    "is",
    "with",
}


def extract_title_abbrev(title: str) -> str:
    """从标题提取缩写（截断 30 字符）。

    规则（与既有行为一致）：
    - 非停用词 ≥3 个 → 首词[:4] + 末词[:6]；
    - 否则前 3 个非停用词各[:4]；
    - 空标题返回空字符串（由调用方兜底，如 "Untitled"/"Paper"）。
    """
    words = re.sub(r"[^\w\s-]", "", title or "").split()
    content_words = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 1]
    if len(content_words) >= 3:
        result = content_words[0][:4] + content_words[-1][:6]
    elif content_words:
        result = "".join(w[:4] for w in content_words[:3])
    else:
        result = words[0][:8] if words else ""
    return result[:30]


def relevance_score(paper: dict, keyword: str) -> float:
    """计算论文与关键词的简单相关性得分（用于抓取批内重排 / fallback 兜底评分）。

    对 title 和 abstract 分别与 keyword 做 token 重叠匹配，
    标题匹配权重为摘要的 2 倍。纯文本处理，无外部依赖。
    """
    if not keyword:
        return 0.0

    kw_tokens = set(tokenize(keyword))
    if not kw_tokens:
        return 0.0

    title_tokens = set(tokenize(paper.get("title", "")))
    abstract_tokens = set(tokenize(paper.get("abstract", "")))

    overlap_title = len(kw_tokens & title_tokens)
    overlap_abstract = len(kw_tokens & abstract_tokens)
    n = len(kw_tokens)

    # 标题匹配权重 ×2，摘要匹配权重 ×1
    score = (overlap_title * 2 + overlap_abstract) / (n * 3)
    return round(min(1.0, score), 4)

def suggest_short_title(paper: dict) -> str:
    """生成短标题（单一来源：Web 回填与 Zotero 兜底共用）。

    格式：
    - 抓取数据 ``raw_data.comment`` 非空 → ``{comment}-{缩写}``，comment 空格转连字符
      并清理非字母数字字符（如 ``ICLR 2025`` → ``ICLR-2025``）；
    - 无 comment → ``{source}-{year}-{缩写}``，year 取 ``updated`` 前 4 位
      （无效用 ``????``），source 取 ``paper.source``，均为空时用 ``Unknown``。

    例：``ICLR-2025-SimpLearni``（comment="ICLR 2025"）；``arxiv-2024-SimpLearni``。
    """
    abbrev = extract_title_abbrev(paper.get("title", "")) or "Paper"
    year = (paper.get("updated", "") or "")[:4]
    if not year or not year.isdigit():
        year = "????"
    source = paper.get("source", "") or "Unknown"
    return f"{source}-{year}-{abbrev}"
