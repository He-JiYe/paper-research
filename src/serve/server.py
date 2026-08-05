"""FastAPI 本地 Web 服务：核心审阅工作流

专注核心功能：
1. 论文审阅首页（待审阅 + 已处理概览）
2. PDF 代理（手动查看，抓取时不自动下载）
3. 标记论文（审阅决策 → 导入 Zotero + 设置 shortTitle）
4. 触发抓取
5. 统计数据
"""

import asyncio
import contextlib
import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import OUTPUT_DIR
from src.db import PaperDB
from src.zotero import ZoteroClient
from src.zotero.models import COLLECTION_ROOT, generate_short_title

# 前端 SPA 静态目录（前后端分离：静态资产由 StaticFiles 托管，数据经 /api/* JSON 获取）
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

_db_zotero: ZoteroClient | None = None
_settings = None
_logger = None
_scheduler = None

_sse_clients: list["asyncio.Queue[dict]"] = []


@contextlib.asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    global _scheduler
    # 确保静态元数据 JSON（data/static/app-meta.json）存在
    from src.serve.static_data import ensure_static_data

    ensure_static_data()
    if _settings:
        try:
            from src.serve.scheduler import FetchScheduler

            _scheduler = FetchScheduler(_settings, _db_zotero)
            # 必须保留强引用：否则任务可能被 GC，调度器静默死亡
            _scheduler._task = asyncio.create_task(_scheduler.start())
        except Exception as e:
            print(f"  ⚠️ 调度器启动失败: {e}")
    yield
    if _scheduler:
        _scheduler.stop()


app = FastAPI(title="Paper Research", version="0.6.0", lifespan=_app_lifespan)


def init_logging():
    global _logger
    log_dir = OUTPUT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _logger = logging.getLogger("paper-research")
    _logger.setLevel(logging.INFO)
    if not _logger.handlers:
        handler = logging.FileHandler(log_dir / "server.log", encoding="utf-8", mode="a")
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        _logger.addHandler(handler)


def set_zotero_client(zotero: ZoteroClient):
    global _db_zotero
    _db_zotero = zotero


def set_settings(settings):
    global _settings
    _settings = settings


# ─── Helper ────────────────────────────────────────────────


def _log(level: str, msg: str):
    if _logger:
        getattr(_logger, level, _logger.info)(msg)
    print(f"  [{level.upper()}] {msg}")


# ─── 数据 API：前后端分离的核心 ─────────────────────────────


@app.get("/api/papers")
async def api_papers():
    """返回分组论文 JSON：unmarked/marked/lurk + stats + update_time + fetch_config。

    动态数据（读 SQLite），前端据此动态渲染，替代原服务端 Jinja2 渲染。
    """
    from src.serve.payloads import build_papers_payload

    db = PaperDB()
    return JSONResponse(content=build_papers_payload(db))


@app.get("/api/static")
async def api_static():
    """返回前端静态元数据（arxiv 分类、评级标签/颜色、分区标题）。

    静态数据来自 data/static/app-meta.json（可编辑覆盖层），缺键时回退默认值。
    """
    from src.serve.static_data import load_app_meta

    return JSONResponse(content=load_app_meta())


# ─── PDF 代理 ─────────────────────────────────────────────


_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


@app.get("/api/pdf/{arxiv_id}")
async def proxy_pdf(arxiv_id: str):
    """重定向到 arxiv PDF（不下载缓存）。"""
    if not _ARXIV_ID_RE.match(arxiv_id):
        raise HTTPException(status_code=400, detail=f"Invalid arxiv_id: {arxiv_id}")
    return RedirectResponse(url=f"https://arxiv.org/pdf/{arxiv_id}")


@app.get("/api/scheduler/status")
async def api_scheduler_status():
    """返回内置调度器状态（上次/下次抓取时间）。"""
    if not _scheduler:
        return JSONResponse(content={"enabled": False, "reason": "scheduler not started"})
    return JSONResponse(content=_scheduler.status)


# ─── 标记 API ─────────────────────────────────────────────


@app.post("/mark")
async def mark_paper(
    arxiv_id: str = Form(...),
    mark_type: str = Form(...),
    short_title: str = Form(""),
):
    """标记论文（纯 DB 操作，不入 Zotero）。

    - ignore: 忽略
    - lurk: 延后处理
    - pending: 取消标记，放回待审阅
    """
    if mark_type not in ("ignore", "lurk", "pending"):
        raise HTTPException(status_code=400, detail=f"Invalid mark type: {mark_type}")

    _log("info", f"Marking {arxiv_id} as {mark_type}")

    db = PaperDB()
    db.update_mark(arxiv_id, mark_type, short_title=short_title)
    return JSONResponse(content={"status": "ok", "arxiv_id": arxiv_id, "mark_type": mark_type})


