"""Zotero 端口：分类列表、批量导入、导入状态、完成信号 SSE。"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.serve.runtime import runtime_of
from src.zotero.utils import MAX_BATCH_ITEMS

router = APIRouter()


def _require_zotero(request: Request):
    rt = runtime_of(request)
    if not rt.zotero_import:
        raise HTTPException(status_code=500, detail="Zotero not connected")
    return rt.zotero_import


@router.get("/api/zotero/collections")
async def api_zotero_collections(request: Request):
    """获取 Zotero 全部分类，供前端导入下拉框使用。"""
    rt = runtime_of(request)
    if not rt.zotero_client:
        raise HTTPException(status_code=500, detail="Zotero not connected")
    collections = await asyncio.to_thread(rt.zotero_client.list_collections)
    return JSONResponse(content={"collections": collections})


@router.post("/api/zotero/import-batch")
async def api_zotero_import_batch(request: Request, data: dict):
    """批量导入论文到 Zotero（JSON body: {items:[{source,source_id,short_title,collection_key}]}）。"""
    manager = _require_zotero(request)
    items = data.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="No items provided")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items 必须是列表")
    if any(not isinstance(it, dict) for it in items):
        raise HTTPException(status_code=400, detail="items 元素必须是对象")
    if len(items) > MAX_BATCH_ITEMS:
        raise HTTPException(status_code=400, detail=f"一次最多导入 {MAX_BATCH_ITEMS} 条")

    job = manager.submit(items)
    if job is None:
        raise HTTPException(status_code=409, detail="已有导入任务进行中，请等待完成")
    runtime_of(request).log("info", f"Zotero 批量导入已提交: {len(items)} 条 job={job['id']}")
    return JSONResponse(
        content={"status": "submitted", "job_id": job["id"], "busy": True, "items": len(items)}
    )


@router.get("/api/zotero/import/status")
async def api_zotero_import_status(request: Request):
    """查看导入任务状态与日志（前端订阅前先查一次 / SSE 断开时兜底）。"""
    manager = _require_zotero(request)
    return JSONResponse(content=manager.status())


@router.get("/api/import/events")
async def api_import_events(request: Request):
    """SSE：导入完成信号（import-done），前端收到即刷新。

    竞态兜底：连接建立时立即下发当前任务状态（任务可能在订阅前已完成）；
    期间每 15s 发一条注释心跳行保活（防代理/网关超时断流）；
    收到终态事件或超过 2h 后结束。只转发导入域事件（import-done/error），
    与抓取事件流（/api/fetch/events）隔离，避免 fetch-done 串扰导入进度。
    """
    from src.serve.runtime import sse_event_stream

    rt = runtime_of(request)

    def _initial():
        """订阅前任务可能已完成/失败，先推一次当前状态（前端据此立即收尾）。"""
        manager = rt.zotero_import
        if manager is None:
            return None, False
        job = manager.status().get("job")
        if not job:
            return None, False
        return (
            {"type": "status", "status": job["status"], "job_id": job["id"]},
            job["status"] in ("done", "error"),
        )

    return StreamingResponse(
        sse_event_stream(
            rt,
            forward={"import-done", "error"},
            terminal={"import-done", "error"},
            init=_initial,
        ),
        media_type="text/event-stream",
    )
