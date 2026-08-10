"""命令处理模块：各 CLI 子命令的实现（基于 DB + Zotero）"""

import asyncio
import logging
import socket
import sys
import threading
import time
import webbrowser

logger = logging.getLogger(__name__)

_DASH_WIDTH = 62  # status 仪表盘总宽度

# ─── fetch ────────────────────────────────────────────────


def cmd_fetch(args, settings):
    """抓取 Arxiv 论文 → LLM 评分 → 写入数据库 → 记录抓取日志"""
    from src.config.settings import get_active_keywords, today_str
    from src.db import PaperDB
    from src.pipeline.fetch import run_fetch_pipeline

    today = today_str()  # 显示横幅与入库 fetch_date 同口径（系统本地时间）
    mode = getattr(args, "mode", "incremental")
    print(f"[{today}] === Paper Research Fetch 开始 (模式: {mode}) ===")
    logger.info("fetch 开始: mode=%s keyword=%s", mode, args.keyword or "")

    keywords = get_active_keywords(settings)  # 复用 dispatch 已加载的 settings，避免重复读盘
    if args.keyword:
        keywords = [kw for kw in keywords if kw.keyword == args.keyword]
        if not keywords:
            print(f"  [!] 未找到关键词: {args.keyword}")
            return
    print(f"  活跃关键词: {len(keywords)} 个")

    # 0 = 使用各源配置的 max_results（run_fetch_pipeline 仅 >0 时覆盖）
    max_results = args.max_results

    db = PaperDB()
    asyncio.run(
        run_fetch_pipeline(
            settings, keywords, max_results, db=db, mode=mode, dry_run=bool(args.dry_run)
        )
    )

    if args.dry_run:
        return


# ─── serve ────────────────────────────────────────────────


def _open_browser_when_ready(url: str, host: str, port: int, timeout: float = 30.0) -> None:
    """等端口真正开始监听后再打开浏览器（避免冷启动时浏览器访问到未就绪的服务）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        return
    webbrowser.open(url)


def cmd_serve(_args, settings):
    """启动 FastAPI 本地 Web 服务"""
    # 无控制台环境（pythonw.exe / 后台任务）下把 stdout/stderr 重定向到日志文件
    from src.logging_setup import redirect_stdio_if_detached

    redirect_stdio_if_detached()

    from src.serve import run_server

    server_url = f"http://{settings.server.host}:{settings.server.port}"
    print("  [OK] Starting server...")
    print(f"  [OK] Review: {server_url}/")
    print(f"  [OK] API:   {server_url}/docs")
    logger.info("serve 启动: %s", server_url)

    # 仅交互式终端自动打开浏览器；计划任务等后台启动（无 TTY）不打开
    if sys.stdout.isatty():
        threading.Thread(
            target=_open_browser_when_ready,
            args=(server_url, settings.server.host, settings.server.port),
            daemon=True,
        ).start()
    run_server(settings)


# ─── status ───────────────────────────────────────────────


def cmd_status(_args, settings):
    """显示统计仪表盘"""
    from src.db import PaperDB

    db = PaperDB()
    stats = db.get_stats()
    logs = db.get_recent_logs(limit=5)
    logger.info("status 查询: total=%s pending=%s", stats["total"], stats["pending"])

    W = _DASH_WIDTH
    S = W - 2
    L = W - 4

    def sep(c="-"):
        return "+" + c * S + "+"

    def line(text):
        return "| " + text.ljust(L) + " |"

    lines = [sep(), line("[*] Paper Research Stats".center(L)), sep()]

    # 本地 DB 统计
    lines.append(line(f"  DB 论文总数:         {stats['total']:>5}"))
    lines.append(line(f"  待审阅:              {stats['pending']:>5}"))
    lines.append(sep())

    # 标记分布
    lines.append(line("  Mark Distribution"))
    lines.append(
        line(
            f"    忽略: {stats['by_mark'].get('ignore', 0):>4}   延后: {stats['by_mark'].get('lurk', 0):>4}"
        )
    )
    lines.append(sep())

    # 评级分布
    lines.append(line("  AI Remark Distribution"))
    lines.append(
        line(
            f"    ⭐重要: {stats['by_remark'].get('important', 0):>4}  👍有用: {stats['by_remark'].get('useful', 0):>4}"
        )
    )
    lines.append(
        line(
            f"    📄浏览: {stats['by_remark'].get('browse', 0):>4}  🗑️跳过: {stats['by_remark'].get('skip', 0):>4}"
        )
    )
    lines.append(sep())

    # 关键词分布
    lines.append(line("  Papers by Keyword"))
    for kw, count in stats["by_keyword"].items():
        if kw:
            lines.append(line(f"    {kw}: {count} 篇"))
    lines.append(sep())

    # 最近抓取日志
    lines.append(line("[+] Recent Fetch Logs"))
    lines.append(sep())
    if logs:
        for log in logs:
            icon = "OK" if log.get("status") == "success" else "!!"
            ts = (log.get("run_time", "") or "")[:16]
            lines.append(
                line(
                    f"  {icon} {ts}  Fetched:{log.get('papers_fetched', 0):>3}  New:{log.get('papers_new', 0):>3}"
                )
            )
    else:
        lines.append(line("  (no fetch logs yet)"))
    lines.append(sep())

    for line_text in lines:
        print(line_text)


# ─── notify ───────────────────────────────────────────────


def cmd_notify(args, settings):
    """手动发送邮件通知"""
    from src.db import PaperDB
    from src.notify.report import send_fetch_report

    db = PaperDB()
    result = send_fetch_report(settings, db)
    logger.info("notify 触发: sent=%s reason=%s", result["sent"], result.get("reason"))
