"""命令处理模块：各 CLI 子命令的实现"""

import datetime
import os
import subprocess
import sys
import webbrowser

from src.config import OUTPUT_DIR

# ─── fetch ────────────────────────────────────────────────


def cmd_fetch(args, settings, conn):
    """抓取 Arxiv 论文 -> LLM 摘要 -> 生成 HTML -> 通知

    支持两种抓取模式:
      - incremental（默认）: 获取最近 lookback_days 天内与关键词最相关的
        target_new_per_keyword 篇论文，适合每日定时增量抓取
      - historical: 从 Arxiv 全量数据中按相关性排序抓取指定数量的论文，
        不受时间限制，适合首次建库或补充历史论文
    """
    import asyncio

    from src.config import get_active_keywords
    from src.network.fetch_pipeline import run_fetch_pipeline

    today = datetime.date.today().isoformat()
    mode = getattr(args, "mode", "incremental")
    print(f"[{today}] === Paper Research Fetch 开始 (模式: {mode}) ===")

    # 1. 加载关键词
    keywords = get_active_keywords()
    if args.keyword:
        keywords = [kw for kw in keywords if kw["keyword"] == args.keyword]
        if not keywords:
            print(f"  [!] 未找到关键词: {args.keyword}")
            return
    print(f"  活跃关键词: {len(keywords)} 个")

    if args.dry_run:
        print("  🔍 Dry-run 模式，不实际写入数据库")

    # 优先级：args.max_results > settings.fetch.max_results
    max_results = args.max_results if args.max_results > 0 else settings.fetch.max_results

    # 2. 执行管道（共享逻辑：抓取 → 入库 → 摘要 → HTML → 日志）
    asyncio.run(
        run_fetch_pipeline(
            conn, settings, keywords, max_results, mode=mode, dry_run=bool(args.dry_run)
        )
    )

    if args.dry_run:
        return

    # 3. 提示后续操作
    server_url = f"http://{settings.server.host}:{settings.server.port}"
    print(f"  [i] Run '{server_url}/api/notify' to send notification with URL")
    print("  [i] Or start serve: uv run paper-research serve")
    print(f"[{today}] === Paper Research Fetch 完成 ===")

    # 如果指定了 --serve，自动启动服务
    if args.serve:
        print("  🌐 启动 Web 审阅服务...")
        cmd_serve(args, settings, conn)


# ─── serve ────────────────────────────────────────────────


def cmd_serve(_args, settings, conn):
    """启动 FastAPI 本地 Web 服务"""
    from src.serve.server import run_server

    html_path = OUTPUT_DIR / "summaries" / "index.html"
    if not html_path.exists():
        print("  ⚠️ 尚未生成 summary，先运行 fetch 命令")
        return
    server_url = f"http://{settings.server.host}:{settings.server.port}"
    print("  [OK] Starting server...")
    print(f"  [OK] Summary: {server_url}/")
    print(f"  [OK] Notes:   {server_url}/notes")
    print(f"  [OK] API:     {server_url}/docs")
    print(f"  [OK] Notify:  POST {server_url}/api/notify")
    webbrowser.open(server_url)
    run_server(settings, conn)


# ─── review ───────────────────────────────────────────────


def cmd_review(args, settings, conn):
    """显示待审核论文列表"""

    limit_clause = ""
    if not args.all and args.head:
        limit_clause = f" LIMIT {args.head}"

    cursor = conn.execute(
        """
        SELECT * FROM papers
        WHERE status = 'summarized' AND user_mark IS NULL
        ORDER BY
            CASE llm_remark
                WHEN 'important' THEN 1
                WHEN 'useful' THEN 2
                WHEN 'browse' THEN 3
                WHEN 'skip' THEN 4
                ELSE 5
            END,
            llm_score DESC"""
        + limit_clause
    )

    papers = [dict(row) for row in cursor.fetchall()]

    if not papers:
        print("✅ 所有论文已标记，暂无待审核论文")
        return

    print(f"\n📋 待审核论文 ({len(papers)} 篇)\n")
    print(f"{'#':<4} {'ArXiv ID':<12} {'评级':<10} {'评分':<6} {'标题'}")
    print("-" * 100)

    for i, p in enumerate(papers, 1):
        remark_emoji = {
            "important": "⭐ 重要",
            "useful": "👍值得关注",
            "browse": "📄 可浏览",
            "skip": "🗑️ 待审核",
        }.get(p["llm_remark"], p["llm_remark"])

        title = p["title"][:60] + "..." if len(p["title"]) > 60 else p["title"]
        print(f"{i:<4} {p['arxiv_id']:<12} {remark_emoji:<10} {p['llm_score']:<6.2f} {title}")

    print('\n用法: uv run paper-research mark <arxiv_id> -t [skim|deep-read|ignore]')


