"""论文审阅相关端口：/api/papers、/api/static、/api/fetch/status、/api/fetch/events、/mark。"""

import datetime

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.db import PaperDB
from src.db.enums import UserMark
from src.serve.runtime import runtime_of

router = APIRouter()

# /api/papers 合法 range 取值（与 docs/api.md 及前端下拉一致）
_VALID_RANGES = {"all", "today", "7d", "30d"}


@router.get("/api/papers")
async def api_papers(request: Request, range_: str = Query("all", alias="range")):
    """分组论文 JSON：unmarked/marked/lurk + stats + update_time（按日期范围过滤）。"""
    if range_ not in _VALID_RANGES:
        raise HTTPException(status_code=400, detail=f"Unknown range: {range_}")
    from src.config.settings import today_str
    from src.serve.payloads import build_papers_payload

    rt = runtime_of(request)
    if not rt.settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    # "今日"参考日期按系统本地时间计算，与邮件今日过滤同一口径
    today = datetime.date.fromisoformat(today_str())
    return JSONResponse(content=build_papers_payload(PaperDB(), range_, today))


@router.get("/api/static")
async def api_static():
    """前端静态元数据（来源分类、评级标签/颜色、分区标题）。"""
    from src.static_meta import load_app_meta

    return JSONResponse(content=load_app_meta())


@router.get("/api/fetch/status")
async def api_fetch_status():
    """今天最近一次成功抓取的时间戳（SSE 重连时单次检查用；今日无成功记录返回 None）。

    遍历今天的日志找最新 success，避免「最新一条是 failed」时掩盖更早的成功记录。
    """
    db = PaperDB()
    log = db.get_last_success(datetime.date.today().isoformat())
    if log:
        return JSONResponse(
            content={
                "last_success": log.get("run_time"),
                "papers_new": log.get("papers_new", 0),
            }
        )
    return JSONResponse(content={"last_success": None, "papers_new": 0})


@router.get("/api/fetch/events")
async def api_fetch_events(request: Request):
    """抓取完成 SSE 流：调度器抓取完成后由后端推送 fetch-done 事件，前端据此刷新（取代轮询）。"""
    from src.serve.runtime import sse_event_stream

    rt = runtime_of(request)
    return StreamingResponse(
        sse_event_stream(rt, forward={"fetch-done"}, terminal=set()),
        media_type="text/event-stream",
    )


@router.post("/mark")
async def mark_paper(
    request: Request,
    source: str = Form(...),
    source_id: str = Form(...),
    mark_type: str = Form(...),
):
    """标记论文（纯 DB 操作，不入 Zotero；mark_type 取值 UserMark + pending）。"""
    allowed = {UserMark.IGNORE.value, UserMark.LURK.value, UserMark.PENDING.value}
    if mark_type not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid mark type: {mark_type}")

    runtime_of(request).log("info", f"Marking {source}:{source_id} as {mark_type}")

    PaperDB().update_mark(source, source_id, mark_type)
    return JSONResponse(
        content={"status": "ok", "source": source, "source_id": source_id, "mark_type": mark_type}
    )
