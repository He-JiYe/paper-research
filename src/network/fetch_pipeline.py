"""共享的抓取管道：供 CLI (cmd_fetch) 和 Web API (_do_fetch) 复用

通过 DataSource 工厂与具体数据源解耦 —— settings.source 决定使用哪个后端。
"""

import asyncio
import datetime


async def run_fetch_pipeline(
    conn, settings, keywords, max_results, mode="incremental", dry_run=False
):
    """核心抓取管道

    执行顺序: 构建 skip_ids → 抓取 → 去重+版本检测 → 入库
              → LLM 摘要 → 生成 HTML → 记日志

    Args:
        conn:         数据库连接
        settings:     配置对象（含 source, fetch, llm 子配置）
        keywords:     关键词列表 [{"keyword": ..., "arxiv_cats": ..., "active": ...}, ...]
        max_results:  每关键词最大结果数
        mode:         "incremental"（增量）| "historical"（全量）
        dry_run:      True 时只抓取不写入

    Returns:
        dict: fetched / new / updated / summarized 计数及论文列表
    """
    # 函数内导入以便测试能 patch 依赖
    from src.agents import PaperScorer
    from src.config import OUTPUT_DIR
    from src.db import (
        get_paper,
        insert_fetch_log,
        insert_paper,
        touch_paper,
        update_paper_summary,
        update_paper_version,
    )
    from src.network.factory import get_source

    today = datetime.date.today().isoformat()
    is_historical = mode == "historical"

    # ── 1. 通过工厂获取数据源 ──────────────────────────────
    source = get_source(settings)

    # ── 2. 构建 skip_ids ──────────────────────────────────
    skip_ids = set()
    for kw in keywords:
        cursor = conn.execute(
            "SELECT arxiv_id FROM papers WHERE keyword_match LIKE ?",
            (f"%{kw['keyword']}%",),
        )
        existing_ids = {row[0] for row in cursor.fetchall()}
        skip_ids |= existing_ids
        print(f"    [{kw['keyword']}]: 已有 {len(existing_ids)} 篇")

    # ── 3. 抓取 ───────────────────────────────────────────
    print(f"  📡 开始抓取 ({mode}模式，跳过 {len(skip_ids)} 篇已有)...")
    all_papers, _, _ = await source.fetch_all(
        keywords,
        settings,
        max_results=max_results,
        historical=is_historical,
        skip_ids=skip_ids,
    )

    # dry-run：只抓取不写入
    if dry_run:
        print(f"  📄 共获取 {len(all_papers)} 篇论文")
        for p in all_papers[:10]:
            print(f"    - [{p['arxiv_id']}] {p['title'][:80]}")
        if len(all_papers) > 10:
            print(f"    ... 还有 {len(all_papers) - 10} 篇")
        return {
            "fetched": len(all_papers),
            "new": 0,
            "updated": 0,
            "summarized": 0,
            "papers_fetched": all_papers,
            "papers_new": [],
            "papers_updated": [],
        }

    # ── 4. 去重 + 版本检测 ────────────────────────────────
    new_papers = []
    updated_papers = []
    for paper in all_papers:
        aid = paper["arxiv_id"]
        existing = get_paper(conn, aid)

        if existing is None:
            paper["fetch_date"] = today
            insert_paper(conn, paper)
            new_papers.append(paper)
        elif (
            existing["arxiv_updated"]
            and paper.get("arxiv_updated", "") > existing["arxiv_updated"]
        ):
            update_paper_version(
                conn,
                aid,
                paper.get("version", 1),
                paper["arxiv_updated"],
                today,
            )
            updated_papers.append(paper)
        else:
            touch_paper(conn, aid, today)

    print(
        f"  📊 抓取: {len(all_papers)} 篇 | 新增: {len(new_papers)} 篇 | 更新: {len(updated_papers)} 篇"
    )

    # ── 5. LLM 摘要 ───────────────────────────────────────
    to_summarize = new_papers + updated_papers
    if to_summarize:
        print(f"  [*] Summarizing {len(to_summarize)} papers...")
        api_key = settings.llm.api_key
        scorer = PaperScorer(
            api_key=api_key,
            api_base=settings.llm.api_base,
            model=settings.llm.model,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        )

        results: list = []
        if api_key:
            # 异步批量评分，失败则回退到串行
            try:
                results = await scorer.score_batch_async(to_summarize)
            except Exception as e:
                print(f"  [!] Async summary failed, falling back to serial: {e}")
                try:
                    results = scorer.score_batch(to_summarize)
                except Exception as e2:
                    print(f"  [!] Sync summary also failed: {e2}")
        else:
            # 无 API key：逐篇串行评分，每篇独立容错
            loop = asyncio.get_running_loop()
            results = []
            for paper in to_summarize:
                try:
                    result = await loop.run_in_executor(
                        None,
                        scorer.score,
                        paper.get("title", ""),
                        paper.get("abstract", ""),
                        paper.get("categories", ""),
                        paper.get("keyword_match", ""),
                    )
                except Exception as e:
                    print(f"    [!] Fallback failed [{paper['arxiv_id']}]: {e}")
                    result = None
                results.append(result)

        count = 0
        for paper, result in zip(to_summarize, results, strict=False):
            if result:
                update_paper_summary(
                    conn,
                    paper["arxiv_id"],
                    result.summary,
                    result.remark,
                    result.reason,
                    result.score,
                )
                count += 1
        print(f"  [OK] Summary done: {count}/{len(to_summarize)}")
    else:
        print("  [.] No new papers to summarize")

    # ── 6. 生成 HTML ──────────────────────────────────────
    print("  [i] 生成 HTML...")
    from src.db import get_papers_for_summary as gfs
    from src.serve.renderer import generate_notes_index_html, generate_summary_html

    grouped = gfs(conn)
    generate_summary_html(grouped, OUTPUT_DIR)
    all_p = list(conn.execute("SELECT * FROM papers ORDER BY fetch_date DESC LIMIT 500"))
    generate_notes_index_html([dict(r) for r in all_p], OUTPUT_DIR)

    # ── 7. 记录日志 ──────────────────────────────────────
    insert_fetch_log(
        conn,
        keywords_used=len(keywords),
        papers_fetched=len(all_papers),
        papers_new=len(new_papers),
        papers_updated=len(updated_papers),
        papers_summarized=len(to_summarize),
        status="success",
    )

    return {
        "fetched": len(all_papers),
        "new": len(new_papers),
        "updated": len(updated_papers),
        "summarized": len(to_summarize),
        "papers_fetched": all_papers,
        "papers_new": new_papers,
        "papers_updated": updated_papers,
    }