# ─── mark ─────────────────────────────────────────────────


def cmd_mark(args, settings, conn):
    """标记论文"""
    import asyncio

    from src.agents import PaperScorer
    from src.agents.note_agent import NoteAgent
    from src.db import get_paper, insert_paper, mark_paper, update_paper_summary
    from src.network.factory import get_source

    paper = get_paper(conn, args.arxiv_id)
    if not paper:
        print(f"  [i] 论文 {args.arxiv_id} 未入库，正在从 Arxiv 抓取...")
        source = get_source(settings)
        papers = asyncio.run(source.fetch_by_ids([args.arxiv_id]))
        if not papers:
            print(f"  ❌ 在 Arxiv 上未找到论文: {args.arxiv_id}")
            return
        p = papers[0]
        p["fetch_date"] = datetime.date.today().isoformat()
        insert_paper(conn, p)
        print(f"  ✅ 已入库: [{p['arxiv_id']}] {p['title'][:80]}")

        # LLM 摘要
        api_key = settings.llm.api_key
        scorer = PaperScorer(
            api_key=api_key,
            api_base=settings.llm.api_base,
            model=settings.llm.model,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        )
        result = scorer.score(p.get("title", ""), p.get("abstract", ""))
        if result:
            update_paper_summary(
                conn,
                p["arxiv_id"],
                result.summary,
                result.remark,
                result.reason,
                result.score,
            )
            print(f"  ✅ LLM 摘要完成: {result.remark} ({result.score:.2f})")

        paper = get_paper(conn, args.arxiv_id)

    # 统一 mark 类型（CLI 用 deep-read，DB 存 deep_read）
    mark_type = args.type.replace("-", "_")

    mark_paper(conn, args.arxiv_id, mark_type)

    label_map = {
        "ignore": "[x] Ignored",
        "skim": "[-] Marked Skim",
        "deep_read": "[*] Marked Deep Read",
        "lurk": "[~] Marked Pending",
    }
    print(f"  {label_map.get(mark_type, '[OK]')}  [{args.arxiv_id}] {paper['title'][:80]}")

    # 粗读或精读 → 自动提取图表 + 生成笔记
    if mark_type in ("skim", "deep_read"):
        print("  [i] Generating note...")
        from src.agents.note_agent import NoteAgent

        asyncio.run(NoteAgent.generate_from_arxiv_id(args.arxiv_id, OUTPUT_DIR, mode=mark_type))

        print(f"  [i] Note generated: output/notes/{args.arxiv_id}/note.md")

        note_path = OUTPUT_DIR / "notes" / args.arxiv_id / "note.md"
        if note_path.exists():
            if sys.platform == "win32":
                os.startfile(str(note_path))
            else:
                subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(note_path)])

    # 标记后刷新静态 HTML snapshot + 笔记画廊
    from src.db import get_papers_for_summary as gfs
    from src.serve.renderer import generate_notes_index_html, generate_summary_html

    grouped = gfs(conn)
    generate_summary_html(grouped, OUTPUT_DIR)
    all_p = list(conn.execute("SELECT * FROM papers ORDER BY fetch_date DESC LIMIT 500"))
    generate_notes_index_html([dict(r) for r in all_p], OUTPUT_DIR)


# ─── note ─────────────────────────────────────────────────


