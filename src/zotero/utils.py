"""Zotero 辅助函数：标签前缀、collection 引用解析、响应 key 提取。

client 只负责与 Zotero 交互；本模块提供纯函数辅助（无 I/O、无 Zotero 连接）。

短标题生成收敛到 ``core.text.suggest_short_title``（单一来源）。
"""

from __future__ import annotations

import re

# 单次批量创建/导入条数上限（Zotero API 批量创建限制，client 与接口层共用）
MAX_BATCH_ITEMS = 50

# ─── 标签前缀 ───────────────────────────────────────────────

TAG_SID = "sid:{source}:{id}"  # 通用 source_id 标签


# ─── collection 引用解析 ────────────────────────────────────


def is_collection_key(ref: str) -> bool:
    """是否为 8 位 base62 的 Zotero collection key（真实 key 形式，含字母）。"""
    return bool(re.fullmatch(r"[0-9A-Za-z]{8}", ref))


def normalize_collection_ref(ref: str | None) -> str | None:
    """清洗分类引用：None/空白 → None，其余 strip 后返回。"""
    if not ref or not str(ref).strip():
        return None
    return str(ref).strip()


# ─── 响应 key 提取 ──────────────────────────────────────────


def extract_key(response: object, what: str = "item") -> str:
    """从 create_collections/create_items 响应提取单个 key。"""
    keys = extract_keys(response, 1, what)
    return keys[0] if keys else ""


def extract_keys(response: object, n: int, what: str = "item") -> list[str | None]:
    """从 create_items/create_collections 响应提取 keys（与输入对齐，失败项为 None）。

    响应的 successful 键是提交序号，值中含 "key"。批量部分失败时成功项仍返回 key
    （供调用方标记已创建项，避免 Zotero 孤儿条目）；仅当全部失败才抛错。
    """
    if isinstance(response, dict) and "successful" in response:
        keys: list[str | None] = []
        for idx in range(n):
            v = response["successful"].get(str(idx))
            if isinstance(v, dict) and v.get("key"):
                keys.append(v["key"])
            else:
                keys.append(None)
        if all(k is None for k in keys):
            failed = response.get("failed", {})
            raise RuntimeError(f"Zotero 创建{what}被拒绝: {failed or response}")
        return keys
    if isinstance(response, list):
        keys = [r.get("key", "") for r in response if isinstance(r, dict)]
        if len(keys) == n and all(keys):
            return keys
    raise RuntimeError(f"无法解析创建{what}响应: {response!r}")
