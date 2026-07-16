"""FastAPI 本地 Web 服务：统一入口（summary、notes、API）"""

import asyncio
import contextlib
import logging
import re

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import OUTPUT_DIR, ROOT_DIR

STATIC_DIR = ROOT_DIR / "static"

app = FastAPI(title="Paper Research", version="0.2.0")

# 挂载静态文件
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_db_conn = None
_settings = None
_logger = None


def init_logging():
    global _logger
    log_dir = OUTPUT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _logger = logging.getLogger("paper-research")
    _logger.setLevel(logging.INFO)
    if not _logger.handlers:
        handler = logging.FileHandler(
            log_dir / "server.log", encoding="utf-8", mode="a"
        )
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        _logger.addHandler(handler)


def set_db_conn(conn):
    global _db_conn
    _db_conn = conn


def set_settings(settings):
    global _settings
    _settings = settings


# ─── Helper: simple markdown to HTML ──────────────────────


def _md_to_html(text: str, arxiv_id: str = "") -> str:
    """Convert markdown to HTML, resolving figure paths against arxiv_id."""
    fig_prefix = f"/notes/{arxiv_id}/figures/" if arxiv_id else ""

    def _fix_img(m):
        alt, src = m.group(1), m.group(2)
        if src.startswith("figures/"):
            src = f"{fig_prefix}{src.split('/', 1)[1]}"
        return f'<img src="{src}" alt="{alt}" style="max-width:100%">'

    def _fix_link(m):
        text, href = m.group(1), m.group(2)
        if href.startswith("figures/"):
            href = f"{fig_prefix}{href.split('/', 1)[1]}"
        return f'<a href="{href}">{text}</a>'

    lines = text.split("\n")
    html_parts = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("<!--") and stripped.endswith("-->"):
            html_parts.append(f"<!--{stripped[4:-3]}-->")
            continue

        if stripped.startswith("## "):
            html_parts.append("</ul>" if in_list else "")
            in_list = False
            html_parts.append(f"<h2>{stripped[3:]}</h2>")
            continue
        if stripped.startswith("# "):
            html_parts.append("</ul>" if in_list else "")
            in_list = False
            html_parts.append(f"<h1>{stripped[2:]}</h1>")
            continue

        if stripped == "---":
            html_parts.append("</ul>" if in_list else "")
            in_list = False
            html_parts.append("<hr>")
            continue

        if stripped.startswith("- "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            item = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", stripped[2:])
            item = re.sub(r"!\[(.+?)\]\((.+?)\)", _fix_img, item)
            item = re.sub(r"\[(.+?)\]\((.+?)\)", _fix_link, item)
            html_parts.append(f"<li>{item}</li>")
            continue
        if in_list:
            html_parts.append("</ul>")
            in_list = False

        if not stripped:
            html_parts.append("<br>")
            continue

        if in_list:
            html_parts.append("</ul>")
            in_list = False
        paragraph = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        paragraph = re.sub(r"!\[(.+?)\]\((.+?)\)", _fix_img, paragraph)
        paragraph = re.sub(r"\[(.+?)\]\((.+?)\)", _fix_link, paragraph)
        html_parts.append(f"<p>{paragraph}</p>")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _log(level: str, msg: str):
    """写入日志文件"""
    if _logger:
        getattr(_logger, level, _logger.info)(msg)
    print(f"  [{level.upper()}] {msg}")


# ─── Summary ──────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    """动态渲染 HTML summary"""
    from src.db import get_papers_for_summary
    from src.serve.renderer import generate_summary_html

    if not _db_conn:
        return HTMLResponse(
            content="<h1>Database not initialized</h1>", status_code=500
        )

    grouped = get_papers_for_summary(_db_conn)
    path = generate_summary_html(grouped)
    html = path.read_text(encoding="utf-8")
    return HTMLResponse(content=html)


# ─── Notes Gallery ───────────────────────────────────────


@app.get("/notes", response_class=HTMLResponse)
async def notes_index():
    """动态渲染笔记画廊页"""
    from src.serve.renderer import generate_notes_index_html

    if not _db_conn:
        return HTMLResponse(
            content="<h1>Database not initialized</h1>", status_code=500
        )
    papers = [
        dict(r)
        for r in _db_conn.execute(
            "SELECT * FROM papers ORDER BY fetch_date DESC LIMIT 500"
        )
    ]
    path = generate_notes_index_html(papers, OUTPUT_DIR)
    html = path.read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/notes/{arxiv_id}", response_class=HTMLResponse)
async def note_detail(arxiv_id: str):
    """笔记页: 左栏PDF链接 + 右栏笔记内容(查看/编辑合并)"""
    note_path = OUTPUT_DIR / "notes" / arxiv_id / "note.md"
    if not note_path.exists():
        return HTMLResponse(
            content=f"<h1>Note not found: {arxiv_id}</h1>", status_code=404
        )

    raw = note_path.read_text(encoding="utf-8")
    body_html = _md_to_html(raw, arxiv_id)

    title_match = re.search(r"<h1>(.+?)</h1>", body_html)
    title = title_match.group(1) if title_match else f"Note: {arxiv_id}"

    # 论文元数据
    paper = None
    if _db_conn:
        from src.db import get_paper

        paper = get_paper(_db_conn, arxiv_id)

    from src.serve.renderer import render_note_detail_html

    html = render_note_detail_html(
        arxiv_id=arxiv_id,
        title=title or (paper.get("note_short_title", "") if paper else "") or arxiv_id,
        body_html=body_html,
        paper=paper,
    )
    return HTMLResponse(content=html)


@app.get("/api/pdf/{arxiv_id}")
async def proxy_pdf(arxiv_id: str):
    """代理 PDF：优先本地 → 下载 → HTML fallback"""
    local_pdf = OUTPUT_DIR / "notes" / arxiv_id / "paper.pdf"
    if local_pdf.exists() and local_pdf.stat().st_size > 10000:
        from fastapi.responses import FileResponse

        return FileResponse(
            str(local_pdf),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={arxiv_id}.pdf",
                "Access-Control-Allow-Origin": "*",
            },
        )
    # 本地无 PDF，尝试下载
    from src.network.arxiv import download_pdf

    try:
        pdf_path = await download_pdf(arxiv_id, OUTPUT_DIR)
        from fastapi.responses import FileResponse

        return FileResponse(
            str(pdf_path),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={arxiv_id}.pdf",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception:
        # 下载失败 → HTML fallback 页
        paper = None
        if _db_conn:
            from src.db import get_paper

            paper = get_paper(_db_conn, arxiv_id)
        p_title = paper.get("title", arxiv_id) if paper else arxiv_id
        html = f"""<html><body style="font-family:sans-serif;padding:40px;text-align:center">
            <h2>PDF not available</h2>
            <p>{p_title}</p>
            <a href="https://arxiv.org/abs/{arxiv_id}" target="_blank">View on Arxiv</a>
            <br><br>
            <a href="https://arxiv.org/pdf/{arxiv_id}" target="_blank">Download from Arxiv</a>
            </body></html>"""
        return HTMLResponse(content=html)


@app.get("/notes/{arxiv_id}/figures/{filename}")
async def note_figure(arxiv_id: str, filename: str):
    """提供笔记中的图表文件"""
    fig_path = OUTPUT_DIR / "notes" / arxiv_id / "figures" / filename
    if not fig_path.exists():
        raise HTTPException(status_code=404)
    from fastapi.responses import FileResponse

    return FileResponse(str(fig_path))


# ─── Mark API ─────────────────────────────────────────────


@app.post("/mark")
async def mark_paper(
    arxiv_id: str = Form(...),
    mark_type: str = Form(...),
    force: bool = Form(False),
):
    """标记论文 API（支持翻标记，force=true 强制重写笔记）"""
    from src.db import get_paper
    from src.db import mark_paper as db_mark

    if not _db_conn:
        raise HTTPException(status_code=500, detail="Database not initialized")

    paper = get_paper(_db_conn, arxiv_id)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper not found: {arxiv_id}")

    if mark_type not in ("ignore", "skim", "deep_read", "lurk", "pending"):
        raise HTTPException(status_code=400, detail=f"Invalid mark type: {mark_type}")

    # 翻标记检测
    old_mark = paper.get("user_mark")
    if old_mark and old_mark != mark_type:
        _log("info", f"Re-mark: {arxiv_id} {old_mark} -> {mark_type}")

    db_mark(_db_conn, arxiv_id, mark_type)
    _log("info", f"Marked {arxiv_id} as {mark_type}")

    if mark_type in ("skim", "deep_read"):
        note_path = OUTPUT_DIR / "notes" / arxiv_id / "note.md"
        if note_path.exists() and not force:
            _log("info", f"Note exists, skip: {arxiv_id}")
        else:
            asyncio.create_task(_extract_and_note(arxiv_id, mark_type))

    asyncio.create_task(_refresh_snapshot())
    asyncio.create_task(_refresh_notes_index())

    return JSONResponse(
        content={"status": "ok", "arxiv_id": arxiv_id, "mark_type": mark_type}
    )


# ─── Note Editor API ──────────────────────────────────────


@app.get("/api/note/{arxiv_id}")
async def get_note(arxiv_id: str):
    """获取笔记原始 markdown 内容（供编辑器加载）"""
    note_path = OUTPUT_DIR / "notes" / arxiv_id / "note.md"
    if not note_path.exists():
        return HTMLResponse(content="", status_code=404)
    content = note_path.read_text(encoding="utf-8")
    return HTMLResponse(content=content)


@app.put("/api/note/{arxiv_id}")
async def save_note(arxiv_id: str, request: Request):
    """保存笔记内容（从在线编辑器写入本地 .md 文件）"""
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")
    note_dir = OUTPUT_DIR / "notes" / arxiv_id
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / "note.md"
    note_path.write_text(body, encoding="utf-8")
    print(f"  [OK] Note saved: {arxiv_id}")
    return JSONResponse(content={"status": "ok", "arxiv_id": arxiv_id})


# ─── Categories API ───────────────────────────────────────


@app.get("/api/categories")
async def list_categories():
    """获取所有分类"""
    from src.db import get_all_categories

    if not _db_conn:
        raise HTTPException(status_code=500, detail="Not initialized")
    return JSONResponse(content=get_all_categories(_db_conn))


@app.post("/api/categories")
async def create_category(name: str = Form(...)):
    """新建自定义分类"""
    from src.db import add_category

    if not _db_conn:
        raise HTTPException(status_code=500, detail="Not initialized")
    cat_id = add_category(_db_conn, name)
    return JSONResponse(content={"id": cat_id, "name": name})


@app.delete("/api/categories/{category_id}")
async def delete_category(category_id: int):
    """删除自定义分类"""
    from src.db import delete_category

    if not _db_conn:
        raise HTTPException(status_code=500, detail="Not initialized")
    ok = delete_category(_db_conn, category_id)
    asyncio.create_task(_refresh_notes_index())
    return JSONResponse(content={"deleted": ok})


@app.post("/api/note/{arxiv_id}/categories")
async def set_note_categories_api(arxiv_id: str, request: Request):
    """设置笔记多分类（逗号分隔 ID，如 \"1,3,5\"，空字符串清空）"""
    from src.db import set_note_categories

    if not _db_conn:
        raise HTTPException(status_code=500, detail="Not initialized")
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")
    ids = [int(x.strip()) for x in body.split(",") if x.strip()]
    set_note_categories(_db_conn, arxiv_id, ids)
    return JSONResponse(content={"status": "ok", "categories": ids})


@app.post("/api/note/{arxiv_id}/short_info")
async def update_short_info(
    arxiv_id: str, short_title: str = Form(""), description: str = Form("")
):
    """更新笔记短标题和说明"""
    from src.db import update_note_short_info

    if not _db_conn:
        raise HTTPException(status_code=500, detail="Not initialized")
    update_note_short_info(_db_conn, arxiv_id, short_title, description)
    return JSONResponse(content={"status": "ok"})


# ─── Data API ─────────────────────────────────────────────


@app.get("/api/keywords")
async def get_keywords():
    """读取 keywords.json"""
    from src.config import load_keywords

    return JSONResponse(content=load_keywords())


@app.post("/api/keywords")
async def save_keywords(request: Request):
    """保存 keywords.json"""
    from src.config import save_keywords

    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(
            status_code=400, detail="Expected a list of keyword objects"
        )
    save_keywords(body)
    return JSONResponse(content={"status": "ok", "count": len(body)})


# ─── Settings API ──────────────────────────────────────────


@app.get("/api/settings")
async def get_settings():
    """获取当前 arxiv 设置"""
    if not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    return JSONResponse(
        content={
            "target_new_per_keyword": _settings.arxiv.target_new_per_keyword,
            "lookback_days": _settings.arxiv.lookback_days,
            "max_concurrent_requests": _settings.arxiv.max_concurrent_requests,
        }
    )


@app.put("/api/settings")
async def update_settings(request: Request):
    """更新 arxiv 设置并持久化到 settings.json"""
    if not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    import json

    from src.config import CONFIG_DIR

    body = await request.json()
    settings_path = CONFIG_DIR / "settings.json"

    existing = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    if "arxiv" not in existing:
        existing["arxiv"] = {}

    if "target_new_per_keyword" in body:
        val = int(body["target_new_per_keyword"])
        existing["arxiv"]["target_new_per_keyword"] = val
        _settings.arxiv.target_new_per_keyword = val
    if "lookback_days" in body:
        val = int(body["lookback_days"])
        existing["arxiv"]["lookback_days"] = val
        _settings.arxiv.lookback_days = val

    settings_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    _log("info", f"Settings updated: target_new_per_keyword={_settings.arxiv.target_new_per_keyword}, lookback_days={_settings.arxiv.lookback_days}")
    return JSONResponse(content={"status": "ok"})


@app.get("/api/stats")
async def stats():
    """获取统计信息"""
    from src.db import get_stats

    if not _db_conn:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return JSONResponse(content=get_stats(_db_conn))


@app.get("/api/papers")
async def papers():
    """获取论文列表"""
    from src.db import get_papers_for_summary

    if not _db_conn:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return JSONResponse(content=get_papers_for_summary(_db_conn))


# ─── Fetch Status ────────────────────────────────────────

FETCH_STATUS_CACHE = {"fetched": 0, "new": 0, "pending": False}
_sse_clients: list["asyncio.Queue[dict]"] = []


def _notify_sse_clients(data: dict):
    """推送结果给所有等待的 SSE 客户端"""
    for q in _sse_clients[:]:
        with contextlib.suppress(Exception):
            q.put_nowait(data)


@app.get("/api/fetch-status-stream")
async def fetch_status_stream():
    """SSE 端点：后端主动推送抓取结果，前端无需轮询"""
    queue: asyncio.Queue = asyncio.Queue()
    _sse_clients.append(queue)

    async def _event_stream():
        try:
            result = await asyncio.wait_for(queue.get(), timeout=300)
            import json

            yield f"data: {json.dumps(result)}\n\n"
        except TimeoutError:
            import json

            yield f"data: {json.dumps({'fetched': 0, 'new': 0, 'timeout': True})}\n\n"
        finally:
            if queue in _sse_clients:
                _sse_clients.remove(queue)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@app.get("/api/fetch_status")
async def get_fetch_status():
    """获取抓取状态（前端轮询用）"""
    global FETCH_STATUS_CACHE
    status_file = OUTPUT_DIR / "fetch_status.json"
    if status_file.exists():
        try:
            import json

            data = json.loads(status_file.read_text(encoding="utf-8"))
            status_file.unlink(missing_ok=True)  # 读取后删除，表示已消费
            FETCH_STATUS_CACHE = data
            FETCH_STATUS_CACHE["pending"] = False
            return JSONResponse(content=data)
        except Exception:
            pass
    return JSONResponse(content=FETCH_STATUS_CACHE)


# ─── Action API ──────────────────────────────────────────


@app.post("/api/fetch")
async def api_fetch(
    keyword: str = Form(""),
    arxiv_cats: str = Form(""),
    max_results: int = Form(20)
):
    """后台抓取"""
    if not _db_conn or not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")

    asyncio.create_task(_do_fetch_safe(keyword, arxiv_cats, max_results))
    _log(
        "info",
        f"Fetch triggered: keyword={keyword}, cats={arxiv_cats}, max={max_results}",
    )
    return JSONResponse(
        content={"status": "ok", "message": "Fetch started in background"}
    )


@app.get("/api/search-db")
async def search_db(q: str = ""):
    """搜索数据库中已有的论文"""
    if not q.strip():
        return JSONResponse(content={"papers": []})
    if not _db_conn:
        return JSONResponse(content={"papers": []})
    like = f"%{q}%"
    cursor = _db_conn.execute(
        """SELECT * FROM papers
           WHERE title LIKE ? OR authors LIKE ? OR arxiv_id LIKE ? OR abstract LIKE ?
           ORDER BY published DESC LIMIT 50""",
        (like, like, like, like),
    )
    papers = [dict(r) for r in cursor.fetchall()]
    return JSONResponse(content={"papers": papers, "count": len(papers)})


@app.get("/api/search")
async def api_search(q: str = "", max: int = 20, cats: str = ""):
    """搜索 Arxiv 并返回结果列表"""
    if not q.strip():
        return JSONResponse(content={"papers": []})
    from src.network.arxiv import search_arxiv

    categories = [c.strip() for c in cats.split(",") if c.strip()] if cats else None
    papers = await search_arxiv(q.strip(), min(max, 100), categories)
    return JSONResponse(content={"papers": papers, "count": len(papers)})


@app.post("/api/import-papers")
async def api_import_papers(request: Request):
    """导入选中的论文（按 arxiv_id 列表）"""
    if not _db_conn or not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    asyncio.create_task(_do_import_papers_safe(ids))
    _log("info", f"Import triggered: {len(ids)} papers")
    return JSONResponse(
        content={
            "status": "ok",
            "message": f"Importing {len(ids)} papers",
            "count": len(ids),
        }
    )


async def _do_import_papers(arxiv_ids: list[str]):
    """后台导入论文（按 arxiv_id 获取 -> 入库 -> LLM 摘要)"""
    import datetime

    from src.agents import PaperScorer
    from src.db import exists, insert_paper, update_paper_summary
    from src.network.arxiv import fetch_by_ids

    today = datetime.date.today().isoformat()
    print(f"  [i] Importing {len(arxiv_ids)} papers by ID...")

    try:
        papers = await fetch_by_ids(arxiv_ids)
    except Exception as e:
        print(f"  [!] Failed to fetch papers: {e}")
        return

    new_papers = []
    for p in papers:
        aid = p["arxiv_id"]
        if not exists(_db_conn, aid):
            p["fetch_date"] = today
            insert_paper(_db_conn, p)
            new_papers.append(p)
        else:
            print(f"  [-] Paper already exists: {aid}")

    # LLM 摘要
    api_key = _settings.llm.api_key
    if new_papers:
        scorer = PaperScorer(
            api_key=api_key,
            api_base=_settings.llm.api_base,
            model=_settings.llm.model,
            temperature=_settings.llm.temperature,
            max_tokens=_settings.llm.max_tokens,
        )
        if api_key:
            results = await scorer.score_batch_async(new_papers)
            for paper, result in zip(new_papers, results, strict=False):
                if result:
                    update_paper_summary(
                        _db_conn,
                        paper["arxiv_id"],
                        result.summary,
                        result.remark,
                        result.reason,
                        result.score,
                    )
        else:
            loop_import = asyncio.get_running_loop()
            for paper in new_papers:
                result = await loop_import.run_in_executor(
                    None,
                    scorer.score,
                    paper.get("title", ""),
                    paper.get("abstract", ""),
                )
                if result:
                    update_paper_summary(
                        _db_conn,
                        paper["arxiv_id"],
                        result.summary,
                        result.remark,
                        result.reason,
                        result.score,
                    )

    await _refresh_snapshot()
    await _refresh_notes_index()

    # 写抓取状态
    import json

    result_data = {"fetched": len(papers), "new": len(new_papers)}
    status_file = OUTPUT_DIR / "fetch_status.json"
    status_file.write_text(json.dumps(result_data), encoding="utf-8")
    global FETCH_STATUS_CACHE
    FETCH_STATUS_CACHE = result_data
    _notify_sse_clients(result_data)
    print(f"  [OK] Import done: {len(papers)} papers, {len(new_papers)} new")


async def _do_import_papers_safe(arxiv_ids: list[str]):
    """带错误保护的 _do_import_papers 包装"""
    try:
        await _do_import_papers(arxiv_ids)
    except Exception as e:
        import traceback

        traceback.print_exc()
        _notify_sse_clients({"fetched": 0, "new": 0, "error": str(e)})


async def _do_fetch(
    keyword: str, arxiv_cats: str, max_results: int
):
    """后台执行抓取"""
    import datetime

    from src.agents import PaperScorer
    from src.config import get_active_keywords
    from src.db import (exists, get_keyword_paper_count, insert_paper,
                        update_paper_summary)
    from src.network.arxiv import fetch_all

    today = datetime.date.today().isoformat()

    global FETCH_STATUS_CACHE
    FETCH_STATUS_CACHE["pending"] = True

    per_keyword_max_results = {}
    if keyword:
        cats = (
            [c.strip() for c in arxiv_cats.split(",") if c.strip()]
            if arxiv_cats
            else None
        )
        kws = [
            {
                "keyword": keyword,
                "arxiv_cats": cats,
                "max_results": max_results,
                "active": True,
            }
        ]
        per_keyword_max_results[keyword] = max_results
    else:
        kws = get_active_keywords()
        target_new = _settings.arxiv.target_new_per_keyword
        for kw in kws:
            existing = get_keyword_paper_count(_db_conn, kw["keyword"])
            desired = existing + target_new
            capped = min(desired, 500)
            per_keyword_max_results[kw["keyword"]] = capped

    if not kws:
        print("  [!] No keywords for background fetch")
        return

    papers, _total_fetched, _duplicate_count = await fetch_all(
        kws, _settings, per_keyword_max_results=per_keyword_max_results
    )
    new_papers = []
    for p in papers:
        if not exists(_db_conn, p["arxiv_id"]):
            p["fetch_date"] = today
            insert_paper(_db_conn, p)
            new_papers.append(p)

    api_key = _settings.llm.api_key
    scorer = PaperScorer(
        api_key=api_key,
        api_base=_settings.llm.api_base,
        model=_settings.llm.model,
        temperature=_settings.llm.temperature,
        max_tokens=_settings.llm.max_tokens,
    )
    if new_papers:
        if api_key:
            results = await scorer.score_batch_async(new_papers)
            for paper, result in zip(new_papers, results, strict=False):
                if result:
                    update_paper_summary(
                        _db_conn,
                        paper["arxiv_id"],
                        result.summary,
                        result.remark,
                        result.reason,
                        result.score,
                    )
        else:
            loop = asyncio.get_running_loop()
            for paper in new_papers:
                result = await loop.run_in_executor(
                    None,
                    scorer.score,
                    paper.get("title", ""),
                    paper.get("abstract", ""),
                )
                if result:
                    update_paper_summary(
                        _db_conn,
                        paper["arxiv_id"],
                        result.summary,
                        result.remark,
                        result.reason,
                        result.score,
                    )

    await _refresh_snapshot()
    await _refresh_notes_index()

    # 记录抓取日志
    from src.db import insert_fetch_log

    insert_fetch_log(
        _db_conn,
        keywords_used=len(kws),
        papers_fetched=len(papers),
        papers_new=len(new_papers),
        papers_updated=0,
        papers_summarized=len(new_papers),
        status="success",
    )

    # 写抓取状态供前端轮询
    import json

    result_data = {"fetched": len(papers), "new": len(new_papers)}
    status_file = OUTPUT_DIR / "fetch_status.json"
    status_file.write_text(json.dumps(result_data), encoding="utf-8")
    FETCH_STATUS_CACHE = result_data
    _notify_sse_clients(result_data)
    print(f"  [OK] Background fetch done: {len(papers)} fetched, {len(new_papers)} new")


async def _do_fetch_safe(
    keyword: str, arxiv_cats: str, max_results: int
):
    """带错误保护的 _do_fetch 包装"""
    try:
        await _do_fetch(keyword, arxiv_cats, max_results)
    except Exception as e:
        import traceback

        traceback.print_exc()
        err_data = {"fetched": 0, "new": 0, "error": str(e)}
        _notify_sse_clients(err_data)


@app.post("/api/refresh")
async def api_refresh():
    """刷新 HTML 快照"""
    asyncio.create_task(_refresh_snapshot())
    asyncio.create_task(_refresh_notes_index())
    _log("info", "HTML snapshots refreshed")
    return JSONResponse(content={"status": "ok", "message": "Refresh triggered"})


@app.post("/api/notify")
async def api_notify():
    """发送通知（Toast + 邮件），包含 server URL"""
    if not _db_conn or not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    from src.db import get_pending_keywords, get_stats
    from src.notify import send_email_if_configured, send_windows_toast

    stats = get_stats(_db_conn)
    server_url = f"http://{_settings.server.host}:{_settings.server.port}"
    important = stats.get("important", 0)
    pending = stats.get("summarized_pending", 0)
    keywords = get_pending_keywords(_db_conn)
    send_windows_toast(
        "Arxiv Paper Research",
        f"Important: {important}, Pending: {pending}",
        keywords=keywords,
        url=server_url,
    )
    send_email_if_configured(_settings, stats, server_url, keywords=keywords)
    _log("info", f"Notifications sent (Toast + email) to {server_url}")
    return JSONResponse(
        content={"status": "ok", "message": f"Notification sent. Server: {server_url}"}
    )


# ─── Background Tasks ─────────────────────────────────────


async def _extract_and_note(arxiv_id: str, mark_type: str = "deep_read"):
    """在后台线程池中运行笔记生成（避免阻塞 serve 事件循环）"""
    from src.agents.note_agent import NoteAgent

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: asyncio.run(
                NoteAgent.generate_from_arxiv_id(arxiv_id, OUTPUT_DIR, mode=mark_type)
            ),
        )
        print(f"  [OK] Note generated: {arxiv_id} ({mark_type})")
        await _refresh_notes_index()
    except Exception as e:
        print(f"  [!] Note generation failed [{arxiv_id}]: {e}")