# ─── Zotero API ────────────────────────────────────────────


@app.get("/api/zotero/collections")
async def api_zotero_collections(include_project: bool = False):
    """获取 Zotero 分类列表，供前端导入下拉框使用。

    默认排除项目自动维护的 "Paper Research" 分类树
    （导入时论文本就会自动归入 Inbox/Keywords，无需在下拉框重复展示）；
    include_project=true 时返回全部分类。
    """
    if not _db_zotero:
        raise HTTPException(status_code=500, detail="Zotero not connected")
    # pyzotero 为同步库，放线程执行避免阻塞事件循环
    collections = await asyncio.to_thread(_db_zotero.get_all_collections)
    if not include_project:
        collections = [
            c
            for c in collections
            if c["path"] != COLLECTION_ROOT
            and not c["path"].startswith(f"{COLLECTION_ROOT} / ")
        ]
    return JSONResponse(content={"collections": collections})


@app.post("/api/zotero/import")
async def api_zotero_import(
    arxiv_id: str = Form(...),
    collection_key: str = Form(""),
    short_title: str = Form(""),
):
    """导入论文到 Zotero，指定分类和 shortTitle。
    """
    if not _db_zotero:
        raise HTTPException(status_code=500, detail="Zotero not connected")

    _log("info", f"Zotero import: {arxiv_id} -> key:{collection_key}")

    db = PaperDB()
    paper = db.get_paper(arxiv_id)
    if not paper:
        from src.network.factory import get_source

        source = get_source(_settings)
        papers = await source.fetch_by_ids([arxiv_id])
        if not papers:
            raise HTTPException(status_code=404, detail=f"Paper not found: {arxiv_id}")
        paper = papers[0]
        if not paper.get("llm_remark"):
            from src.scorer import PaperScorer

            scorer = PaperScorer(
                api_key=_settings.llm.api_key,
                api_base=_settings.llm.api_base,
                model=_settings.llm.model,
                temperature=_settings.llm.temperature,
                max_tokens=_settings.llm.max_tokens,
            )
            # 同步 LLM 调用放线程执行，避免阻塞事件循环
            result = await asyncio.to_thread(
                scorer.score, paper.get("title", ""), paper.get("abstract", "")
            )
            if result:
                paper["llm_summary"] = result.summary
                paper["llm_remark"] = result.remark
                paper["llm_reason"] = result.reason
                paper["llm_score"] = result.score

    existing = await asyncio.to_thread(_db_zotero.get_item_by_arxiv_id, arxiv_id)
    if existing:
        item_key = existing["data"]["key"]
        _log("info", f"Paper already in Zotero: {item_key}")
    else:
        from src.scorer import LLMResult

        llm_result = None
        if paper.get("llm_remark"):
            llm_result = LLMResult(
                summary=paper.get("llm_summary", ""),
                remark=paper.get("llm_remark", ""),
                reason=paper.get("llm_reason", ""),
                score=paper.get("llm_score", 0),
            )
        item_key = await asyncio.to_thread(_db_zotero.create_item, paper, llm_result)
        if not item_key:
            raise HTTPException(status_code=500, detail="Failed to create Zotero item")

    final_title = short_title or generate_short_title(paper)
    await asyncio.to_thread(_db_zotero.set_short_title, item_key, final_title)

    if collection_key:
        # 按 key 精确归档到已有分类（不创建新分类）
        await asyncio.to_thread(_db_zotero.add_to_collection, item_key, collection_key)

    # PDF 附件：下载 arXiv PDF 并上传为子附件。
    # 失败不阻塞导入（Zotero 存储配额/网络问题不应影响元数据导入）
    # pdf_attached = False
    # try:
    #     has_pdf = await asyncio.to_thread(_db_zotero.has_pdf_attachment, item_key)
    #     if not has_pdf:
    #         import tempfile

    #         from src.network.arxiv import download_pdf

    #         with tempfile.TemporaryDirectory() as tmpdir:
    #             pdf_path = await asyncio.to_thread(
    #                 download_pdf, arxiv_id, tmpdir, paper.get("version", 0) or 0
    #             )
    #             await asyncio.to_thread(
    #                 _db_zotero.attach_pdf, item_key, str(pdf_path), "Full Text PDF"
    #             )
    #         pdf_attached = True
    # except Exception as e:
    #     _log("warning", f"PDF 附件上传失败（导入本身成功）: {arxiv_id}: {e}")

    # 导入成功：标记为已处理（从待审阅池移除，记入已处理组）
    db.update_mark(arxiv_id, "imported", short_title=final_title, zotero_key=item_key)
    _log("info", f"Zotero import complete: {arxiv_id} -> {item_key} (pdf={False})")
    return JSONResponse(
        content={
            "status": "ok",
            "arxiv_id": arxiv_id,
            "zotero_key": item_key,
            "pdf_attached": False,
        }
    )


