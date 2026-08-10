"""serve 包入口：FastAPI app 工厂 + 启动入口。

运行时上下文在 ``app.state.runtime``（Runtime），由 lifespan/run_server 注入；
路由只剩 papers（论文+静态+标记）与 zotero（导入）——前端已取消抓取/推送/搜索。
"""

import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src import __version__
from src.paths import APP_DIR
from src.serve.routes import papers, zotero
from src.serve.runtime import Runtime

logger = logging.getLogger(__name__)


def _log_scheduler_exception(task: asyncio.Task) -> None:
    """调度器后台任务的异常回调：捕获并记录，避免静默死亡无日志。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("调度器任务异常退出: %s", exc)


@contextlib.asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    """启动动作：确保 app-meta.json 存在 + 启动内置调度器。"""
    from src.static_meta import ensure_static_data

    ensure_static_data()
    rt = _app.state.runtime
    if rt.settings:
        try:
            from src.serve.scheduler import FetchScheduler

            rt.scheduler = FetchScheduler(rt.settings, on_fetch_done=rt.publish_sse)
            # 必须保留强引用：否则任务可能被 GC，调度器静默死亡
            task = asyncio.create_task(rt.scheduler.start())
            rt.scheduler._task = task
            # 记录调度器任务异常，避免异常被吞进 asyncio 无日志
            task.add_done_callback(_log_scheduler_exception)
        except Exception as e:
            logger.warning("调度器启动失败: %s", e)
    yield
    if rt.scheduler:
        rt.scheduler.stop()


app = FastAPI(title="Paper Research", version=__version__, lifespan=_app_lifespan)
app.state.runtime = Runtime()

app.include_router(papers.router)
app.include_router(zotero.router)

# 前端静态资源：须在所有 /api/* 路由之后注册，避免 catch-all 遮蔽 JSON API
if APP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(APP_DIR), html=True), name="frontend")


def run_server(settings):
    """启动 FastAPI 服务（注入运行时上下文 + uvicorn，统一日志）。"""
    import uvicorn

    from src.logging_setup import setup_logging
    from src.zotero import ZoteroClient
    from src.zotero.manager import ZoteroImportManager

    setup_logging()

    rt = app.state.runtime
    rt.settings = settings

    zotero = None
    try:
        zotero = ZoteroClient(
            settings.zotero.api_key,
            settings.zotero.library_id,
            settings.zotero.library_type,
        )
        logger.info("Zotero connected")
    except Exception as e:
        logger.warning("Zotero 连接失败: %s（导入功能不可用，审阅页面仍可访问）", e)
        zotero = None

    if zotero:
        rt.zotero_client = zotero
        # on_done：任务完成/失败后经 SSE 推送信号，前端无需高频轮询
        rt.zotero_import = ZoteroImportManager(zotero, on_done=rt.publish_sse)

    server_cfg = settings.server
    logger.info("Server: http://%s:%s", server_cfg.host, server_cfg.port)
    uvicorn.run(
        app,
        host=server_cfg.host,
        port=server_cfg.port,
        log_level="info",
        log_config=None,  # 不让 uvicorn 用 dictConfig 覆盖 root handlers（统一走日志基建）
    )
