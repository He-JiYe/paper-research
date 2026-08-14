"""Zotero API 客户端：与 Zotero 的交互层。

- ``ZoteroClient``：读/写原语 + 核心组合 ``create_items``（本机 connector 只读，
  PDF 附件功能已随前端取消移除）。

线程安全：pyzotero 实例内部维护可变状态，非线程安全；每个同步原语用可重入锁整体串行化。
"""

import asyncio
import logging
import threading

from pyzotero import Zotero as PyzoteroClient

from src.zotero.convert import collection_paths, paper_to_zotero_item, split_collection_path
from src.zotero.utils import (
    MAX_BATCH_ITEMS,
    extract_key,
    extract_keys,
    is_collection_key,
    normalize_collection_ref,
)

logger = logging.getLogger(__name__)


class ZoteroClient:
    """Zotero 核心交互：连接、读/写原语、批量创建条目。"""

    def __init__(
        self,
        api_key: str,
        library_id: int | str,
        library_type: str = "user",
        upload_timeout: int | float = 120,
    ):
        self._client = PyzoteroClient(
            library_id, library_type, api_key, upload_timeout=upload_timeout
        )
        self._api_key = api_key
        self._lock = threading.RLock()

    # ── 读原语 ─────────────────────────────────────────────

    def list_collections(self) -> list[dict]:
        """获取全部分类（直接查询 API）。

        Returns:
            [{"name", "key", "path", "depth", "parentCollection"}] 按 path 排序。
        """
        with self._lock:
            raw = self._client.everything(self._client.collections())
            return collection_paths(raw)

    # ── 写原语 ─────────────────────────────────────────────

    def create_collection(self, name: str, parent_key: str = "") -> str:
        """创建分类，返回 collection key。"""
        with self._lock:
            payload = {"name": name}
            if parent_key:
                payload["parentCollection"] = parent_key
            response = self._client.create_collections([payload])
            key = extract_key(response, what="collection")
            logger.info("创建 Zotero collection: %s (%s)", name, key)
            return key

    # ── 核心组合：批量创建论文条目 ─────────────────────────

    async def create_items(
        self,
        papers: list[dict],
        *,
        short_titles: list[str] | None = None,
        collection_keys: list[str | None] | None = None,
    ) -> list[str | None]:
        """批量创建论文条目（一次 pyzotero 调用，逐篇可带独立 collection）。

        自动完成：逐篇分类 find-or-create → 构造 items → 一次批量创建。

        Args:
            papers: 论文 dict 列表（含 source/source_id/title/...）
            short_titles: 与 papers 等长的短标题列表（None 时用 paper.short_title）
            collection_keys: 与 papers 等长的归档分类（key 或 "A / B / C" 路径，
                不存在自动创建）；None 表示不归档

        Returns:
            按输入顺序的 item key 列表；Zotero 拒绝的项为 None（部分失败，成功项不丢）。
        """
        if not papers:
            return []
        if len(papers) > MAX_BATCH_ITEMS:
            raise ValueError(f"一次最多创建 {MAX_BATCH_ITEMS} 个 item")

        sts = short_titles or [""] * len(papers)
        cks = collection_keys or [None] * len(papers)
        resolved = [await asyncio.to_thread(self.ensure_collection, ck) for ck in cks]
        # zip(strict=True)：三个参数须等长，长度不一致立即抛错（替代 resolved[i] 越界）。
        payload = [
            paper_to_zotero_item(p, short_title=(st or ""), collection_key=rk)
            for p, st, rk in zip(papers, sts, resolved, strict=True)
        ]

        response = await asyncio.to_thread(self._create_items_batch, payload)
        keys = extract_keys(response, len(payload))
        logger.info("创建 Zotero items: %d 个 (%s)", len(keys), ",".join(keys))
        return keys

    def _create_items_batch(self, payload: list[dict]) -> object:
        """调用 pyzotero 批量创建（带锁；失败抛错并带详情）。"""
        with self._lock:
            try:
                return self._client.create_items(payload)
            except Exception as e:
                logger.error("创建 Zotero items 失败: %s", e)
                raise

    def ensure_collection(
        self, ref: str | None, *, path_to_key: dict[str, str] | None = None
    ) -> str | None:
        """按 key 或路径 "A / B / C" find-or-create 分类，返回 collection key。

        8 位 base62（Zotero 真实 key 形式，含字母）视为 key 直接返回；否则沿路径
        逐级查找，缺哪级用 ``create_collection`` 补齐。路径解析复用
        ``convert.split_collection_path``（与 collection_paths 单一来源）。

        Args:
            ref: collection key 或 "A / B / C" 路径。
            path_to_key: 可选的预拉 path(lowercase)→key 映射；传入则避免再次全量
                ``list_collections``（批量导入时由 manager 预拉一次复用）。

        Note:
            恰好 8 位 base62 的单个路径组件（如名为 "Research" 的收藏夹）会被当作
            key 直接返回而非 find-or-create——符合 Zotero key 语义，属接受的取舍。
        """
        ref = normalize_collection_ref(ref)
        if ref is None:
            return None
        if is_collection_key(ref):
            return ref
        if path_to_key is None:
            path_to_key = {c["path"].lower(): c["key"] for c in self.list_collections()}
        parent_key = ""
        current = ""
        for part in split_collection_path(ref):
            current = f"{current} / {part}" if current else part
            key = path_to_key.get(current.lower())
            if not key:
                key = self.create_collection(part, parent_key)
                path_to_key[current.lower()] = key
            parent_key = key
        return parent_key