async def _refresh_snapshot():
    try:
        from src.db import get_papers_for_summary
        from src.serve.renderer import generate_summary_html

        if _db_conn:
            grouped = get_papers_for_summary(_db_conn)
            generate_summary_html(grouped, OUTPUT_DIR)
    except Exception as e:
        print(f"  [!] Snapshot refresh failed: {e}")


async def _refresh_notes_index():
    try:
        from src.serve.renderer import generate_notes_index_html

        if _db_conn:
            papers = [
                dict(r)
                for r in _db_conn.execute(
                    "SELECT * FROM papers ORDER BY fetch_date DESC LIMIT 500"
                )
            ]
            generate_notes_index_html(papers, OUTPUT_DIR)
    except Exception as e:
        print(f"  [!] Notes index refresh failed: {e}")


# ─── Server Entry Point ──────────────────────────────────


def run_server(settings, conn):
    """启动服务"""
    import uvicorn

    init_logging()
    set_db_conn(conn)
    set_settings(settings)
    server_cfg = settings.server
    _log("info", "Server started")
    print(f"  [OK] Server: http://{server_cfg.host}:{server_cfg.port}")
    print(f"  [OK] Summary: http://{server_cfg.host}:{server_cfg.port}/")
    print(f"  [OK] Notes:   http://{server_cfg.host}:{server_cfg.port}/notes")
    uvicorn.run(
        "src.serve.server:app",
        host=server_cfg.host,
        port=server_cfg.port,
        log_level="info",
    )
