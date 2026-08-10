"""核心数据模型：论文元数据标准格式。

数据源（network 层）与数据库（db 层）共享的论文元数据契约。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class KeywordItem:
    """关键词条目：所有数据源共享的固定形式（keyword/categories/active）。

    各数据源不再定义各自的 keyworditem；keyword 的查询构造由各源在
    ``Options.to_list()`` 中按本条目 + 源级参数完成（config 与 network 的契约）。
    """

    keyword: str
    categories: list[str] = field(default_factory=list)
    active: bool = True


@dataclass
class Record:
    """论文元数据标准格式：数据源与 sqlite 共享。"""

    # ── 公共核心字段 ────────────────────────────────────────
    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    published: str = ""  # 发布（YYYY-MM-DD 或 ISO）
    updated: str = ""  # 最近更新
    categories: list[str] = field(default_factory=list)
    url: str = ""  # 详情页
    pdf_url: str = ""  # PDF 下载地址（可为空）
    keyword_match: str = ""  # 命中关键词

    # ── 数据源字段 ─────────────────────────────────────────
    source: str = ""  # 数据源名称
    source_id: str = ""  # 数据源内唯一 ID

    # ── 每源附加字段（JSON 化入 raw_data 列）────────────────
    raw_data: dict[str, Any] = field(default_factory=dict)


def record_to_row(record: Record) -> dict:
    """Record → DB 行 dict。

    ``authors``/``categories`` 转逗号串，``raw_data`` JSON 化。
    （scorer、core.zotero.parse_authors 均依赖逗号串格式）
    """
    row = asdict(record)
    row["authors"] = ", ".join(record.authors)
    row["categories"] = ", ".join(record.categories)
    row["raw_data"] = json.dumps(record.raw_data, ensure_ascii=False)
    return row
