"""Zotero 导入任务管理器：后台单飞 + 日志（批量统一入口）。

- 一次只允许一个导入任务（busy 期间新提交返回 None，接口层回 409）；
- 前端选定 db 内 record：读取论文 → 一次 ``create_items`` 批量创建（逐篇独立 collection）；
  导入不做去重，重复导入同一 item 会创建新条目；
- 每篇创建后立即 ``update_mark('imported')``；
- 每步写入 job.log + items_status；任务完成经 on_done → SSE 推送完成信号。

不再提供 PDF 附件功能：本机 Zotero connector（localhost:23119/api）只支持读取、
不支持写入，无法自动添加本地附件（也不走云端上传）。
"""

import asyncio
import logging
from datetime import datetime

from src.core.text import suggest_short_title

logger = logging.getLogger(__name__)


class ZoteroImportManager:
    """Zotero 导入任务管理器（单飞 + 批量，按 collection 分组批量创建）。"""

    def __init__(self, zotero, on_done=None):
        self._zotero = zotero
        self._job: dict | None = None
        # 任务完成/失败回调（serve 注入，用于 SSE 推送完成信号，避免前端高频轮询）
        self._on_done = on_done

    @property
    def busy(self) -> bool:
        return bool(self._job and self._job.get("status") == "running")

    def submit(self, items: list[dict]) -> dict | None:
        """提交批量导入任务（items 每项含 source/source_id/short_title/collection_key）。

        busy 时返回 None。
        """
        if self.busy:
            return None
        job = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S"),
            "items": len(items),
            "status": "running",
            "step": "排队中",
            "log": [],
            "items_status": {},  # {"source:source_id": {"item": imported|failed}}（合成 key 防跨源碰撞）
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "result": None,
            "error": None,
        }
        self._job = job
        # 保留任务引用，避免被 GC 静默回收
        self._task = asyncio.create_task(self._run(job, items))
        return job

    def _log(self, job: dict, level: str, msg: str) -> None:
        job["log"].append({"t": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": msg})
        getattr(logger, "warning" if level == "warn" else level, logger.info)(
            f"[zotero-import] {msg}"
        )

    async def _run(self, job: dict, items: list[dict]) -> None:
        z = self._zotero
        try:
            from src.db import PaperDB

            db = PaperDB()
            total = len(items)

            # 预拉一次全部分类 path→key（批量内 find-or-create 复用，避免每篇重复全量拉取）
            collection_map: dict[str, str] = {}
            try:
                for c in await asyncio.to_thread(z.list_collections):
                    collection_map[c["path"].lower()] = c["key"]
            except Exception:
                logger.warning("预拉 Zotero 分类失败，改用逐篇 find-or-create")

            # ── 1. 读 db records + 组装批量创建参数（不做去重）──
            papers, short_titles, collection_keys, metas = await self._collect_pending(
                job, items, db
            )
            results: list[dict] = []

            # ── 2. 一次批量 create_items（逐篇独立 collection，分类已解析为 key）──
            job["step"] = f"批量创建 {len(papers)} 条"
            # 预解析路径→key：create_items 内部还会对每个 ck 再跑一次 ensure_collection，
            # 但已是 key 会 is_collection_key 短路（无 I/O），故这里预解析负责实际的
            # find-or-create I/O（共享 collection_map，避免每篇重复全量拉取），
            # create_items 那遍是廉价的空转兜底，两处并存属有意为之。
            resolved_colls = [
                await asyncio.to_thread(z.ensure_collection, ck, path_to_key=collection_map)
                for ck in collection_keys
            ]
            keys = await z.create_items(
                papers,
                short_titles=short_titles,
                collection_keys=resolved_colls,
            )
            failed = 0
            for meta, key, final_title in zip(metas, keys, short_titles, strict=True):
                source, source_id = meta["source"], meta["source_id"]
                if not key:
                    # 部分失败：Zotero 拒绝该条，不回写 imported（避免伪造已导入），
                    # 已成功项仍正常标记，避免 Zotero 孤儿条目。
                    failed += 1
                    self._log(job, "error", f"导入被 Zotero 拒绝: {source}:{source_id}")
                    job["items_status"][f"{source}:{source_id}"] = {"item": "failed"}
                    continue
                db.update_mark(
                    source, source_id, "imported", short_title=final_title, zotero_key=key
                )
                self._log(job, "info", f"导入完成: {source}:{source_id} -> {key}")
                job["items_status"][f"{source}:{source_id}"] = {"item": "imported"}
                results.append(
                    {
                        "source": source,
                        "source_id": source_id,
                        "zotero_key": key,
                        "created": True,
                    }
                )
            # 部分失败也把已成功项写进 result（前端可见明细，而不是只有 error）
            job["result"] = {"items": results, "total": total, "failed": failed}
            if failed:
                raise RuntimeError(f"{failed} 篇导入被 Zotero 拒绝，已成功导入 {len(results)} 篇")

            job["status"] = "done"
            # item 全部导入并标记，发完成信号（前端刷新即显示"已处理"）
            self._emit("import-done", job)
        except Exception as e:
            logger.exception("Zotero 导入失败")
            job["status"] = "error"
            job["error"] = str(e)
            self._log(job, "error", f"导入失败: {e}")
            self._emit("error", job)  # 事件类型与终态一致，SSE 循环的 error 分支才收得到
        finally:
            job["finished_at"] = datetime.now().isoformat()
            if job["status"] == "running":
                job["status"] = "error"
                self._emit("import-done", job)

    async def _collect_pending(
        self, job: dict, items: list[dict], db
    ) -> tuple[list, list, list, list]:
        """读 db records + 组装批量创建参数（导入不去重：重复导入会创建新条目）。

        返回 ``(papers, short_titles, collection_keys, metas)`` 四项，用于批量创建。
        """
        papers, short_titles, collection_keys, metas = [], [], [], []
        for idx, item in enumerate(items):
            job["step"] = f"检查 {idx + 1}/{len(items)}"
            source = item.get("source", "")
            source_id = item.get("source_id", "")
            collection_key = item.get("collection_key", "") or None
            short_title = item.get("short_title", "")

            paper = db.get_paper(source, source_id)
            if not paper:
                raise ValueError(f"论文不存在于数据库: {source}:{source_id}")

            papers.append(paper)
            short_titles.append(short_title or suggest_short_title(paper))
            collection_keys.append(collection_key)
            metas.append({"source": source, "source_id": source_id, "paper": paper})
        return papers, short_titles, collection_keys, metas

    def _emit(self, type_: str, job: dict) -> None:
        """推送终态/完成事件（on_done → SSE）。"""
        if self._on_done:
            self._on_done(
                {
                    "type": type_,
                    "job_id": job["id"],
                    "status": job["status"],
                    "result": job.get("result"),
                    "error": job.get("error"),
                }
            )

    def status(self) -> dict:
        if not self._job:
            return {"busy": False, "job": None}
        return {"busy": self._job["status"] == "running", "job": self._job}
