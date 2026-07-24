"""Zotero API 客户端：基于 pyzotero 封装

提供与 Zotero 交互的统一接口：
- 分类管理（查找/创建/路径化列表）
- 论文条目创建（含 Inbox/Keywords 归档、AI Summary 笔记）
- 短标题更新（写原生 shortTitle 字段）
- PDF 附件上传（手工模板 + upload_attachments）
- 数据查询（按 arxiv_id、已有 id 集合、统计）
"""

import logging
from pathlib import Path
from typing import Any

from pyzotero import Zotero as PyzoteroClient

from src.zotero.models import (
    COLLECTION_IGNORED,
    COLLECTION_INBOX,
    COLLECTION_KEYWORDS,
    COLLECTION_LURK,
    COLLECTION_ROOT,
    MARK_IGNORE,
    MARK_LURK,
    REMARK_BROWSE,
    REMARK_IMPORTANT,
    REMARK_SKIP,
    REMARK_USEFUL,
    STATUS_REVIEWED,
    STATUS_SUMMARIZED,
    TAG_AID,
    decode_extra,
    encode_extra,
    paper_to_zotero_item,
)

logger = logging.getLogger(__name__)


class ZoteroClient:
    """Zotero API 客户端。

    基于 pyzotero 库，封装项目特定的 Zotero 操作。
    所有方法为同步调用（pyzotero 为同步库），
    异步上下文中请使用 asyncio.to_thread() 包装。
    """

    def __init__(self, api_key: str, library_id: int | str, library_type: str = "user"):
        self._client = PyzoteroClient(library_id, library_type, api_key)

    # ─── 分类管理 ──────────────────────────────────────────

    def get_or_create_collection(self, path: str) -> str:
        """获取或创建分类路径（递归创建中间层级）。

        Args:
            path: 分类路径，如 "Paper Research/Inbox"

        Returns:
            分类的 key（Zotero collection key）
        """
        parts = path.strip("/").split("/")
        parent_key: str | None = None

        for i, part in enumerate(parts):
            current_path = "/".join(parts[: i + 1])
            existing = self._find_collection_by_name(part, parent_key)
            if existing:
                parent_key = existing["data"]["key"]
            else:
                # pyzotero v1.13+ 的 create_collection 接收 list[dict]
                payload = [{"name": part}]
                if parent_key:
                    payload[0]["parentCollection"] = parent_key
                response = self._client.create_collection(payload)
                # 从响应中提取 key
                # 注意：successful 的键是提交序号（"0"、"1"...），真实 collection key
                # 在值的 "key" 字段里——取错会拿到 "0" 并在后续请求中被 Zotero 拒绝
                if isinstance(response, dict) and "successful" in response:
                    for v in response["successful"].values():
                        if isinstance(v, dict) and v.get("key"):
                            parent_key = v["key"]
                            break
                elif isinstance(response, list) and response:
                    parent_key = response[0].get("key", "")
                else:
                    parent_key = getattr(response, "key", str(response) if response else "")
                logger.info("创建 Zotero 分类: %s", current_path)

        return parent_key or ""

    def _find_collection_by_name(self, name: str, parent_key: str | None = None) -> dict | None:
        """按名称和父级查找分类。"""
        collections = self._client.collections()
        for col in collections:
            data = col.get("data", {})
            if data.get("name") == name and data.get("parentCollection", False) == (
                parent_key or False
            ):
                return col
            # pyzotero 中 parentCollection 可能是字符串或布尔值
            if data.get("name") == name:
                col_parent = data.get("parentCollection") or ""
                if parent_key and col_parent == parent_key:
                    return col
                if not parent_key and not col_parent:
                    return col
        return None

    def get_all_collections(self) -> list[dict]:
        """获取 Zotero 所有分类（含完整路径信息）。

        Returns:
            [{"name": 叶子名, "key": ..., "path": "A / B / 叶子名", "depth": 层级深度}]
            按路径排序，便于前端下拉框展示层级关系。
        """
        try:
            raw = self._client.collections()
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
                        "key": data.get("key", ""),
                        "path": path,
                        "depth": depth,
                    }
                )
            return sorted(result, key=lambda c: c["path"].lower())
        except Exception as e:
            logger.warning("获取 Zotero 分类失败: %s", e)
            return []

    def init_collections(self) -> dict[str, str]:
        """初始化项目分类层级。幂等操作。

        Returns:
            dict: 各分类的 key 映射
        """
        root = self.get_or_create_collection(COLLECTION_ROOT)
        inbox = self.get_or_create_collection(COLLECTION_INBOX)
        ignored = self.get_or_create_collection(COLLECTION_IGNORED)
        lurk = self.get_or_create_collection(COLLECTION_LURK)
        keywords = self.get_or_create_collection(COLLECTION_KEYWORDS)

        keys = {
            "root": root,
            "inbox": inbox,
            "ignored": ignored,
            "lurk": lurk,
            "keywords": keywords,
        }
        logger.info("Zotero 分类初始化完成 (Paper Research/)")
        return keys

    # ─── Item CRUD ─────────────────────────────────────────

    def get_item_by_arxiv_id(self, arxiv_id: str) -> dict | None:
        """通过 arxiv_id 查询 Zotero 中的论文。

        Args:
            arxiv_id: ArXiv ID

        Returns:
            Zotero item dict，未找到则返回 None
        """
        tag = TAG_AID.format(id=arxiv_id)
        items = self._client.items(tag=tag, limit=1)
        if items:
            return items[0]
        return None

    def create_item(self, paper: dict, llm_result: Any = None) -> str:
        """创建论文条目到 Zotero。

        Args:
            paper: 论文数据
            llm_result: LLM 评分结果（可选）

        Returns:
            创建的 item key
        """
        item_data = paper_to_zotero_item(paper, llm_result)
        try:
            response = self._client.create_items([item_data])
        except Exception as e:
            logger.error("创建 Zotero item 失败: %s", e)
            raise

        # 从返回值中提取 item key
        # 注意：successful 的键是提交序号（"0"、"1"...），真实 item key
        # 在值的 "key" 字段里——取错会拿到 "0" 并在后续请求中 404/返回列表
        item_key = ""
        if isinstance(response, dict) and "successful" in response:
            for v in response["successful"].values():
                if isinstance(v, dict) and v.get("key"):
                    item_key = v["key"]
                    break
        elif isinstance(response, list):
            item_key = response[0].get("key", "") if response else ""
        else:
            item_key = getattr(response, "key", "")

        if not item_key:
            # Zotero 拒绝（如字段校验失败）时 successful 为空、failed 含原因，
            # 必须在此抛出，否则空 key 会在后续加分类时产生难以理解的级联错误
            failed = response.get("failed", {}) if isinstance(response, dict) else {}
            raise RuntimeError(f"Zotero 创建条目被拒绝: {failed or response}")

        # 添加到 Inbox 分类
        self._add_to_inbox_collection(item_key, paper)

        # 添加 LLM 摘要子笔记
        if llm_result and llm_result.summary:
            self._add_ai_summary_note(item_key, llm_result)

        return item_key

    def _add_item_to_collection(self, item_key: str, collection_key: str) -> None:
        """将 item 加入指定 collection。

        pyzotero >= 1.13 的 addto_collection 签名为 (collection_key, item_dict)，
        需要先取回完整 item 再提交。
        """
        item = self._client.item(item_key)
        self._client.addto_collection(collection_key, item)

    def _add_to_inbox_collection(self, item_key: str, paper: dict):
        """将论文添加到 Inbox 和 Keywords 分类。"""
        inbox_key = self.get_or_create_collection(COLLECTION_INBOX)
        self._add_item_to_collection(item_key, inbox_key)

        # 按关键词添加到 Keywords 子分类
        kw = paper.get("keyword_match", "")
        if kw:
            kw_path = f"{COLLECTION_KEYWORDS}/{kw}"
            kw_key = self.get_or_create_collection(kw_path)
            self._add_item_to_collection(item_key, kw_key)

    def _add_ai_summary_note(self, item_key: str, llm_result: Any):
        """添加 AI Summary 子笔记。"""
        note_content = f"<h2>AI Summary</h2>\n<p>{llm_result.summary}</p>\n"
        if llm_result.reason:
            note_content += f"<p><strong>理由</strong>: {llm_result.reason}</p>\n"
        note_content += f"<p><strong>评分</strong>: {llm_result.score:.2f} | <strong>评级</strong>: {llm_result.remark}</p>\n"
        try:
            self._client.add_notes(item_key, note_content)
        except Exception as e:
            logger.error("添加 AI Summary 笔记失败: %s", e)

    # ─── 分类移动 ──────────────────────────────────────────

    def add_to_collection(self, item_key: str, collection_key: str):
        """将 item 添加到指定分类（按 key，不创建新分类）。"""
        self._add_item_to_collection(item_key, collection_key)

    def move_to_collection(self, item_key: str, collection_path: str):
        """将 item 移动到指定分类路径（不存在则创建）。"""
        col_key = self.get_or_create_collection(collection_path)
        self._add_item_to_collection(item_key, col_key)

    # ─── 更新元数据 ────────────────────────────────────────

    def set_short_title(self, item_key: str, short_title: str):
        """设置 item 的 shortTitle 字段（Zotero 原生字段，不动完整 title）。

        历史版本曾把 title 覆盖为短标题、原标题塞进 extra——这导致
        Zotero 里标题显示为"未读-XXX-2024-Abbr"，违背用户预期。

        Args:
            item_key: Zotero item key
            short_title: 短标题（如 "未读-CVPR-2024-Diffusion"）
        """
        item = self._client.item(item_key)
        if not item:
            return
        data = item.get("data", {})
        data["shortTitle"] = short_title

        extra = decode_extra(data.get("extra", ""))
        extra["shortTitle"] = short_title
        data["extra"] = encode_extra(extra)

        self._client.update_item(data)

    # ─── PDF 附件 ──────────────────────────────────────────

    def has_pdf_attachment(self, item_key: str) -> bool:
        """检查 item 是否已有 PDF 附件（防重复上传）。"""
        try:
            for child in self._client.children(item_key) or []:
                data = child.get("data", {})
                if (
                    data.get("itemType") == "attachment"
                    and data.get("contentType") == "application/pdf"
                ):
                    return True
        except Exception as e:
            logger.warning("检查 PDF 附件失败: %s", e)
        return False

    def attach_pdf(self, item_key: str, pdf_path: str, title: str = "") -> str:
        """上传 PDF 文件为 item 的子附件。

        注意不能用 pyzotero 的 attachment_simple/attachment_both：
        它们把本地完整路径塞进 filename 字段，而 Zotero API 要求
        filename 不含目录（400: cannot contain a directory path）。
        正确姿势是手工构造模板（filename=basename）+ upload_attachments
        并传 basedir 定位本地文件。

        Args:
            item_key: 父 item key
            pdf_path: 本地 PDF 文件路径
            title: 附件显示标题（默认取文件名）

        Returns:
            附件 item key

        Raises:
            RuntimeError: 上传被 Zotero 拒绝时
        """
        path = Path(pdf_path)
        tmpl = self._client.item_template("attachment", linkmode="imported_file")
        tmpl["title"] = title or path.name
        tmpl["filename"] = path.name
        tmpl["contentType"] = "application/pdf"

        try:
            result = self._client.upload_attachments([tmpl], item_key, basedir=str(path.parent))
        except Exception as e:
            # 上传中途失败（如存储配额 413）时，attachment item 已在
            # _create_prelim 阶段创建——清理这个没有文件的空壳附件。
            # 统一包装为 RuntimeError，屏蔽 pyzotero 自有异常类型
            self._remove_empty_attachment(item_key, tmpl["title"])
            raise RuntimeError(f"Zotero 附件上传失败: {e}") from e

        # 返回 {"success": [...], "failure": [...], "unchanged": [...]}（列表）
        failure = result.get("failure") or []
        if failure:
            self._remove_empty_attachment(item_key, tmpl["title"])
            raise RuntimeError(f"Zotero 附件上传失败: {failure}")
        for item in (result.get("success") or []) + (result.get("unchanged") or []):
            if isinstance(item, dict) and item.get("key"):
                logger.info("PDF 附件已上传: %s -> %s", pdf_path, item["key"])
                return item["key"]
        return ""

    def _remove_empty_attachment(self, item_key: str, title: str) -> None:
        """删除上传失败后残留的空附件（无 md5 即文件未落库）。"""
        try:
            for child in self._client.children(item_key) or []:
                data = child.get("data", {})
                if (
                    data.get("itemType") == "attachment"
                    and data.get("title") == title
                    and not data.get("md5")
                ):
                    self._client.delete_item(child)
                    logger.info("已清理空附件: %s", data.get("key"))
        except Exception as e:
            logger.warning("清理空附件失败: %s", e)

    # ─── 统计 ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取统计信息。

        通过查询各标签对应的 items 数量来聚合统计。
        """
        all_items = self._client.items(limit=9999) or []
        tag_index: dict[str, int] = {}
        item_index: dict[str, bool] = {}

        for item in all_items:
            data = item.get("data", item)
            key = data.get("key", "")
            if key in item_index:
                continue
            item_index[key] = True

            for t in data.get("tags", []):
                tag = t["tag"] if isinstance(t, dict) else str(t)
                tag_index[tag] = tag_index.get(tag, 0) + 1

        def count_tag(prefix: str) -> int:
            return sum(v for k, v in tag_index.items() if k.startswith(prefix))

        return {
            "total": len(item_index),
            "summarized_pending": count_tag(f"status:{STATUS_SUMMARIZED}"),
            "reviewed": count_tag(f"status:{STATUS_REVIEWED}"),
            "ignored": count_tag(f"mark:{MARK_IGNORE}"),
            "lurk": count_tag(f"mark:{MARK_LURK}"),
            "important": count_tag(f"remark:{REMARK_IMPORTANT}"),
            "useful": count_tag(f"remark:{REMARK_USEFUL}"),
            "browse": count_tag(f"remark:{REMARK_BROWSE}"),
            "skip": count_tag(f"remark:{REMARK_SKIP}"),
        }

    # ─── 获取已有 arxiv_id 集合（用于 skip_ids）─────────────

    def get_existing_arxiv_ids(self) -> set[str]:
        """获取 Zotero 中所有已有论文的 arxiv_id 集合。

        通过遍历所有 items 的 tags 提取 aid: 前缀标签。
        （原实现用 source:arxiv 标签分页查询，但若 tag 被精简则查不到；
        改为查全部 items 再本地过滤，更健壮。）
        """
        ids: set[str] = set()
        start = 0
        while True:
            items = self._client.items(limit=100, start=start)
            if not items:
                break
            for item in items:
                data = item.get("data", {})
                for t in data.get("tags", []):
                    tag = t["tag"] if isinstance(t, dict) else str(t)
                    if tag.startswith("aid:"):
                        ids.add(tag.split(":", 1)[1])
            if len(items) < 100:
                break
            start += 100
        return ids
