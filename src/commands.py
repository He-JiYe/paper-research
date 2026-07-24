"""命令处理模块：各 CLI 子命令的实现（基于 DB + Zotero）"""

import asyncio
import datetime
import socket
import sys
import threading
import time
import webbrowser

# ─── fetch ────────────────────────────────────────────────


def cmd_fetch(args, settings):
    """抓取 Arxiv 论文 → LLM 评分 → 写入数据库 → 生成 HTML"""
    from src.config import get_active_keywords
    from src.db import PaperDB
    from src.network.fetch_pipeline import run_fetch_pipeline

    today = datetime.date.today().isoformat()
    mode = getattr(args, "mode", "incremental")
    print(f"[{today}] === Paper Research Fetch 开始 (模式: {mode}) ===")

    keywords = get_active_keywords()
    if args.keyword:
        keywords = [kw for kw in keywords if kw.keyword == args.keyword]
        if not keywords:
            print(f"  [!] 未找到关键词: {args.keyword}")
            return
    print(f"  活跃关键词: {len(keywords)} 个")

    max_results = args.max_results if args.max_results > 0 else settings.fetch.max_results

    db = PaperDB()
    asyncio.run(
        run_fetch_pipeline(
            settings, keywords, max_results, db=db, mode=mode, dry_run=bool(args.dry_run)
        )
    )

    if args.dry_run:
        return

    server_url = f"http://{settings.server.host}:{settings.server.port}"
    print(f"  [i] 启动服务: {server_url}")
    print(f"  [i] 查看待审阅论文: {server_url}/")


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


def _redirect_stdio_if_detached() -> None:
    """无控制台环境（pythonw.exe / 后台任务）下把 stdout/stderr 重定向到日志文件。

    pythonw.exe 中 sys.stdout/sys.stderr 为 None，直接 print 会抛 AttributeError；
    重定向后也保证后台运行时日志可追溯。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    from src.config import OUTPUT_DIR

    log_dir = OUTPUT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # 该文件句柄需存活整个进程生命周期，不能用 with 管理
    log_file = log_dir / "serve-stdout.log"
    try:
        fh = open(log_file, "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    except OSError:
        # 日志被其他进程占用（如旧实例未退净）时退回按 PID 命名，避免启动即崩
        import os

        fh = open(log_dir / f"serve-stdout-{os.getpid()}.log", "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh


def cmd_serve(_args, settings):
    """启动 FastAPI 本地 Web 服务"""
    _redirect_stdio_if_detached()

    from src.serve.server import run_server

    server_url = f"http://{settings.server.host}:{settings.server.port}"
    print("  [OK] Starting server...")
    print(f"  [OK] Review: {server_url}/")
    print(f"  [OK] API:   {server_url}/docs")

    # 仅交互式终端自动打开浏览器；计划任务等后台启动（无 TTY）不打开
    if sys.stdout.isatty():
        threading.Thread(
            target=_open_browser_when_ready,
            args=(server_url, settings.server.host, settings.server.port),
            daemon=True,
        ).start()
    run_server(settings)


# ─── status ───────────────────────────────────────────────


def cmd_status(args, settings):
    """显示统计仪表盘"""
    from src.db import PaperDB
    from src.zotero import ZoteroClient

    db = PaperDB()
    stats = db.get_stats()
    logs = db.get_recent_logs(limit=5)

    try:
        zotero = ZoteroClient(
            settings.zotero.api_key,
            settings.zotero.library_id,
            settings.zotero.library_type,
        )
        zotero_stats = zotero.get_stats()
    except Exception as e:
        print(f"  ⚠️ 连接 Zotero 失败: {e}")
        zotero_stats = {}

    W = 62
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
    lines.append(line(f"    忽略: {stats['by_mark'].get('ignore', 0):>4}   延后: {stats['by_mark'].get('lurk', 0):>4}"))
    lines.append(sep())

    # 评级分布
    lines.append(line("  AI Remark Distribution"))
    lines.append(line(f"    ⭐重要: {stats['by_remark'].get('important', 0):>4}  👍有用: {stats['by_remark'].get('useful', 0):>4}"))
    lines.append(line(f"    📄浏览: {stats['by_remark'].get('browse', 0):>4}  🗑️跳过: {stats['by_remark'].get('skip', 0):>4}"))
    lines.append(sep())

    # 关键词分布
    lines.append(line("  Papers by Keyword"))
    for kw, count in stats["by_keyword"].items():
        if kw:
            lines.append(line(f"    {kw}: {count} 篇"))
    lines.append(sep())

    # Zotero 统计
    if zotero_stats:
        lines.append(line(f"  Zotero 论文总数:     {zotero_stats.get('total', 0):>5}"))
        lines.append(line(f"  已审阅:              {zotero_stats.get('reviewed', 0):>5}"))
        lines.append(sep())

    # 最近抓取日志
    lines.append(line("[+] Recent Fetch Logs"))
    lines.append(sep())
    if logs:
        for log in logs:
            icon = "OK" if log.get("status") == "success" else "!!"
            ts = (log.get("run_time", "") or "")[:16]
            lines.append(
                line(f"  {icon} {ts}  Fetched:{log.get('papers_fetched', 0):>3}  New:{log.get('papers_new', 0):>3}")
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
    from src.notify import EmailNotifier

    db = PaperDB()
    pending = db.get_pending()

    notifier = EmailNotifier(settings.notification)
    notifier.send_fetch_report(
        {"new": len(pending)},
        pending,
        [kw.keyword for kw in settings.keywords],
    )