def cmd_note(args, settings, conn):
    """生成或打开阅读笔记

    如果论文未入库，自动从 Arxiv 抓取 → LLM 摘要 → 生成笔记。
    """
    import asyncio

    from src.agents import PaperScorer
    from src.agents.note_agent import NoteAgent
    from src.db import get_paper, insert_paper, mark_paper, update_paper_summary
    from src.network.factory import get_source

    paper = get_paper(conn, args.arxiv_id)
    if not paper:
        print(f"  [i] 论文 {args.arxiv_id} 未入库，正在从 Arxiv 抓取...")
        source = get_source(settings)
        papers = asyncio.run(source.fetch_by_ids([args.arxiv_id]))
        if not papers:
            print(f"  ❌ 在 Arxiv 上未找到论文: {args.arxiv_id}")
            return
        p = papers[0]
        p["fetch_date"] = datetime.date.today().isoformat()
        insert_paper(conn, p)
        print(f"  ✅ 已入库: [{p['arxiv_id']}] {p['title'][:80]}")

        # LLM 摘要
        api_key = settings.llm.api_key
        scorer = PaperScorer(
            api_key=api_key,
            api_base=settings.llm.api_base,
            model=settings.llm.model,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        )
        result = scorer.score(p.get("title", ""), p.get("abstract", ""))
        if result:
            update_paper_summary(
                conn,
                p["arxiv_id"],
                result.summary,
                result.remark,
                result.reason,
                result.score,
            )
            print(f"  ✅ LLM 摘要完成: {result.remark} ({result.score:.2f})")

        mark_paper(conn, args.arxiv_id, "deep_read")
        paper = get_paper(conn, args.arxiv_id)

    note_dir = OUTPUT_DIR / "notes" / args.arxiv_id
    note_path = note_dir / "note.md"

    if not note_path.exists():
        print("  📝 生成阅读笔记...")
        asyncio.run(NoteAgent.generate_from_arxiv_id(args.arxiv_id, OUTPUT_DIR))

    # 标记后刷新静态 HTML snapshot + 笔记画廊
    from src.db import get_papers_for_summary as gfs
    from src.serve.renderer import generate_notes_index_html, generate_summary_html

    grouped = gfs(conn)
    generate_summary_html(grouped, OUTPUT_DIR)
    all_p = list(conn.execute("SELECT * FROM papers ORDER BY fetch_date DESC LIMIT 500"))
    generate_notes_index_html([dict(r) for r in all_p], OUTPUT_DIR)

    print(f"  📂 打开笔记: {note_path}")
    if sys.platform == "win32":
        os.startfile(str(note_path))
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(note_path)])


# ─── list ─────────────────────────────────────────────────


def cmd_list(args, settings, conn):
    """列出论文（SQL 级分页 + 独立 COUNT）"""
    # 构建 WHERE 子句
    where_parts = []
    params = []
    label = "全部"

    if args.mark:
        where_parts.append("user_mark = ?")
        params.append(args.mark)
        label = {"ignore": "已忽略", "skim": "已粗读", "deep_read": "已精读"}.get(
            args.mark, args.mark
        )
    elif args.keyword:
        where_parts.append("keyword_match LIKE ?")
        params.append(f"%{args.keyword}%")
        label = f"关键词: {args.keyword}"
    elif args.status:
        where_parts.append("status = ?")
        params.append(args.status)
        label = f"状态: {args.status}"

    where = ""
    if where_parts:
        where = "WHERE " + " AND ".join(where_parts)

    # 总记录数
    total = conn.execute(f"SELECT COUNT(*) FROM papers {where}", params).fetchone()[0]

    # 排序
    sort_map = {
        "date": "published DESC",
        "rdate": "published ASC",
        "score": "llm_score DESC",
        "rscore": "llm_score ASC",
    }
    order_by = sort_map.get(args.sort, "published DESC")

    # 数据查询（SQL 级 LIMIT）
    limit_clause = ""
    if not args.all and args.head:
        limit_clause = f" LIMIT {args.head}"

    cursor = conn.execute(
        f"SELECT * FROM papers {where} ORDER BY {order_by}{limit_clause}",
        params,
    )
    papers = [dict(r) for r in cursor.fetchall()]

    print(f"\n📋 {label} (共 {total} 篇, 显示 {len(papers)} 篇)\n")

    if not papers:
        print("  (空)")
        return

    remark_map = {
        "important": "⭐",
        "useful": "👍",
        "browse": "📄",
        "skip": "🗑️",
    }

    mark_icons = {
        "ignore": "忽略",
        "lurk": "延后处理",
        "skim": "粗读",
        "deep_read": "精读",
    }

    for i, p in enumerate(papers, 1):
        remark_icon = remark_map.get(p["llm_remark"], "??")
        user_mark_str = f" {mark_icons.get(p['user_mark'], '?')}" if p["user_mark"] else "待处理"
        updated_str = " (UPD)" if p["is_updated"] else ""

        title = p["title"][:70] + "..." if len(p["title"]) > 70 else p["title"]
        print(f"{i:>3}. {remark_icon} [{p['arxiv_id']}] {title} {user_mark_str} {updated_str}")
        print(
            f"     日期: {p['published']}  |  评分: {p['llm_score']:.2f}  |  原因: {p.get('llm_reason', '')[:60]}\n"
        )