# ─── 抓取 API ─────────────────────────────────────────────


@app.get("/api/fetch-status-stream")
async def fetch_status_stream():
    queue: asyncio.Queue = asyncio.Queue()
    _sse_clients.append(queue)

    async def _event_stream():
        try:
            result = await asyncio.wait_for(queue.get(), timeout=300)
            yield f"data: {json.dumps(result)}\n\n"
        except TimeoutError:
            yield f"data: {json.dumps({'fetched': 0, 'new': 0, 'timeout': True})}\n\n"
        finally:
            if queue in _sse_clients:
                _sse_clients.remove(queue)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@app.post("/api/fetch")
async def api_fetch(
    keyword: str = Form(""), max_results: int = Form(20), mode: str = Form("incremental")
):
    """搜索抓取：指定关键词、数量、模式抓取论文到 pending 池"""
    if not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    asyncio.create_task(_do_fetch_safe(keyword, max_results, mode))
    return JSONResponse(content={"status": "ok", "message": "Fetch started"})


@app.post("/api/search-import")
async def api_search_import(data: dict):
    """导入选中的论文（仅导入指定的 arxiv_ids）。"""
    if not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    arxiv_ids = data.get("arxiv_ids", [])
    if not arxiv_ids:
        raise HTTPException(status_code=400, detail="No arxiv_ids provided")

    from src.network.factory import get_source

    source = get_source(_settings)
    papers = await source.fetch_by_ids(arxiv_ids)

    db = PaperDB()
    existing_ids = db.get_existing_arxiv_ids()
    new_papers = []
    for p in papers:
        if p["arxiv_id"] not in existing_ids:
            new_papers.append(p)

    if new_papers:
        # LLM 评分
        _log("info", f"LLM 初筛 {len(new_papers)} 篇导入论文...")
        from src.scorer import PaperScorer

        scorer = PaperScorer(
            api_key=_settings.llm.api_key,
            api_base=_settings.llm.api_base,
            model=_settings.llm.model,
            temperature=_settings.llm.temperature,
            max_tokens=_settings.llm.max_tokens,
        )
        results = await scorer.score_batch_async(new_papers)
        for paper, result in zip(new_papers, results, strict=False):
            if result:
                paper["llm_summary"] = result.summary
                paper["llm_remark"] = result.remark
                paper["llm_reason"] = result.reason
                paper["llm_score"] = result.score
                paper["status"] = "summarized"
            else:
                paper["status"] = "new"
        db.add_papers(new_papers)
        _log("info", f"搜索导入: {len(new_papers)} 篇新论文")

    return JSONResponse(
        content={"status": "ok", "searched": len(papers), "imported": len(new_papers)}
    )


@app.post("/api/search-preview")
async def api_search_preview(query: str = Form(...), max_results: int = Form(20)):
    """搜索 Arxiv 预览结果（不导入，仅返回论文列表）"""
    if not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    from src.network.factory import get_source

    source = get_source(_settings)
    papers = await source.search(query, max_results=min(max_results, 50))
    if _db_zotero:
        existing_zotero = await asyncio.to_thread(_db_zotero.get_existing_arxiv_ids)
    else:
        existing_zotero = set()
    db = PaperDB()
    existing_ids = db.get_existing_arxiv_ids()

    for p in papers:
        p["_in_zotero"] = p["arxiv_id"] in existing_zotero
        p["_in_pending"] = p["arxiv_id"] in existing_ids

    return JSONResponse(content={"papers": papers})


@app.post("/api/keyword-fetch")
async def api_keyword_fetch(max_results: int = Form(20), mode: str = Form("incremental")):
    """关键词抓取：使用 config.yaml 中配置的关键词抓取论文"""
    if not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")
    asyncio.create_task(_do_fetch_safe("", max_results, mode))
    return JSONResponse(content={"status": "ok", "message": "Keyword fetch started"})


@app.get("/api/keywords")
async def api_get_keywords():
    """获取当前配置文件中的关键词列表"""
    from src.config import load_settings

    cfg = load_settings()
    return JSONResponse(
        content={
            "keywords": [
                {
                    "keyword": kw.keyword,
                    "arxiv_cats": kw.arxiv_cats or [],
                    "active": kw.active,
                }
                for kw in cfg.keywords
            ],
            "fetch_config": {
                "max_results": cfg.fetch.max_results,
                "lookback_days": cfg.fetch.lookback_days,
            },
        }
    )


