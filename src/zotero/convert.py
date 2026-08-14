"""Zotero 数据转换：作者解析、Paper→Item、collection 路径派生（纯函数）。"""

from __future__ import annotations

import json
import re


def split_collection_path(ref: str) -> list[str]:
    """把 ``"A / B / C"`` 路径拆成逐级名称列表（去空、去空白）。

    单一来源：client.ensure_collection 沿路径 find-or-create 时复用。
    """
    return [p.strip() for p in ref.split("/") if p.strip()]


def _normalize_date(value: str) -> str:
    """把日期归一化为 ``YYYY-MM-DD``（截取 ISO 前 10 位；无效返回前 10 字符）。"""
    if not value:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return value[:10]


def _extract_raw_data_field(paper: dict, key: str) -> str:
    """从 ``raw_data``（dict 或 JSON 字符串）提取指定字段（如 doi / citation）。

    ``citation`` 由各数据源的 ``adapt()`` 写入 ``raw_data["citation"]``（源特有知识下沉到 network 层）。
    """
    raw = paper.get("raw_data", "")
    if isinstance(raw, dict):
        return raw.get(key, "") or ""
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw).get(key, "") or ""
        except (json.JSONDecodeError, TypeError):
            return ""
    return ""


def parse_authors(authors_str: str) -> list[dict]:
    """将作者字符串解析为 Zotero creators 格式。

    "Alice Zhang, Bob Li" → [{"creatorType": "author", "firstName": "Alice", "lastName": "Zhang"}, ...]

    注意：Zotero API 要求每个 creator 必须含 creatorType，否则 400 拒绝。

    支持格式:
    - "FirstName LastName, ..."
    - "Last, First, ..."（如 "Zhang, Alice"；"Last, First, Last2, First2" 亦可）
    """
    if not authors_str:
        return []

    creators = []
    for part in authors_str.split(","):
        part = part.strip()
        if not part:
            continue

        # "Last, First" 格式：纯姓名的段（无空格）且上一个 creator 仍缺 firstName
        # → 归并为其 firstName（如 "Zhang, Alice" → lastName=Zhang firstName=Alice）。
        # 对 "Alice Zhang, Bob Li" 的正常格式不生效（每段都含空格）。
        if " " not in part and creators and not creators[-1].get("firstName"):
            creators[-1]["firstName"] = part
            continue

        toks = part.rsplit(" ", 1)
        if len(toks) == 2:
            creators.append({"creatorType": "author", "firstName": toks[0], "lastName": toks[1]})
        else:
            creators.append({"creatorType": "author", "firstName": "", "lastName": toks[0]})

    return creators


def paper_to_zotero_item(
    paper: dict,
    *,
    short_title: str = "",
    collection_key: str = "",
) -> dict:
    """将论文数据转换为 Zotero item JSON（参数化：直接写入 shortTitle/collections）。

    字段映射：
    - ``repository``（仓库）/ ``libraryCatalog``（文库编目）= source 名；
    - ``archiveID``（存档ID）= ``{source}:{source_id}``；``language`` = "en"；
    - ``date`` 归一化为 ``YYYY-MM-DD``；``DOI`` 取自 ``raw_data``（arxiv 源已存 doi）；
    - ``short_title`` / ``collection_key`` 参数化写入原生字段；
    - extra 只写引用分类（raw_data.citation），source/source_id/shortTitle 已由原生字段承载，
      不再冗余写入 extra。
    """
    source = paper.get("source", "")
    source_id = paper.get("source_id", "")

    short_title = (short_title or paper.get("short_title") or "").strip()
    doi = _extract_raw_data_field(paper, "doi")

    item = {
        "itemType": "preprint",
        "title": paper.get("title", ""),
        "creators": parse_authors(paper.get("authors", "")),
        "abstractNote": paper.get("abstract", ""),
        "date": _normalize_date(paper.get("published", "")),
        "url": paper.get("url", ""),
        "repository": source,  # 仓库
        "archiveID": f"{source}:{source_id}",  # 存档ID
        "libraryCatalog": source,  # 文库编目
        "language": "en",  # 语言
        "shortTitle": short_title,
    }
    if collection_key:
        item["collections"] = [collection_key]
    if doi:
        item["DOI"] = doi

    citation = _extract_raw_data_field(paper, "citation")
    if citation:
        item["extra"] = citation  # extra 只写引用格式分类（如 arXiv:xxx [cs.RO]）

    return item


def collection_paths(raw: list[dict]) -> list[dict]:
    """从 pyzotero collections 原始响应构建扁平列表（含 path/depth，按 path 排序）。

    Args:
        raw: ``everything(collections())`` 的原始 item dict 列表

    Returns:
        [{"name", "key", "path", "depth", "parentCollection"}] 按 path 排序。
    """
    by_key: dict[str, dict] = {}
    for col in raw:
        data = col.get("data", {})
        if data.get("key"):
            by_key[data["key"]] = data

    def _path_of(data: dict) -> tuple[str, int]:
        """沿 parentCollection 向上拼完整路径（防环）。"""
        parts = [data.get("name", "")]
        depth = 0
        parent = data.get("parentCollection") or ""
        seen = {data.get("key")}
        while parent and parent in by_key and parent not in seen:
            seen.add(parent)
            pdata = by_key[parent]
            parts.insert(0, pdata.get("name", ""))
            depth += 1
            parent = pdata.get("parentCollection") or ""
        return " / ".join(parts), depth

    result = []
    for data in by_key.values():
        path, depth = _path_of(data)
        result.append(
            {
                "name": data.get("name", ""),
                "key": data["key"],
                "path": path,
                "depth": depth,
                "parentCollection": data.get("parentCollection") or "",
            }
        )
    return sorted(result, key=lambda c: c["path"].lower())