# ─── status ───────────────────────────────────────────────


def cmd_status(args, settings, conn):
    """显示统计仪表盘"""
    from src.db import get_pending_keywords, get_recent_logs, get_stats

    stats = get_stats(conn)
    kws = get_pending_keywords(conn)
    logs = get_recent_logs(conn, limit=5)

    W = 62  # 表格总宽度（含边框）
    S = W - 2  # sep: 边框 `+` 之间的宽度
    L = W - 4  # line: 内容 `|` 之间的宽度

    def sep(c="-"):
        return "+" + c * S + "+"

    def line(*parts):
        return "| " + "".join(parts).ljust(L) + " |"

    def center(text):
        return line(text.center(L))

    def left(text):
        return line(text)

    lines = [sep(), center("[*] Paper Research Stats"), sep()]

    # 统计概览
    lines.append(left(f"  Total papers:             {stats['total']:>5}"))
    lines.append(left(f"  Awaiting review:          {stats['summarized_pending']:>5}"))
    lines.append(left(f"  Version updates:          {stats['updated']:>5}"))
    lines.append(sep())

    # AI 评级分布
    lines.append(left("  AI Remark Distribution"))
    lines.append(
        left(f"    ** Important: {stats['important']:>4}      !! Useful: {stats['useful']:>4}")
    )
    lines.append(left(f"    .. Browse:   {stats['browse']:>4}      -- Skip:   {stats['skip']:>4}"))
    lines.append(sep())

    # 关键词待审
    lines.append(center("[-] Pending By Keyword"))
    lines.append(sep())
    if kws:
        lines.append(left(f"  {'Keyword':<20} {'Count':>4}"))
        for kw in kws:
            lines.append(left(f"  {kw['keyword']:<20} {kw['count']:>4}"))
    else:
        lines.append(left("  (none)"))
    lines.append(sep())

    # 最近抓取日志
    lines.append(center("[+] Recent Fetch Logs"))
    lines.append(sep())
    if logs:
        for log in logs:
            icon = (
                "OK" if log["status"] == "success" else "!!" if log["status"] == "partial" else "XX"
            )
            lines.append(
                left(
                    f"  {icon} {log['run_time'][:16]}  "
                    f"| Fet:{log['papers_fetched']:>3}  "
                    f"Upd:{log['papers_updated']:>3}  "
                    f"Sum:{log['papers_summarized']:>3}"
                )
            )
    else:
        lines.append(left("  (no fetch logs yet)"))
    lines.append(sep())

    for l in lines:
        print(l)


# ─── notify ───────────────────────────────────────────────


def cmd_notify(args, settings, conn):
    """手动发送通知"""
    from src.db import get_pending_keywords, get_stats
    from src.notify import send_email_if_configured, send_windows_toast

    stats = get_stats(conn)
    server_url = f"http://{settings.server.host}:{settings.server.port}"
    important_count = stats.get("important", 0)
    pending = stats.get("summarized_pending", 0)
    keywords = get_pending_keywords(conn)

    send_windows_toast(
        "Arxiv Paper Research",
        f"Important: {important_count}, Pending: {pending}",
        keywords=keywords,
        url=server_url,
    )
    kw_str = ", ".join(f"{k['keyword']}({k['count']})" for k in keywords)
    print(f"  [OK] Toast sent: Important={important_count}, Pending={pending}, Keywords=[{kw_str}]")

    send_email_if_configured(settings, stats, server_url, keywords=keywords)