@app.post("/api/keywords")
async def api_save_keywords(data: dict):
    """保存关键词和 fetch 配置到 config.yaml"""
    import yaml

    from src.config import CONFIG_DIR, load_settings

    config_path = CONFIG_DIR / "config.yaml"

    # 读取现有配置
    existing = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    # 更新 keywords（arxiv_cats 归一化为空数组避免 YAML null/[] 振荡）
    keywords_raw = data.get("keywords", [])
    existing["keywords"] = [
        {
            "keyword": kw["keyword"],
            "arxiv_cats": kw.get("arxiv_cats") or [],
            "active": kw.get("active", True),
        }
        for kw in keywords_raw
    ]

    # 更新 fetch 配置（max_results / lookback_days）
    fetch_config = data.get("fetch_config")
    if fetch_config:
        if "fetch" not in existing or not isinstance(existing["fetch"], dict):
            existing["fetch"] = {}
        if "max_results" in fetch_config:
            existing["fetch"]["max_results"] = int(fetch_config["max_results"])
        if "lookback_days" in fetch_config:
            existing["fetch"]["lookback_days"] = int(fetch_config["lookback_days"])

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # 刷新内存中的 _settings，使后续抓取立即生效
    global _settings
    _settings = load_settings()

    _log("info", f"配置已保存: {len(keywords_raw)} 个关键词")
    return JSONResponse(content={"status": "ok", "count": len(keywords_raw)})


@app.post("/api/push")
async def api_push():
    """发送邮件通知（仅统计摘要 + Top 3 + 审阅页面链接）。"""
    if not _settings:
        raise HTTPException(status_code=500, detail="Not initialized")

    from src.config import get_active_keywords
    from src.notify import EmailNotifier

    db = PaperDB()
    stats = db.get_stats()
    pending = db.get_pending()
    if not pending:
        return JSONResponse(content={"status": "ok", "sent": False, "reason": "no pending papers"})

    server_cfg = _settings.server
    server_url = f"http://{server_cfg.host}:{server_cfg.port}"

    notifier = EmailNotifier(_settings.notification)
    sent = await asyncio.to_thread(
        notifier.send_fetch_report,
        stats=stats,
        new_papers=pending,
        keywords=[kw.keyword for kw in get_active_keywords()],
        server_url=server_url,
    )
    return JSONResponse(content={"status": "ok", "sent": sent})


# ─── 后台任务 ─────────────────────────────────────────────


async def _do_fetch(keyword: str, max_results: int, mode: str = "incremental"):
    from src.config import KeywordEntry, get_active_keywords
    from src.network.fetch_pipeline import run_fetch_pipeline

    if keyword:
        kws = [KeywordEntry(keyword=keyword, arxiv_cats=None, active=True)]
    else:
        kws = get_active_keywords()

    if not kws:
        print("  [!] No keywords for background fetch")
        return

    db = PaperDB()
    result = await run_fetch_pipeline(_settings, kws, max_results, db=db, mode=mode)
    _notify_sse_clients({"fetched": result.get("fetched", 0), "new": result.get("new", 0)})


async def _do_fetch_safe(keyword: str, max_results: int, mode: str):
    try:
        await _do_fetch(keyword, max_results, mode)
    except Exception as e:
        import traceback

        traceback.print_exc()
        _notify_sse_clients({"fetched": 0, "new": 0, "error": str(e)})


def _notify_sse_clients(data: dict):
    for q in _sse_clients[:]:
        with contextlib.suppress(Exception):
            q.put_nowait(data)


# ─── 前端静态资源（前后端分离）────────────────────────────
# 须放在所有 /api/* 路由之后注册，避免 catch-all 遮蔽 JSON API
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


# ─── 启动入口 ─────────────────────────────────────────────


def run_server(settings):
    import uvicorn

    init_logging()

    zotero = None
    try:
        zotero = ZoteroClient(
            settings.zotero.api_key,
            settings.zotero.library_id,
            settings.zotero.library_type,
        )
        zotero.init_collections()
        print("  [OK] Zotero connected")
    except Exception as e:
        print(f"  ⚠️ Zotero 连接失败: {e}")
        print("  ⚠️ 标记/导入 Zotero 功能不可用，但审阅页面仍可正常访问")
        zotero = None

    if zotero:
        set_zotero_client(zotero)
    set_settings(settings)

    server_cfg = settings.server
    print(f"  [OK] Server: http://{server_cfg.host}:{server_cfg.port}")
    uvicorn.run(
        "src.serve.server:app", host=server_cfg.host, port=server_cfg.port, log_level="info"
    )
