"""Zotero 数据模型：标签常量、Extra 字段编解码、数据转换"""

import re
from datetime import UTC, datetime
from typing import Any

# ─── 标签前缀 ───────────────────────────────────────────────

TAG_AID = "aid:{id}"                 # Arxiv ID 标签

# ─── 状态常量 ───────────────────────────────────────────────

STATUS_NEW = "new"
STATUS_SUMMARIZED = "summarized"
STATUS_REVIEWED = "reviewed"

# ─── 标记常量 ───────────────────────────────────────────────

MARK_IGNORE = "ignore"
MARK_LURK = "lurk"

# ─── 评级常量 ───────────────────────────────────────────────

REMARK_IMPORTANT = "important"
REMARK_USEFUL = "useful"
REMARK_BROWSE = "browse"
REMARK_SKIP = "skip"

# ─── 分类路径常量 ───────────────────────────────────────────

COLLECTION_ROOT = "Paper Research"
COLLECTION_INBOX = "Paper Research/Inbox"
COLLECTION_IGNORED = "Paper Research/Ignored"
COLLECTION_LURK = "Paper Research/Lurk"
COLLECTION_KEYWORDS = "Paper Research/Keywords"


# ─── Extra 字段编解码 ───────────────────────────────────────


def encode_extra(fields: dict[str, str]) -> str:
    """将字典编码为 Zotero extra 字段格式（换行分隔 key: value）。

    Args:
        fields: 键值对字典

    Returns:
        编码后的字符串，如 "version: 1\\nllmScore: 0.85"
    """
    return "\n".join(f"{k}: {v}" for k, v in fields.items() if v)


def decode_extra(extra: str) -> dict[str, str]:
    """从 Zotero extra 字段解析为字典。

    Args:
        extra: extra 字段原始字符串

    Returns:
        解析后的字典
    """
    result = {}
    if not extra:
        return result
    for line in extra.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


# ─── 短标题生成 ─────────────────────────────────────────────


def generate_short_title(paper: dict, venue: str = "Unknown") -> str:
    """生成短标题：未读-会议-年份-简称

    Args:
        paper: 论文数据
        venue: 会议/期刊名称

    Returns:
        短标题，如 "未读-CVPR-2024-Diffusion"
    """
    year = (paper.get("published", "") or "")[:4]
    if not year or not year.isdigit():
        year = "????"
    title = paper.get("title", "")
    abbrev = _extract_abbreviation(title)
    return f"未读-{venue}-{year}-{abbrev}"


def _extract_abbreviation(title: str) -> str:
    """从论文标题提取简称。

    1. 如果标题包含 '-' 分隔的英文，取最后一段
    2. 取第 1-2 个单词的首字母或首词
    3. 限制长度 30 字符
    """
    if not title:
        return "Untitled"

    # 清理标题，移除特殊字符
    clean = re.sub(r"[^\w\s-]", "", title)
    words = clean.split()

    # 尝试找有代表性的缩写
    # 规则：取第一个和最后一个非停用词的首字母大写
    stop_words = {"a", "an", "the", "for", "of", "in", "on", "at", "to", "by", "and", "or", "is", "with"}
    content_words = [w for w in words if w.lower() not in stop_words and len(w) > 1]

    if len(content_words) >= 3:
        # 取前两个词的首字母 + 最后一个词
        result = content_words[0][:4] + content_words[-1][:6]
    elif content_words:
        result = "".join(w[:4] for w in content_words[:3])
    else:
        result = words[0][:8] if words else "Untitled"

    return result[:30]


# ─── 作者解析 ───────────────────────────────────────────────


def parse_authors(authors_str: str) -> list[dict]:
    """将作者字符串解析为 Zotero creators 格式。

    "Alice Zhang, Bob Li" → [{"creatorType": "author", "firstName": "Alice", "lastName": "Zhang"}, ...]

    注意：Zotero API 要求每个 creator 必须含 creatorType，否则 400 拒绝。

    支持格式:
    - "FirstName LastName, ..."
    - "Last, First, ..."
    """
    if not authors_str:
        return []

    creators = []
    for part in authors_str.split(","):
        part = part.strip()
        if not part:
            continue

        parts = part.rsplit(" ", 1)
        if len(parts) == 2:
            creators.append({"creatorType": "author", "firstName": parts[0], "lastName": parts[1]})
        else:
            creators.append({"creatorType": "author", "firstName": "", "lastName": parts[0]})

    return creators


# ─── Paper ↔ Zotero Item 转换 ──────────────────────────────


def paper_to_zotero_item(paper: dict, llm_result: Any = None) -> dict:
    """将论文数据转换为 Zotero item JSON 格式。

    字段映射对齐 Zotero 官方 arXiv translator 的习惯：
    - itemType=preprint，title 保留论文完整标题（短标题写 shortTitle 字段）
    - DOI 使用 arXiv 官方 DataCite DOI 格式 10.48550/arXiv.{id}
    - repository/archiveID/libraryCatalog 标记来源为 arXiv

    Args:
        paper: 论文数据（来自 Arxiv API 的 dict）
        llm_result: LLMResult 对象，含 summary/remark/reason/score

    Returns:
        Zotero API 兼容的 item dict
    """
    arxiv_id = paper["arxiv_id"]
    extra_fields: dict[str, str] = {
        "version": str(paper.get("version", 1)),
        "keywordMatch": paper.get("keyword_match", ""),
        "fetchDate": paper.get("fetch_date", ""),
        "lastFetchDate": paper.get("fetch_date", ""),
        "primaryCategory": paper.get("primary_category", ""),
        "arxivUpdated": paper.get("arxiv_updated", ""),
        "isUpdated": "0",
    }

    # 标签列表
    tags = [
        {"tag": f"aid:{paper['arxiv_id']}", "type": 1},
        {"tag": "source:arxiv", "type": 1},
    ]

    # 分类标签
    categories = paper.get("categories", "")
    if categories:
        for cat in categories.split(","):
            cat = cat.strip()
            if cat:
                tags.append({"tag": cat, "type": 1})

    # 关键词标签
    kw = paper.get("keyword_match", "")
    if kw:
        tags.append({"tag": f"kw:{kw}", "type": 1})

    # LLM 结果
    if llm_result:
        extra_fields["llmScore"] = str(llm_result.score)
        extra_fields["llmRemark"] = llm_result.remark
        extra_fields["llmReason"] = llm_result.reason[:200]
        tags.append({"tag": f"remark:{llm_result.remark}", "type": 1})
        tags.append({"tag": f"status:{STATUS_SUMMARIZED}", "type": 1})
    else:
        tags.append({"tag": f"status:{STATUS_NEW}", "type": 1})

    # 作者
    creators = parse_authors(paper.get("authors", ""))

    return {
        "itemType": "preprint",
        "title": paper.get("title", ""),
        "creators": creators,
        "abstractNote": paper.get("abstract", ""),
        "date": paper.get("published", ""),
        # arXiv 官方 DataCite DOI（2022 年起为所有论文分配）
        "DOI": f"10.48550/arXiv.{arxiv_id}",
        "repository": "arXiv",
        "archiveID": f"arXiv:{arxiv_id}",
        "libraryCatalog": "arXiv.org",
        "language": "en",
        "accessDate": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": paper.get("url", f"https://arxiv.org/abs/{arxiv_id}"),
        "extra": encode_extra(extra_fields),
        "tags": tags,
    }
